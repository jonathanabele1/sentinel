"""GitHub webhook receiver.

Validates HMAC-SHA256 signatures, dispatches by event type, creates a
ReviewRun row, executes the default review plan via the orchestrator
Engine, then posts a summary comment to the PR.

Week 3 changes:
  - The default plan is now constructed per request via build_default_plan
    so it can wire in the LLM and GitHub-diff clients from dependencies.
  - The ReviewRun row carries installation_id so steps can talk back to
    the GitHub Apps API later in the run.
  - The summary comment includes the LLM-generated diff analysis when
    the analyze_diff step completed successfully.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from packages.core.agents.diff_analyzer import DiffAnalysis
from packages.core.github.app import GitHubAppClient
from packages.core.github.diff import GitHubDiffClient
from packages.core.github.signature import verify_signature
from packages.core.llm import LLMClient
from packages.core.models.db import ReviewRun, StepExecution
from packages.core.models.session import get_session
from packages.core.observability.logging import get_logger
from packages.core.observability.metrics import (
    review_runs_total,
    webhooks_received_total,
)
from packages.core.orchestrator import Engine, StepFailedError, build_default_plan
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, get_settings
from apps.api.deps import (
    get_engine,
    get_github_client,
    get_github_diff_client,
    get_llm_client,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger(__name__)


SUPPORTED_PR_ACTIONS = {"opened", "synchronize", "reopened"}


@router.post("/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    engine: Annotated[Engine, Depends(get_engine)],
    client: Annotated[GitHubAppClient, Depends(get_github_client)],
    diff_client: Annotated[GitHubDiffClient, Depends(get_github_diff_client)],
    llm_client: Annotated[LLMClient, Depends(get_llm_client)],
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    body = await request.body()

    if not verify_signature(body, x_hub_signature_256, settings.github_webhook_secret):
        webhooks_received_total.labels(
            event=x_github_event or "unknown", result="bad_signature"
        ).inc()
        log.warning(
            "webhook.bad_signature",
            gh_event=x_github_event,
            delivery=x_github_delivery,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad signature")

    payload = await request.json()
    event = x_github_event or "unknown"

    if event == "ping":
        webhooks_received_total.labels(event=event, result="accepted").inc()
        return {"status": "pong"}

    if event != "pull_request":
        webhooks_received_total.labels(event=event, result="ignored").inc()
        return {"status": "ignored", "event": event}

    action = payload.get("action")
    if action not in SUPPORTED_PR_ACTIONS:
        webhooks_received_total.labels(event=event, result="ignored").inc()
        return {"status": "ignored", "action": action}

    pr = payload["pull_request"]
    repo = payload["repository"]
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")

    # Build the plan with this request's shared client dependencies.
    plan = build_default_plan(diff_client=diff_client, llm_client=llm_client)

    # Create the ReviewRun row. installation_id is persisted so steps can
    # mint GitHub installation tokens after the webhook has returned.
    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url=pr["html_url"],
        repo_full_name=repo["full_name"],
        pr_number=pr["number"],
        head_sha=pr["head"]["sha"],
        installation_id=installation_id,
        plan_name=plan.name,
        status="pending",
        request_id=getattr(request.state, "request_id", None),
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()

    # Run the plan. The engine handles snapshotting, retries, timeouts,
    # spans, and DB updates. On failure it marks the run "failed" and
    # raises StepFailedError; we catch and log so the webhook still ack's.
    try:
        await engine.run(plan, run, session)
        review_runs_total.labels(status="completed").inc()
    except StepFailedError as exc:
        review_runs_total.labels(status="failed").inc()
        log.warning(
            "plan.failed",
            run_id=str(run.id),
            step=exc.step_name,
            error=str(exc.last_error),
        )

    webhooks_received_total.labels(event=event, result="accepted").inc()

    # Pull the diff analysis (if it completed) for the summary comment.
    analysis = await _load_diff_analysis(session, run.id)

    # Post a summary comment. Best-effort: failures get logged, the
    # webhook still acks.
    if installation_id and settings.github_app_id:
        body_md = _summary_comment(run, analysis)
        try:
            await client.post_issue_comment(
                installation_id=installation_id,
                repo_full_name=repo["full_name"],
                issue_number=pr["number"],
                body=body_md,
            )
        except Exception as exc:
            log.warning(
                "webhook.comment_failed",
                run_id=str(run.id),
                error=str(exc),
            )

    log.info(
        "webhook.processed",
        gh_event=event,
        action=action,
        run_id=str(run.id),
        repo=repo["full_name"],
        pr=pr["number"],
        plan_status=run.status,
    )

    return {"status": "accepted", "run_id": str(run.id), "plan_status": run.status}


async def _load_diff_analysis(session: AsyncSession, run_id: uuid.UUID) -> DiffAnalysis | None:
    """Read the analyze_diff step's snapshotted output, if it succeeded."""
    stmt = select(StepExecution).where(
        StepExecution.run_id == run_id,
        StepExecution.step_name == "analyze_diff",
        StepExecution.status == "completed",
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    try:
        return DiffAnalysis.model_validate(row.outputs)
    except Exception as exc:
        # Broad except is intentional: a row from an older schema version
        # shouldn't crash the webhook; we just degrade to a bare comment.
        log.warning(
            "webhook.analysis_parse_failed",
            run_id=str(run_id),
            error=str(exc),
        )
        return None


def _summary_comment(run: ReviewRun, analysis: DiffAnalysis | None) -> str:
    """Format a Markdown comment summarising the run.

    If the analyzer step succeeded we include its summary + risk hints.
    Otherwise we fall back to the bare run-status report.
    """
    lines = [
        f"**Sentinel** ran the `{run.plan_name}` plan.",
        "",
        f"- **Status:** `{run.status}`",
        f"- **Run ID:** `{run.id}`",
    ]
    if run.error:
        lines.extend(["", f"**Error:** {run.error}"])

    if analysis is not None:
        lines.extend(["", "---", "", "### Diff analysis", "", analysis.summary])
        if analysis.categories:
            lines.append("")
            lines.append("**Categories:** " + ", ".join(f"`{c}`" for c in analysis.categories))
        if analysis.risk_hints:
            lines.extend(["", "**Risk hints:**"])
            lines.extend(f"- {hint}" for hint in analysis.risk_hints)
        if analysis.primary_language:
            lines.extend(["", f"**Primary language:** {analysis.primary_language}"])

    lines.extend(
        [
            "",
            (
                "_Week 3: diff analysis online. Specialist reviewers "
                "(security/correctness/testing) land in Week 4._"
            ),
        ]
    )
    return "\n".join(lines)

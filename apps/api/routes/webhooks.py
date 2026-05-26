"""GitHub webhook receiver.

Validates HMAC-SHA256 signatures, dispatches by event type, creates a
ReviewRun row, executes the default review plan via the orchestrator
Engine, then posts a summary comment to the PR.

Week 2 changes: the placeholder comment is gone. Instead, the engine runs
the (single-step) default plan, snapshots every step's inputs/outputs to
step_executions, and the handler posts a summary mentioning the run ID
and status.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from packages.core.github.app import GitHubAppClient
from packages.core.github.signature import verify_signature
from packages.core.models.db import ReviewRun
from packages.core.models.session import get_session
from packages.core.observability.logging import get_logger
from packages.core.observability.metrics import (
    review_runs_total,
    webhooks_received_total,
)
from packages.core.orchestrator import Engine, StepFailedError, default_review_plan
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, get_settings
from apps.api.deps import get_engine, get_github_client

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

    # Create the ReviewRun row. Status starts "pending"; the engine
    # transitions it to "running" then to "completed" or "failed".
    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url=pr["html_url"],
        repo_full_name=repo["full_name"],
        pr_number=pr["number"],
        head_sha=pr["head"]["sha"],
        plan_name=default_review_plan.name,
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
        await engine.run(default_review_plan, run, session)
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

    # Post a summary comment to the PR. The comment is best-effort: if it
    # fails, log a warning but don't fail the webhook.
    installation_id = installation.get("id")
    if installation_id and settings.github_app_id:
        body_md = _summary_comment(run)
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


def _summary_comment(run: ReviewRun) -> str:
    """Format a brief Markdown comment summarising the review run."""
    lines = [
        f"**Sentinel** ran the `{run.plan_name}` plan.",
        "",
        f"- **Status:** `{run.status}`",
        f"- **Run ID:** `{run.id}`",
    ]
    if run.error:
        lines.extend(["", f"**Error:** {run.error}"])
    lines.extend(
        [
            "",
            (
                "_Week 2: orchestrator wired up. The real review pipeline "
                "lands in Week 3 (diff analyzer) and Week 4 "
                "(specialist reviewers)._"
            ),
        ]
    )
    return "\n".join(lines)

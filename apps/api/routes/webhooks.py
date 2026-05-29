"""GitHub webhook receiver.

Validates HMAC-SHA256 signatures, dispatches by event type, creates a
ReviewRun row, executes the default review plan via the orchestrator
Engine, then posts a PR review (with inline comments) summarising what
Sentinel found.

Review posting:
  - The plan ends with a consolidator that produces ConsolidatedFindings.
  - The handler reads the consolidated findings and posts inline review
    comments via post_pr_review (one API call, one notification email).
  - The summary body is a roll-up: counts by severity, plus the upstream
    diff analysis if available.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from packages.core.agents.consolidator import ConsolidatedFindings
from packages.core.github.app import GitHubAppClient
from packages.core.github.diff import GitHubDiffClient
from packages.core.github.signature import verify_signature
from packages.core.llm import LLMClient
from packages.core.models.db import ReviewRun, StepExecution
from packages.core.models.domain import DiffAnalysis, ReviewFinding
from packages.core.models.session import get_session
from packages.core.observability.logging import get_logger
from packages.core.observability.metrics import (
    review_runs_total,
    webhooks_received_total,
)
from packages.core.orchestrator import Engine, StepFailedError
from packages.core.orchestrator.plans import build_default_plan
from packages.core.policy import RepoPolicy, load_repo_policy
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

    # Load per-repo policy from .sentinel.yml (defaults if absent/malformed).
    policy = await _load_policy(
        client, installation_id, repo["full_name"], repo.get("default_branch")
    )
    plan = build_default_plan(diff_client=diff_client, llm_client=llm_client, policy=policy)

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

    # Pull the analyzer + consolidator outputs to build the review.
    analysis = await _load_diff_analysis(session, run.id)
    consolidated = await _load_consolidated_findings(session, run.id)

    if installation_id and settings.github_app_id:
        await _post_review(
            client=client,
            installation_id=installation_id,
            repo_full_name=repo["full_name"],
            pr_number=pr["number"],
            commit_sha=run.head_sha,
            run=run,
            analysis=analysis,
            consolidated=consolidated,
        )

    log.info(
        "webhook.processed",
        gh_event=event,
        action=action,
        run_id=str(run.id),
        repo=repo["full_name"],
        pr=pr["number"],
        plan_status=run.status,
        findings_posted=consolidated.posted_count if consolidated else 0,
    )

    return {
        "status": "accepted",
        "run_id": str(run.id),
        "plan_status": run.status,
        "findings_posted": consolidated.posted_count if consolidated else 0,
    }


# ----- Loaders -----


async def _load_policy(
    client: GitHubAppClient,
    installation_id: int | None,
    repo_full_name: str,
    default_branch: str | None,
) -> RepoPolicy:
    """Fetch and parse .sentinel.yml from the repo's default branch.

    Returns defaults if we can't authenticate, the file is absent, or it's
    malformed. Policy loading must never block a review.
    """
    if not installation_id:
        return RepoPolicy()
    try:
        yaml_text = await client.get_file_contents(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            path=".sentinel.yml",
            ref=default_branch,
        )
    except Exception as exc:
        log.warning("policy.fetch_failed", repo=repo_full_name, error=str(exc))
        return RepoPolicy()
    return load_repo_policy(yaml_text)


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
        log.warning(
            "webhook.analysis_parse_failed",
            run_id=str(run_id),
            error=str(exc),
        )
        return None


async def _load_consolidated_findings(
    session: AsyncSession, run_id: uuid.UUID
) -> ConsolidatedFindings | None:
    """Read the consolidate step's snapshotted output, if it succeeded."""
    stmt = select(StepExecution).where(
        StepExecution.run_id == run_id,
        StepExecution.step_name == "consolidate",
        StepExecution.status == "completed",
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    try:
        return ConsolidatedFindings.model_validate(row.outputs)
    except Exception as exc:
        log.warning(
            "webhook.consolidated_parse_failed",
            run_id=str(run_id),
            error=str(exc),
        )
        return None


# ----- Posting the PR review -----


async def _post_review(
    *,
    client: GitHubAppClient,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    commit_sha: str,
    run: ReviewRun,
    analysis: DiffAnalysis | None,
    consolidated: ConsolidatedFindings | None,
) -> None:
    """Build the review body + inline comments and POST as one review.

    Best-effort: any GitHub API failure is logged but doesn't fail the
    webhook. We've already persisted the audit trail; the comment is
    secondary signal.
    """
    body_md = _summary_comment(run, analysis, consolidated)
    inline_comments = _inline_comments_from_findings(consolidated)

    try:
        await client.post_pr_review(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            commit_sha=commit_sha,
            body=body_md,
            comments=inline_comments,
        )
    except Exception as exc:
        log.warning(
            "webhook.review_post_failed",
            run_id=str(run.id),
            inline_count=len(inline_comments),
            error=str(exc),
        )


def _inline_comments_from_findings(
    consolidated: ConsolidatedFindings | None,
) -> list[dict[str, Any]]:
    """Translate posted findings into GitHub's review-comment shape.

    Each comment dict is one of:
      {"path": ..., "line": N, "body": "..."}              # single line
      {"path": ..., "start_line": N1, "line": N2, "body":..} # range

    Only findings flagged `posted=True` by the consolidator are included.
    """
    if consolidated is None:
        return []

    comments: list[dict[str, Any]] = []
    for finding in consolidated.findings:
        if not finding.posted:
            continue
        comment: dict[str, Any] = {
            "path": finding.file,
            "line": finding.line_end,
            "body": _format_inline_body(finding),
        }
        if finding.line_start < finding.line_end:
            comment["start_line"] = finding.line_start
        comments.append(comment)
    return comments


def _format_inline_body(finding: ReviewFinding) -> str:
    """Markdown body for a single inline comment."""
    badge = f"**[{finding.reviewer}]** `{finding.category}` · `{finding.severity}`"
    confidence = f"_confidence: {finding.confidence:.2f}_"
    return f"{badge} · {confidence}\n\n{finding.message}\n\n> {finding.evidence}"


# ----- The summary comment -----


def _summary_comment(
    run: ReviewRun,
    analysis: DiffAnalysis | None,
    consolidated: ConsolidatedFindings | None,
) -> str:
    """Build the top-level PR review body.

    Order of sections (each conditional on data being available):
      1. Run header (plan name, status, run ID).
      2. Diff analysis (summary + categories + risk hints).
      3. Findings roll-up (counts by severity, posted vs total).
      4. Error message if the run failed.
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

    if consolidated is not None:
        lines.extend(["", "---", "", "### Findings"])
        by_severity = _counts_by_severity(consolidated.findings)
        if by_severity:
            badges = ", ".join(f"**{sev}**: {n}" for sev, n in by_severity.items())
            lines.extend(["", f"Total: {len(consolidated.findings)} ({badges})"])
        lines.append(f"Posted as inline comments: **{consolidated.posted_count}**")
        if consolidated.posted_count == 0 and len(consolidated.findings) > 0:
            lines.extend(
                [
                    "",
                    "_All findings were below the posting threshold. "
                    "Inspect the run row in DBeaver to see the full set._",
                ]
            )
        elif len(consolidated.findings) == 0:
            lines.extend(["", "_No findings. Nice clean PR._"])

    lines.extend(
        [
            "",
            (
                "_Sentinel: specialist reviewers (security, correctness, "
                "testing) with a deterministic consolidator._"
            ),
        ]
    )
    return "\n".join(lines)


def _counts_by_severity(findings: list[ReviewFinding]) -> dict[str, int]:
    """Severity -> count, in canonical severity order."""
    order = ["critical", "high", "medium", "low", "info"]
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    # Reorder so the summary line reads critical → info.
    return {sev: counts[sev] for sev in order if sev in counts}

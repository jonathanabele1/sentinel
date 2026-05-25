"""GitHub webhook receiver.

Validates HMAC-SHA256 signatures, dispatches by event type, creates a
ReviewRun row, and posts a placeholder comment via the GitHub Apps API.

Week 1 scope: the route only proves the end-to-end pipe works. Real review
orchestration arrives in Week 2.
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
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, get_settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger(__name__)


SUPPORTED_PR_ACTIONS = {"opened", "synchronize", "reopened"}


_client: GitHubAppClient | None = None


def get_github_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitHubAppClient:
    """Lazy singleton. Real impl will be wired through app state in Week 2."""
    global _client
    if _client is None:
        _client = GitHubAppClient(
            app_id=settings.github_app_id,
            private_key_path=settings.github_app_private_key_path,
        )
    return _client


@router.post("/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
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

    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url=pr["html_url"],
        repo_full_name=repo["full_name"],
        pr_number=pr["number"],
        head_sha=pr["head"]["sha"],
        plan_name="placeholder",
        status="completed",
        request_id=getattr(request.state, "request_id", None),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()

    review_runs_total.labels(status="completed").inc()
    webhooks_received_total.labels(event=event, result="accepted").inc()

    installation_id = installation.get("id")
    if installation_id and settings.github_app_id:
        try:
            await client.post_issue_comment(
                installation_id=installation_id,
                repo_full_name=repo["full_name"],
                issue_number=pr["number"],
                body=("Sentinel received this PR. Review pipeline coming online in Week 2."),
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
    )

    return {"status": "accepted", "run_id": str(run.id)}

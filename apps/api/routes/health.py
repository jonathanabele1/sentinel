"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from packages.core.observability.metrics import registry
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 as long as the process is up."""
    return {"status": "ok"}


@router.get("/ready", status_code=status.HTTP_200_OK)
async def ready() -> dict[str, str]:
    """Readiness probe. Real implementation will check DB and Redis."""
    return {"status": "ready"}


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape endpoint."""
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

"""Smoke tests for the health endpoint via FastAPI's test client."""

from __future__ import annotations

from apps.api.routes import health
from fastapi.testclient import TestClient


def test_health_returns_ok() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(health.router)
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics_endpoint_serves_prometheus_format() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(health.router)
    client = TestClient(app)

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "sentinel_webhooks_received_total" in response.text

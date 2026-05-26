"""Sentinel API entrypoint.

Wires together: settings, structured logging, OpenTelemetry, request-ID middleware,
and the route modules. Webhook handler is registered here but kept thin; logic lives
in routes/webhooks.py and packages/core.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from packages.core.observability.logging import configure_logging, get_logger
from packages.core.observability.tracing import configure_tracing

from apps.api.config import get_settings
from apps.api.middleware import RequestIdMiddleware
from apps.api.routes import admin, health, webhooks


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, env=settings.app_env)
    configure_tracing(
        service_name=settings.otel_service_name,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        env=settings.app_env,
    )
    HTTPXClientInstrumentor().instrument()

    log = get_logger(__name__)
    log.info("sentinel.startup", env=settings.app_env, port=settings.api_port)
    yield
    log.info("sentinel.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sentinel",
        description="Production-grade GitHub PR review system.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(admin.router)
    FastAPIInstrumentor.instrument_app(app)
    return app


app = create_app()

"""Request ID middleware that injects a correlation ID into logs and OTel baggage."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from opentelemetry import baggage, context, trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "x-request-id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request_id to every request, exposing it via:

    - the `X-Request-ID` response header
    - structlog contextvars (so logs include it)
    - OTel baggage (so traces and downstream calls inherit it)
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.bind_contextvars(request_id=request_id)
        ctx = baggage.set_baggage("request_id", request_id)
        token = context.attach(ctx)

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("request_id", request_id)

        try:
            response = await call_next(request)
        finally:
            context.detach(token)
            structlog.contextvars.unbind_contextvars("request_id")

        response.headers[REQUEST_ID_HEADER] = request_id
        return response

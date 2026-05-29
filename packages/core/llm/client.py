"""Thin async wrapper around the Anthropic SDK.

This is the single place all LLM calls go through. The wrapper owns:
  - Per-call timeout (asyncio.timeout)
  - Retries with exponential backoff + jitter (handled here, not by agents)
  - Token counting from the response
  - Cost calculation via the pricing table
  - OTel spans with attributes (model, agent, tokens, cost, latency, retry)
  - Prometheus metrics (requests, tokens, cost, latency)

Agents call `await client.complete(...)` or `await client.complete_structured(...)`
and never touch the Anthropic SDK directly. This is the same pattern as the
Engine wrapping individual Steps: cross-cutting concerns live at one layer,
business logic at another.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic.types import Message
from opentelemetry import trace

from packages.core.llm.pricing import cents_for, has_pricing
from packages.core.observability.logging import get_logger
from packages.core.observability.metrics import (
    llm_cost_cents_total,
    llm_latency_seconds,
    llm_requests_total,
    llm_tokens_total,
)

_rng = secrets.SystemRandom()
_log = get_logger(__name__)
_tracer = trace.get_tracer("sentinel.llm")


@dataclass(frozen=True)
class LLMRetryPolicy:
    """How the LLM client retries on transient failures."""

    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 30.0
    jitter_seconds: float = 0.5

    def backoff_for(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        raw = self.initial_backoff_seconds * (self.backoff_multiplier ** (attempt - 1))
        return min(raw, self.max_backoff_seconds)


@dataclass(frozen=True)
class LLMResponse:
    """A successful LLM call. Includes everything the caller needs to inspect.

    The `raw` field is the SDK's response object so structured-output helpers
    can pick the tool_use blocks out of it without us forking the SDK's typing.

    `tokens_in` is fresh (non-cached) input only, mirroring the Anthropic
    `usage.input_tokens` field. Prompt-cache reads and writes are reported
    separately in `cache_read_tokens` / `cache_creation_tokens`. Use
    `input_tokens_total` for the honest count of all input tokens processed.
    """

    text: str
    tokens_in: int
    tokens_out: int
    cost_cents: int
    model: str
    stop_reason: str
    raw: Message
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def input_tokens_total(self) -> int:
        """All input tokens the request comprised, including cached ones.

        `usage.input_tokens` counts only non-cached input; cache reads and
        writes are billed separately. Summing the three gives the true number
        of input tokens the model processed, which is what we record on the
        step so token totals stay honest even though the cost (computed with
        cache discounts) is much lower.
        """
        return self.tokens_in + self.cache_read_tokens + self.cache_creation_tokens


class LLMError(Exception):
    """Wraps the underlying SDK exception after retries are exhausted."""

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(
            f"LLM call failed after {attempts} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        )
        self.attempts = attempts
        self.last_error = last_error


class LLMClient:
    """Async wrapper around anthropic.AsyncAnthropic with retries + metrics.

    One instance per app is fine; the underlying SDK client is thread-safe
    and connection-pooled. Construct via apps/api/deps.get_llm_client().
    """

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 60.0,
        retry_policy: LLMRetryPolicy | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required (set ANTHROPIC_API_KEY in .env)")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._timeout_seconds = timeout_seconds
        self._retry_policy = retry_policy or LLMRetryPolicy()

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        agent: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Call Anthropic with retries, return a typed LLMResponse.

        `agent` is the label used in metrics (e.g. "diff_analyzer",
        "security_reviewer"). It's how we'll later slice cost by who's
        spending it.
        """
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system is not None:
            params["system"] = system
        if tools is not None:
            params["tools"] = tools
        if tool_choice is not None:
            params["tool_choice"] = tool_choice

        return await self._call_with_retries(model=model, agent=agent, params=params)

    async def _call_with_retries(
        self,
        *,
        model: str,
        agent: str,
        params: dict[str, Any],
    ) -> LLMResponse:
        last_error: BaseException | None = None

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            start = time.monotonic()
            with _tracer.start_as_current_span(
                f"llm.{agent}",
                attributes={
                    "llm.model": model,
                    "llm.agent": agent,
                    "llm.attempt": attempt,
                },
            ) as span:
                try:
                    async with asyncio.timeout(self._timeout_seconds):
                        message = await self._client.messages.create(**params)
                except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                    last_error = exc
                    latency = time.monotonic() - start
                    self._record_failure(model, agent, latency, exc, attempt, span)
                    if self._should_retry(exc, attempt):
                        await self._sleep_for_attempt(attempt)
                        continue
                    raise LLMError(attempt, exc) from exc
                except TimeoutError as exc:
                    last_error = exc
                    latency = time.monotonic() - start
                    self._record_failure(model, agent, latency, exc, attempt, span)
                    if attempt < self._retry_policy.max_attempts:
                        await self._sleep_for_attempt(attempt)
                        continue
                    raise LLMError(attempt, exc) from exc

                # Success path
                latency = time.monotonic() - start
                response = self._build_response(message, model)
                self._record_success(model, agent, latency, response, span)
                return response

        # Defensive: should be unreachable; the loop either returns or raises.
        assert last_error is not None
        raise LLMError(self._retry_policy.max_attempts, last_error)

    def _build_response(self, message: Message, model: str) -> LLMResponse:
        # Extract plain text from text blocks (ignoring tool_use blocks).
        text_chunks = [block.text for block in message.content if block.type == "text"]
        text = "".join(text_chunks)

        usage = message.usage
        tokens_in = usage.input_tokens
        tokens_out = usage.output_tokens
        # These fields are absent on older responses and on test mocks shaped
        # as a bare SimpleNamespace; getattr defaults to 0, and `or 0` folds
        # the real API's None (when caching is off) down to 0 as well.
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = cents_for(
            model,
            tokens_in,
            tokens_out,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

        if not has_pricing(model):
            _log.warning(
                "llm.unknown_pricing",
                model=model,
                hint="Add an entry to packages/core/llm/pricing.py PRICING table.",
            )

        return LLMResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_cents=cost,
            model=model,
            stop_reason=message.stop_reason or "",
            raw=message,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

    def _record_success(
        self,
        model: str,
        agent: str,
        latency: float,
        response: LLMResponse,
        span: trace.Span,
    ) -> None:
        llm_requests_total.labels(model=model, agent=agent, status="success").inc()
        llm_tokens_total.labels(model=model, agent=agent, direction="in").inc(response.tokens_in)
        llm_tokens_total.labels(model=model, agent=agent, direction="out").inc(response.tokens_out)
        llm_cost_cents_total.labels(model=model, agent=agent).inc(response.cost_cents)
        llm_latency_seconds.labels(model=model, agent=agent, status="success").observe(latency)

        span.set_attribute("llm.tokens_in", response.tokens_in)
        span.set_attribute("llm.tokens_out", response.tokens_out)
        span.set_attribute("llm.cache_read_tokens", response.cache_read_tokens)
        span.set_attribute("llm.cache_creation_tokens", response.cache_creation_tokens)
        span.set_attribute("llm.cost_cents", response.cost_cents)
        span.set_attribute("llm.latency_seconds", latency)
        span.set_attribute("llm.stop_reason", response.stop_reason)
        span.set_attribute("llm.status", "success")

    def _record_failure(
        self,
        model: str,
        agent: str,
        latency: float,
        exc: BaseException,
        attempt: int,
        span: trace.Span,
    ) -> None:
        llm_requests_total.labels(model=model, agent=agent, status="failure").inc()
        llm_latency_seconds.labels(model=model, agent=agent, status="failure").observe(latency)
        span.set_attribute("llm.status", "failure")
        span.set_attribute("llm.error_type", type(exc).__name__)
        span.record_exception(exc)
        _log.warning(
            "llm.attempt_failed",
            model=model,
            agent=agent,
            attempt=attempt,
            error_type=type(exc).__name__,
            error=str(exc),
        )

    def _should_retry(self, exc: BaseException, attempt: int) -> bool:
        if attempt >= self._retry_policy.max_attempts:
            return False
        # Retry transient categories. Don't retry 4xx other than 429.
        if isinstance(exc, anthropic.APIConnectionError):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            # getattr returns Any when a default is provided; annotate so the
            # comparisons below return bool rather than Any.
            status: int | None = getattr(exc, "status_code", None)
            if status is None:
                return False
            if status == 429:
                return True
            return 500 <= status < 600
        return False

    async def _sleep_for_attempt(self, attempt: int) -> None:
        backoff = self._retry_policy.backoff_for(attempt)
        jitter = _rng.uniform(0, self._retry_policy.jitter_seconds)
        await asyncio.sleep(backoff + jitter)

    async def aclose(self) -> None:
        await self._client.close()

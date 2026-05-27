"""Prometheus metric registry. Define metrics here so all callers share the same objects."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

registry = CollectorRegistry()

# --- Webhook / API ---
webhooks_received_total = Counter(
    "sentinel_webhooks_received_total",
    "GitHub webhooks received, by event type and validation result.",
    labelnames=("event", "result"),
    registry=registry,
)

# --- ReviewRun lifecycle ---
review_runs_total = Counter(
    "sentinel_review_runs_total",
    "ReviewRun lifecycle events.",
    labelnames=("status",),
    registry=registry,
)

plan_duration_seconds = Histogram(
    "sentinel_plan_duration_seconds",
    "End-to-end plan duration.",
    labelnames=("plan", "status"),
    registry=registry,
)

step_duration_seconds = Histogram(
    "sentinel_step_duration_seconds",
    "Per-step duration.",
    labelnames=("step", "status"),
    registry=registry,
)

# --- LLM client ---
llm_requests_total = Counter(
    "sentinel_llm_requests_total",
    "LLM API calls, by model, agent, and outcome.",
    labelnames=("model", "agent", "status"),
    registry=registry,
)

llm_tokens_total = Counter(
    "sentinel_llm_tokens_total",
    "Tokens consumed by LLM calls.",
    labelnames=("model", "agent", "direction"),  # direction: in | out
    registry=registry,
)

llm_cost_cents_total = Counter(
    "sentinel_llm_cost_cents_total",
    "Cost of LLM calls in cents (integer; sub-cent costs accumulate).",
    labelnames=("model", "agent"),
    registry=registry,
)

llm_latency_seconds = Histogram(
    "sentinel_llm_latency_seconds",
    "LLM API call latency.",
    labelnames=("model", "agent", "status"),
    registry=registry,
)

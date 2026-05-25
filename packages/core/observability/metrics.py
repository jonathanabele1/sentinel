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

"""Engine: walks a Plan against a ReviewRun, snapshotting every step to Postgres.

For each Step in the Plan, the Engine:
  1. Calls step.build_inputs(ctx) to construct typed inputs from prior outputs.
  2. Inserts a StepExecution row with status="running" and the inputs snapshotted
     as JSONB. This is the moment that makes replay possible later.
  3. Opens an OpenTelemetry span around the execution.
  4. Calls step.execute(inputs, ctx) under the configured timeout and retry policy.
  5. On success: snapshots outputs to JSONB, marks the row "completed", stores
     the output in ctx.outputs so later steps can read it.
  6. On failure (after retries): marks the row "failed", records the error,
     stops the plan, marks the run failed. Week 2 doesn't have graceful
     degradation; Week 6 will.
  7. Emits Prometheus duration histograms regardless of outcome.

The Engine is stateless apart from the tracer and logger. One instance is
safe to share across requests.
"""

from __future__ import annotations

import asyncio
import random
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from opentelemetry import trace
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.db import ReviewRun, StepExecution
from packages.core.observability.logging import get_logger
from packages.core.observability.metrics import (
    plan_duration_seconds,
    step_duration_seconds,
)
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.plan import Plan
from packages.core.orchestrator.step import Step

# secrets.SystemRandom is cryptographically-strong; we use it for jitter not for
# security, but it sidesteps ruff's S311 warning against `random` in security
# contexts and the difference in cost is negligible.
_rng = secrets.SystemRandom()


class StepFailedError(Exception):
    """A step exhausted its retries. Wraps the last underlying exception."""

    def __init__(self, step_name: str, attempts: int, last_error: BaseException):
        super().__init__(
            f"Step {step_name!r} failed after {attempts} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        )
        self.step_name = step_name
        self.attempts = attempts
        self.last_error = last_error


class Engine:
    """Executes Plans against ReviewRuns.

    Stateless across calls; safe to share one instance across the app.
    """

    def __init__(self) -> None:
        self._tracer = trace.get_tracer("sentinel.orchestrator")
        self._log = get_logger(__name__)

    async def run(
        self,
        plan: Plan,
        run: ReviewRun,
        session: AsyncSession,
    ) -> ReviewRun:
        """Execute the Plan end-to-end against the given ReviewRun.

        Commits per step so audit-trail rows are visible even if the run
        fails partway through. Always updates the run row with a terminal
        status (completed or failed) before returning.
        """
        plan_start = time.monotonic()
        ctx = StepContext(
            run=run,
            session=session,
            log=self._log.bind(run_id=str(run.id), plan=plan.name),
        )

        with self._tracer.start_as_current_span(
            f"plan.{plan.name}",
            attributes={
                "sentinel.run_id": str(run.id),
                "sentinel.plan": plan.name,
                "sentinel.pr_url": run.pr_url,
            },
        ):
            run.status = "running"
            await session.commit()
            ctx.log.info("plan.started", steps=list(plan.step_names))

            final_status = "completed"
            error_msg: str | None = None

            try:
                for step in plan.steps:
                    await self._run_step(step, ctx)
            except Exception as exc:
                final_status = "failed"
                error_msg = str(exc)
                ctx.log.error("plan.failed", error=error_msg)
                raise
            finally:
                run.status = final_status
                run.error = error_msg
                run.completed_at = datetime.now(UTC)
                await session.commit()
                plan_duration_seconds.labels(plan=plan.name, status=final_status).observe(
                    time.monotonic() - plan_start
                )

            ctx.log.info("plan.completed")
            return run

    async def _run_step(self, step: Step[Any, Any], ctx: StepContext) -> None:
        """Execute one step end-to-end with snapshotting, retries, observability."""
        step_start = time.monotonic()
        step_log = ctx.log.bind(step=step.name)

        with self._tracer.start_as_current_span(
            f"step.{step.name}",
            attributes={"sentinel.step.name": step.name},
        ) as span:
            # Build the typed input model from the context. This is what gets
            # snapshotted; everything later flows from these inputs.
            try:
                inputs = step.build_inputs(ctx)
            except Exception as exc:
                step_log.error("step.build_inputs_failed", error=str(exc))
                span.set_attribute("sentinel.step.status", "failed")
                raise

            # Insert the StepExecution row with inputs snapshotted. Commit so
            # the audit trail is visible even if execute() crashes later.
            step_row = StepExecution(
                id=uuid.uuid4(),
                run_id=ctx.run.id,
                step_name=step.name,
                status="running",
                inputs=inputs.model_dump(mode="json"),
                started_at=datetime.now(UTC),
            )
            ctx.session.add(step_row)
            await ctx.session.commit()
            span.set_attribute("sentinel.step.execution_id", str(step_row.id))

            # Execute with timeout and retries. The engine, not the step, owns
            # this concern. Steps just write the work; the engine wraps it.
            try:
                outputs = await self._execute_with_retries(step, inputs, ctx)
            except StepFailedError as exc:
                latency_ms = int((time.monotonic() - step_start) * 1000)
                step_row.status = "failed"
                step_row.error = str(exc.last_error)
                step_row.latency_ms = latency_ms
                step_row.completed_at = datetime.now(UTC)
                await ctx.session.commit()
                step_duration_seconds.labels(step=step.name, status="failed").observe(
                    latency_ms / 1000.0
                )
                span.set_attribute("sentinel.step.status", "failed")
                span.record_exception(exc.last_error)
                step_log.error(
                    "step.failed",
                    attempts=exc.attempts,
                    latency_ms=latency_ms,
                    error=str(exc.last_error),
                )
                raise

            # Success: snapshot outputs, store in context for downstream steps,
            # mark the row completed.
            latency_ms = int((time.monotonic() - step_start) * 1000)
            step_row.status = "completed"
            step_row.outputs = outputs.model_dump(mode="json")
            step_row.latency_ms = latency_ms
            step_row.completed_at = datetime.now(UTC)
            await ctx.session.commit()

            ctx.outputs[step.name] = outputs

            step_duration_seconds.labels(step=step.name, status="completed").observe(
                latency_ms / 1000.0
            )
            span.set_attribute("sentinel.step.status", "completed")
            step_log.info("step.completed", latency_ms=latency_ms)

    async def _execute_with_retries(
        self,
        step: Step[Any, Any],
        inputs: BaseModel,
        ctx: StepContext,
    ) -> BaseModel:
        """Run step.execute() under timeout + retry policy.

        Wraps every attempt in asyncio.timeout. On any exception, waits the
        policy-defined backoff (plus jitter), then retries. Raises
        StepFailedError carrying the last underlying exception once retries
        are exhausted.

        Week 2 retries ALL exception types. Weeks 3-6 will discriminate
        between transient failures (worth retrying) and permanent ones
        (validation errors, programming bugs) that should fail fast.
        """
        policy = step.retry_policy
        last_error: BaseException | None = None

        for attempt in range(1, policy.max_attempts + 1):
            try:
                async with asyncio.timeout(step.timeout_seconds):
                    # step is Step[Any, Any] so execute() returns Any; cast to
                    # the concrete BaseModel for the engine's return contract.
                    return cast("BaseModel", await step.execute(inputs, ctx))
            except Exception as exc:  # Week 2 retries broadly; see docstring
                last_error = exc
                ctx.log.warning(
                    "step.attempt_failed",
                    step=step.name,
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if attempt < policy.max_attempts:
                    backoff = policy.backoff_for(attempt)
                    jitter = _rng.uniform(0, policy.jitter_seconds)
                    await asyncio.sleep(backoff + jitter)

        assert last_error is not None  # mypy: loop ran at least once
        raise StepFailedError(step.name, policy.max_attempts, last_error)

    async def replay_step(
        self,
        step: Step[Any, Any],
        snapshotted_inputs: dict[str, Any],
        ctx: StepContext,
    ) -> BaseModel:
        """Re-run one step using inputs read from a step_executions row.

        Does NOT write to the database. Useful for debugging: feed in the
        exact JSONB inputs from a past failure and watch the step run again,
        then compare to the snapshotted outputs.

        Persistent replay (writing a new step_executions row marked as a
        replay) is a future addition; the read-only version is sufficient
        for the Week 2 Definition of Done.
        """
        inputs = step.input_model.model_validate(snapshotted_inputs)
        with self._tracer.start_as_current_span(
            f"replay.{step.name}",
            attributes={
                "sentinel.step.name": step.name,
                "sentinel.replay": True,
            },
        ):
            # Same cast rationale as _execute_with_retries.
            return cast("BaseModel", await step.execute(inputs, ctx))


# Module-level random for non-security jitter only.
def _seed_for_tests(seed: int) -> None:
    """Test hook: deterministic jitter. Never call outside tests."""
    global _rng
    # Deterministic random is the entire point of this test hook;
    # S311 (cryptographic warning) is irrelevant here.
    _rng = random.Random(seed)  # type: ignore[assignment]  # noqa: S311

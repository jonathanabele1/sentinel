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
     stops the plan, marks the run failed. Graceful degradation is not
     implemented yet.
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.models.db import ReviewRun, StepExecution
from packages.core.models.session import get_session_factory
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
                await self._execute_dag(plan, ctx)
            except Exception as exc:
                final_status = "failed"
                error_msg = str(exc)
                ctx.log.error("plan.failed", error=error_msg)
                raise
            finally:
                # Roll up per-step token/cost into the run. Reads the
                # step_executions rows the per-step sessions committed.
                totals = (
                    await session.execute(
                        select(
                            func.coalesce(func.sum(StepExecution.tokens_in), 0),
                            func.coalesce(func.sum(StepExecution.tokens_out), 0),
                            func.coalesce(func.sum(StepExecution.cost_cents), 0),
                        ).where(StepExecution.run_id == run.id)
                    )
                ).one()
                run.total_tokens = int(totals[0]) + int(totals[1])
                run.cost_cents = int(totals[2])
                run.status = final_status
                run.error = error_msg
                run.completed_at = datetime.now(UTC)
                await session.commit()
                plan_duration_seconds.labels(plan=plan.name, status=final_status).observe(
                    time.monotonic() - plan_start
                )

            ctx.log.info("plan.completed")
            return run

    async def _execute_dag(self, plan: Plan, ctx: StepContext) -> None:
        """Walk the Plan as a DAG: each round, run all currently-ready steps
        in parallel; wait for them all to finish; recompute ready; repeat.

        A step is "ready" when every step in its resolved depends_on has
        been marked completed. A purely linear Plan has exactly one ready
        step per round, so it runs sequentially with no extra cost.

        Failure semantics: if any step in a round raises, the whole DAG
        execution stops. Already-completed steps stay committed (per-step
        commits in _run_step). In-flight peers run to completion before
        the exception propagates, so their snapshot rows aren't lost.
        A future change will add graceful degradation: continue with
        siblings' outputs even when one of them fails.
        """
        deps = plan.resolved_dependencies()
        pending: set[str] = set(deps.keys())
        completed: set[str] = set()
        # Each parallel step gets its own session (an AsyncSession is not
        # safe for concurrent use), so we hand _run_step a factory rather
        # than the run-level session.
        session_factory = get_session_factory()

        while pending:
            ready = [
                step
                for step in plan.steps
                if step.name in pending and set(deps[step.name]).issubset(completed)
            ]
            if not ready:
                # Should be unreachable: Plan validation guarantees an
                # acyclic graph, so there's always at least one ready
                # step until everything's done. This is a defensive
                # check for "an invariant we believed got broken."
                raise RuntimeError(
                    f"Plan {plan.name!r}: no ready steps but pending={sorted(pending)}. "
                    f"Possible cycle that slipped past validation."
                )

            ctx.log.info(
                "plan.round_started",
                ready=[s.name for s in ready],
                pending=sorted(pending - {s.name for s in ready}),
            )

            # Run all ready steps concurrently. asyncio.gather collects
            # their results; if any raises, the exception propagates after
            # the siblings finish (because we don't pass return_exceptions).
            await asyncio.gather(*(self._run_step(step, ctx, session_factory) for step in ready))

            for step in ready:
                pending.discard(step.name)
                completed.add(step.name)

    async def _run_step(
        self,
        step: Step[Any, Any],
        parent_ctx: StepContext,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Execute one step end-to-end in its OWN database session.

        Steps in the same DAG round run concurrently via asyncio.gather,
        and an AsyncSession is not safe for concurrent use. So each step
        gets a fresh session. The per-step context shares `run`, `log`,
        and the `outputs` dict (so downstream steps can read this step's
        output) but isolates the session.

        Reading parent_ctx.run.<attr> across coroutines is safe: those
        attributes are already loaded and expire_on_commit is off, so no
        lazy load touches the run-level session concurrently.
        """
        step_start = time.monotonic()
        step_log = parent_ctx.log.bind(step=step.name)

        async with session_factory() as session:
            ctx = StepContext(
                run=parent_ctx.run,
                session=session,
                log=step_log,
                outputs=parent_ctx.outputs,  # shared dict across steps
            )

            with self._tracer.start_as_current_span(
                f"step.{step.name}",
                attributes={"sentinel.step.name": step.name},
            ) as span:
                # Build the typed input model. This is what gets snapshotted;
                # everything later flows from these inputs.
                try:
                    inputs = step.build_inputs(ctx)
                except Exception as exc:
                    step_log.error("step.build_inputs_failed", error=str(exc))
                    span.set_attribute("sentinel.step.status", "failed")
                    raise

                # Insert the StepExecution row with inputs snapshotted. Commit
                # so the audit trail is visible even if execute() crashes later.
                step_row = StepExecution(
                    id=uuid.uuid4(),
                    run_id=ctx.run.id,
                    step_name=step.name,
                    status="running",
                    inputs=inputs.model_dump(mode="json"),
                    started_at=datetime.now(UTC),
                )
                session.add(step_row)
                await session.commit()
                span.set_attribute("sentinel.step.execution_id", str(step_row.id))

                # Execute with timeout and retries. The engine, not the step,
                # owns this concern. Steps just write the work; engine wraps it.
                try:
                    outputs = await self._execute_with_retries(step, inputs, ctx)
                except StepFailedError as exc:
                    latency_ms = int((time.monotonic() - step_start) * 1000)
                    step_row.status = "failed"
                    step_row.error = str(exc.last_error)
                    step_row.latency_ms = latency_ms
                    step_row.completed_at = datetime.now(UTC)
                    await session.commit()
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

                # Success: snapshot outputs, record per-step LLM usage that
                # the step reported via ctx.add_usage(), store output in the
                # shared context for downstream steps, mark the row completed.
                latency_ms = int((time.monotonic() - step_start) * 1000)
                step_row.status = "completed"
                step_row.outputs = outputs.model_dump(mode="json")
                step_row.latency_ms = latency_ms
                step_row.tokens_in = ctx.tokens_in
                step_row.tokens_out = ctx.tokens_out
                step_row.cost_cents = ctx.cost_cents
                step_row.completed_at = datetime.now(UTC)
                await session.commit()

                parent_ctx.outputs[step.name] = outputs

                step_duration_seconds.labels(step=step.name, status="completed").observe(
                    latency_ms / 1000.0
                )
                span.set_attribute("sentinel.step.status", "completed")
                span.set_attribute("sentinel.step.cost_cents", ctx.cost_cents)
            step_log.info(
                "step.completed",
                latency_ms=latency_ms,
                tokens_in=ctx.tokens_in,
                tokens_out=ctx.tokens_out,
                cost_cents=ctx.cost_cents,
            )

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

        This currently retries ALL exception types. A future change will
        discriminate between transient failures (worth retrying) and
        permanent ones (validation errors, programming bugs) that should
        fail fast.
        """
        policy = step.retry_policy
        last_error: BaseException | None = None

        for attempt in range(1, policy.max_attempts + 1):
            try:
                async with asyncio.timeout(step.timeout_seconds):
                    # step is Step[Any, Any] so execute() returns Any; cast to
                    # the concrete BaseModel for the engine's return contract.
                    return cast("BaseModel", await step.execute(inputs, ctx))
            except Exception as exc:  # retries broadly for now; see docstring
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
        for now.
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

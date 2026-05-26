"""Integration tests for Engine: end-to-end against real Postgres.

Run via `make test-integration` (requires `make up && make migrate` first).
Tests are marked with @pytest.mark.integration so the CI unit-test job
skips them.

Each test creates a ReviewRun with a unique UUID and asserts on the rows
the engine writes. Tests intentionally leave their rows behind; clearing
them isn't necessary for correctness and the dev DB is disposable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from packages.core.models.db import ReviewRun, StepExecution
from packages.core.orchestrator import Engine, Plan, RetryPolicy, Step, StepContext
from packages.core.orchestrator.steps.noop import NoopInputs, NoopOutputs, NoopStep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _make_run(pr_number: int = 999) -> ReviewRun:
    return ReviewRun(
        id=uuid.uuid4(),
        pr_url=f"https://github.com/test/repo/pull/{pr_number}",
        repo_full_name="test/repo",
        pr_number=pr_number,
        head_sha="deadbeef",
        plan_name="default",
        status="pending",
        started_at=datetime.now(UTC),
    )


async def _persist(session: AsyncSession, run: ReviewRun) -> None:
    session.add(run)
    await session.commit()


async def test_engine_runs_noop_to_completion(session: AsyncSession) -> None:
    """Happy path: engine runs the noop step and writes both rows correctly."""
    run = _make_run()
    await _persist(session, run)

    engine = Engine()
    plan = Plan(name="default", steps=(NoopStep(),))

    result = await engine.run(plan, run, session)

    assert result.status == "completed"
    assert result.error is None
    assert result.completed_at is not None

    # The step_executions row should exist with completed status + outputs.
    stmt = select(StepExecution).where(StepExecution.run_id == run.id)
    step_rows = (await session.execute(stmt)).scalars().all()
    assert len(step_rows) == 1
    step_row = step_rows[0]
    assert step_row.step_name == "noop"
    assert step_row.status == "completed"
    assert step_row.error is None
    assert step_row.inputs["pr_url"] == run.pr_url
    assert "message" in step_row.outputs
    assert "timestamp" in step_row.outputs
    assert step_row.latency_ms is not None and step_row.latency_ms >= 0


class _AlwaysFailStep(Step[NoopInputs, NoopOutputs]):
    """A step that always raises, with fast retries for tests."""

    name = "always_fail"
    input_model = NoopInputs
    output_model = NoopOutputs
    timeout_seconds = 5
    retry_policy = RetryPolicy(
        max_attempts=2,
        initial_backoff_seconds=0.01,
        backoff_multiplier=1.0,
        max_backoff_seconds=0.01,
        jitter_seconds=0.0,
    )

    def build_inputs(self, ctx: StepContext) -> NoopInputs:
        return NoopInputs(run_id=str(ctx.run.id), pr_url=ctx.run.pr_url)

    async def execute(self, inputs: NoopInputs, ctx: StepContext) -> NoopOutputs:
        raise RuntimeError("intentional test failure")


async def test_engine_marks_run_failed_when_step_fails(
    session: AsyncSession,
) -> None:
    """A step that exhausts retries should leave the run marked 'failed'."""
    from packages.core.orchestrator import StepFailedError

    run = _make_run(pr_number=998)
    await _persist(session, run)

    engine = Engine()
    plan = Plan(name="failing", steps=(_AlwaysFailStep(),))

    with pytest.raises(StepFailedError):
        await engine.run(plan, run, session)

    # The run row should be marked failed with the error captured.
    await session.refresh(run)
    assert run.status == "failed"
    assert run.error is not None
    assert "always_fail" in run.error

    # The step_executions row should also be marked failed.
    stmt = select(StepExecution).where(StepExecution.run_id == run.id)
    step_row = (await session.execute(stmt)).scalars().one()
    assert step_row.status == "failed"
    assert step_row.error is not None
    assert "intentional test failure" in step_row.error


async def test_engine_replay_step_returns_equivalent_output(
    session: AsyncSession,
) -> None:
    """Replay re-runs the step using snapshotted inputs and returns new output."""
    run = _make_run(pr_number=997)
    await _persist(session, run)

    engine = Engine()
    plan = Plan(name="default", steps=(NoopStep(),))
    await engine.run(plan, run, session)

    # Pull the step_executions row we just created.
    stmt = select(StepExecution).where(StepExecution.run_id == run.id)
    step_row = (await session.execute(stmt)).scalars().one()

    # Replay with the snapshotted inputs.
    import structlog

    ctx = StepContext(
        run=run,
        session=session,
        log=structlog.get_logger(),
    )
    step = plan.get_step("noop")
    replayed = await engine.replay_step(step, step_row.inputs, ctx)

    # The message field is deterministic in pr_url; timestamps differ.
    replayed_dict = replayed.model_dump(mode="json")
    assert replayed_dict["message"] == step_row.outputs["message"]
    # Timestamp will differ because datetime.now() moved on.
    # (We don't assert inequality strictly; on a very fast machine they
    # could coincide. The point is the replay machinery completed.)

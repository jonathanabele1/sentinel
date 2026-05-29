"""NoopStep: a trivial step that does nothing useful but exercises the engine.

The purpose isn't the work; it's to prove the orchestrator's plumbing end-to-end:
  - build_inputs pulls data from the ReviewRun into a typed Pydantic model.
  - The engine snapshots those inputs to step_executions.inputs as JSONB.
  - execute returns a typed output model.
  - The engine snapshots those outputs to step_executions.outputs as JSONB.
  - The replay endpoint can read the snapshotted inputs and re-execute.

The real diff_analyzer step replaces this. The contract a Step satisfies is
what that step also satisfies; nothing about the engine needs to change.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.step import Step


class NoopInputs(BaseModel):
    """Inputs the engine snapshots to step_executions.inputs (JSONB).

    Carrying run_id and pr_url is gratuitous for a noop, but it makes the
    snapshotted row meaningful in DBeaver and confirms the JSONB round-trip
    works end-to-end. Real steps will have inputs that actually drive work.
    """

    run_id: str
    pr_url: str


class NoopOutputs(BaseModel):
    """Outputs the engine snapshots to step_executions.outputs (JSONB)."""

    message: str
    timestamp: str = Field(description="ISO-8601 UTC timestamp when execute ran")


class NoopStep(Step[NoopInputs, NoopOutputs]):
    """A step that exercises the engine without doing real work."""

    name = "noop"
    input_model = NoopInputs
    output_model = NoopOutputs

    def build_inputs(self, ctx: StepContext) -> NoopInputs:
        return NoopInputs(
            run_id=str(ctx.run.id),
            pr_url=ctx.run.pr_url,
        )

    async def execute(self, inputs: NoopInputs, ctx: StepContext) -> NoopOutputs:
        ctx.log.info("noop.executed", pr_url=inputs.pr_url)
        return NoopOutputs(
            message=f"NoopStep ran successfully for {inputs.pr_url}",
            timestamp=datetime.now(UTC).isoformat(),
        )

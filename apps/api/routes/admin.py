"""Admin endpoints: replay, run inspection.

POST /admin/runs/{run_id}/replay?step={step_name}

Re-runs a single step using the inputs snapshotted at original execution
time. Useful for debugging ("why did the diff analyzer produce that
output?") and for regression testing prompts ("does the new prompt
produce better findings on this historical input?").

This is the payoff of snapshotting every step's inputs to JSONB: any past
execution can be reconstructed without re-deriving its inputs from prior
steps. The replay is read-only by default; persistent replay (writing a
new step_executions row marked as a replay) is a future addition.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from packages.core.models.db import ReviewRun, StepExecution
from packages.core.models.session import get_session
from packages.core.observability.logging import get_logger
from packages.core.orchestrator import Engine, StepContext, default_review_plan
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_engine

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger(__name__)


@router.post("/runs/{run_id}/replay")
async def replay_step(
    run_id: uuid.UUID,
    engine: Annotated[Engine, Depends(get_engine)],
    session: Annotated[AsyncSession, Depends(get_session)],
    step_name: Annotated[str, Query(alias="step", description="Step name to replay (e.g. 'noop')")],
) -> dict[str, Any]:
    """Re-run one step using its previously snapshotted inputs.

    Returns the new outputs alongside the original outputs and a `match`
    field indicating whether they're byte-identical. For steps with
    nondeterministic content (timestamps, LLM-generated text), `match`
    will often be False; that's expected and not a failure.
    """
    run = await session.get(ReviewRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    # Fetch the original step_executions row.
    stmt = select(StepExecution).where(
        StepExecution.run_id == run_id,
        StepExecution.step_name == step_name,
    )
    result = await session.execute(stmt)
    step_row = result.scalar_one_or_none()
    if step_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no step_execution for step '{step_name}' in run {run_id}",
        )

    # Find the Step instance in the plan. Hardcoded to default_review_plan
    # for Week 2; future plans get looked up by `run.plan_name` via a registry.
    if run.plan_name != default_review_plan.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"run was executed under plan '{run.plan_name}', "
                f"but replay only supports '{default_review_plan.name}' in Week 2"
            ),
        )
    step_obj = default_review_plan.get_step(step_name)

    # Build a fresh context for the replay. Note: ctx.outputs is empty;
    # replay does NOT depend on prior step outputs because the snapshotted
    # inputs are self-contained.
    ctx = StepContext(
        run=run,
        session=session,
        log=log.bind(run_id=str(run_id), step=step_name, replay=True),
    )

    new_output_model = await engine.replay_step(step_obj, step_row.inputs, ctx)
    new_outputs = new_output_model.model_dump(mode="json")

    ctx.log.info(
        "step.replayed",
        original_status=step_row.status,
        match=step_row.outputs == new_outputs,
    )

    return {
        "run_id": str(run_id),
        "step": step_name,
        "original_status": step_row.status,
        "original_outputs": step_row.outputs,
        "new_outputs": new_outputs,
        "match": step_row.outputs == new_outputs,
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Fetch a run with its step executions for inspection."""
    run = await session.get(ReviewRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    stmt = (
        select(StepExecution)
        .where(StepExecution.run_id == run_id)
        .order_by(StepExecution.started_at)
    )
    result = await session.execute(stmt)
    steps = result.scalars().all()

    return {
        "id": str(run.id),
        "pr_url": run.pr_url,
        "repo": run.repo_full_name,
        "pr_number": run.pr_number,
        "head_sha": run.head_sha,
        "plan_name": run.plan_name,
        "status": run.status,
        "error": run.error,
        "cost_cents": run.cost_cents,
        "total_tokens": run.total_tokens,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "steps": [
            {
                "id": str(s.id),
                "step_name": s.step_name,
                "status": s.status,
                "inputs": s.inputs,
                "outputs": s.outputs,
                "error": s.error,
                "latency_ms": s.latency_ms,
                "tokens_in": s.tokens_in,
                "tokens_out": s.tokens_out,
                "cost_cents": s.cost_cents,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            }
            for s in steps
        ],
    }

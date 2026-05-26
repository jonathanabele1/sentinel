"""Unit tests for NoopStep: build_inputs + execute."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
import structlog
from packages.core.models.db import ReviewRun
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.steps.noop import NoopInputs, NoopOutputs, NoopStep


def _make_ctx(pr_url: str = "https://github.com/acme/api/pull/42") -> StepContext:
    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url=pr_url,
        repo_full_name="acme/api",
        pr_number=42,
        head_sha="abc123",
        plan_name="default",
        status="pending",
    )
    return StepContext(
        run=run,
        session=MagicMock(),
        log=structlog.get_logger(),
    )


def test_build_inputs_pulls_from_run() -> None:
    ctx = _make_ctx(pr_url="https://github.com/acme/api/pull/7")
    step = NoopStep()

    inputs = step.build_inputs(ctx)

    assert isinstance(inputs, NoopInputs)
    assert inputs.pr_url == "https://github.com/acme/api/pull/7"
    assert inputs.run_id == str(ctx.run.id)


@pytest.mark.asyncio
async def test_execute_returns_message_with_pr_url() -> None:
    ctx = _make_ctx(pr_url="https://github.com/acme/api/pull/7")
    step = NoopStep()
    inputs = step.build_inputs(ctx)

    outputs = await step.execute(inputs, ctx)

    assert isinstance(outputs, NoopOutputs)
    assert "https://github.com/acme/api/pull/7" in outputs.message
    assert outputs.timestamp  # ISO string, not empty


def test_class_level_attributes() -> None:
    """Confirm Step subclass declares the expected ClassVars."""
    assert NoopStep.name == "noop"
    assert NoopStep.input_model is NoopInputs
    assert NoopStep.output_model is NoopOutputs

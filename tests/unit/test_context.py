"""Unit tests for StepContext: typed access to prior step outputs."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
import structlog
from packages.core.models.db import ReviewRun
from packages.core.orchestrator.context import StepContext
from pydantic import BaseModel


class FooOutput(BaseModel):
    value: int


class BarOutput(BaseModel):
    label: str


def _make_ctx() -> StepContext:
    """Build a StepContext with a stub run and no real DB session.

    These tests only exercise ctx.get_output, which doesn't touch the DB
    or the logger machinery, so MagicMocks are fine.
    """
    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url="https://example.test/pull/1",
        repo_full_name="acme/api",
        pr_number=1,
        head_sha="abc123",
        plan_name="default",
        status="pending",
    )
    return StepContext(
        run=run,
        session=MagicMock(),
        log=structlog.get_logger(),
    )


def test_get_output_returns_typed_value() -> None:
    ctx = _make_ctx()
    ctx.outputs["foo"] = FooOutput(value=42)

    result = ctx.get_output("foo", FooOutput)
    assert result.value == 42


def test_get_output_raises_on_missing_step() -> None:
    ctx = _make_ctx()
    with pytest.raises(KeyError, match="Step 'missing'"):
        ctx.get_output("missing", FooOutput)


def test_get_output_raises_on_type_mismatch() -> None:
    ctx = _make_ctx()
    ctx.outputs["foo"] = FooOutput(value=42)

    with pytest.raises(TypeError, match="Expected output of step 'foo' to be BarOutput"):
        ctx.get_output("foo", BarOutput)


def test_run_id_property() -> None:
    ctx = _make_ctx()
    assert ctx.run_id == ctx.run.id
    assert isinstance(ctx.run_id, uuid.UUID)

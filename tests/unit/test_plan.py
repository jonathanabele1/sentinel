"""Unit tests for Plan: validation, accessors."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from packages.core.orchestrator.plan import Plan
from packages.core.orchestrator.steps.noop import NoopStep


def test_plan_requires_at_least_one_step() -> None:
    with pytest.raises(ValueError, match="must have at least one step"):
        Plan(name="empty", steps=())


def test_plan_rejects_duplicate_step_names() -> None:
    with pytest.raises(ValueError, match="duplicate step names"):
        # Two NoopStep instances both named "noop"
        Plan(name="duped", steps=(NoopStep(), NoopStep()))


def test_plan_step_names() -> None:
    plan = Plan(name="default", steps=(NoopStep(),))
    assert plan.step_names == ("noop",)


def test_plan_get_step_returns_instance() -> None:
    noop = NoopStep()
    plan = Plan(name="default", steps=(noop,))
    assert plan.get_step("noop") is noop


def test_plan_get_step_raises_for_unknown_name() -> None:
    plan = Plan(name="default", steps=(NoopStep(),))
    with pytest.raises(KeyError, match="No step named 'missing'"):
        plan.get_step("missing")


def test_plan_is_frozen() -> None:
    plan = Plan(name="default", steps=(NoopStep(),))
    with pytest.raises(FrozenInstanceError):
        plan.name = "renamed"  # type: ignore[misc]

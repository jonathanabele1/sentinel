"""Plan: an ordered, named, immutable sequence of Steps.

A Plan is pure declarative data. It describes WHAT to run; the Engine decides
HOW. Plans are constructed once (usually as module-level constants) and walked
many times by the engine.

Week 2 plans are linear (steps run one after another). When Week 4 introduces
parallel specialist reviewers, Plan will gain dependency declarations between
steps and the engine will execute them as a DAG. The Plan API stays small in
the meantime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.core.orchestrator.step import Step


@dataclass(frozen=True)
class Plan:
    """An ordered list of Steps with a name.

    Frozen so the engine can trust that a Plan it's executing won't mutate
    underneath it. Steps are stored as a tuple for the same reason (lists
    would allow .append() after construction).

    Validation runs in __post_init__: every Plan must have at least one step,
    and step names must be unique. Both are programmer errors, not user input,
    so ValueError at construction time is the right shape.
    """

    name: str
    # Step[Any, Any] because Plans are heterogeneous: each Step has its own
    # InT/OutT, and the Plan doesn't (and shouldn't) constrain them.
    steps: tuple[Step[Any, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(f"Plan {self.name!r} must have at least one step")

        names = [step.name for step in self.steps]
        seen: set[str] = set()
        duplicates = [n for n in names if n in seen or seen.add(n)]  # type: ignore[func-returns-value]
        if duplicates:
            raise ValueError(f"Plan {self.name!r} has duplicate step names: {duplicates}")

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)

    def get_step(self, name: str) -> Step[Any, Any]:
        """Return the Step with the given name, or raise KeyError."""
        for step in self.steps:
            if step.name == name:
                return step
        raise KeyError(
            f"No step named {name!r} in plan {self.name!r}. Available: {list(self.step_names)}"
        )

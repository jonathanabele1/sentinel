"""Plan: a typed, named, immutable graph of Steps.

A Plan is pure declarative data. It describes WHAT to run; the Engine
decides HOW. Plans are constructed once (usually as module-level constants
or via a factory like build_default_plan) and walked many times by the
engine.

Plans can be linear (each step follows the previous) or DAG-shaped (steps
declare their dependencies via `Step.depends_on`). Validation enforces
that every declared dependency resolves to a real step and that the
graph is acyclic.

Backward compatibility: if no step declares depends_on, the Plan is
treated as a strict linear chain where each step depends on the step that
came before it in the tuple. Earlier linear plans keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.core.orchestrator.step import Step


@dataclass(frozen=True)
class Plan:
    """A typed, immutable graph of Steps.

    Frozen so the engine can trust that a Plan it's executing won't mutate
    underneath it. Steps are stored as a tuple for the same reason (lists
    would allow .append() after construction).

    Validation runs in __post_init__:
      - At least one step.
      - Unique step names.
      - Every depends_on entry resolves to a real step in the Plan.
      - No cycles in the dependency graph.

    All four are programmer errors caught at construction time. Plans
    should be built once at startup; failures here surface in tests and
    on app boot, not at runtime.
    """

    name: str
    # Step[Any, Any] because Plans are heterogeneous: each Step has its own
    # InT/OutT, and the Plan doesn't (and shouldn't) constrain them.
    steps: tuple[Step[Any, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(f"Plan {self.name!r} must have at least one step")

        # Step names must be unique.
        names = [step.name for step in self.steps]
        seen: set[str] = set()
        duplicates = [n for n in names if n in seen or seen.add(n)]  # type: ignore[func-returns-value]
        if duplicates:
            raise ValueError(f"Plan {self.name!r} has duplicate step names: {duplicates}")

        name_set = set(names)

        # Every declared depends_on must resolve to a step in the Plan.
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in name_set:
                    raise ValueError(
                        f"Plan {self.name!r}: step {step.name!r} declares "
                        f"depends_on={dep!r}, but no such step exists. "
                        f"Available: {sorted(name_set)}"
                    )

        # No cycles in the resolved DAG.
        cycle = _find_cycle(self.resolved_dependencies())
        if cycle:
            raise ValueError(f"Plan {self.name!r} has a dependency cycle: " + " → ".join(cycle))

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

    def resolved_dependencies(self) -> dict[str, tuple[str, ...]]:
        """Return the effective dependency map for each step.

        For a step with explicit `depends_on`, that tuple is returned as-is.
        For a step with empty `depends_on`, the implicit dependency is the
        step that immediately precedes it in `self.steps` (or nothing for
        the first step). This is the "linear by default" behaviour that
        keeps earlier linear plans working without modification.
        """
        deps: dict[str, tuple[str, ...]] = {}
        for index, step in enumerate(self.steps):
            if step.depends_on:
                deps[step.name] = step.depends_on
            elif index == 0:
                deps[step.name] = ()
            else:
                deps[step.name] = (self.steps[index - 1].name,)
        return deps


def _find_cycle(deps: dict[str, tuple[str, ...]]) -> list[str] | None:
    """Return a cycle as an ordered list of step names, or None if acyclic.

    Uses depth-first search with a "currently visiting" set. A back-edge
    into the visiting set is a cycle; we reconstruct the cycle by walking
    the parent map back to the repeated node.

    Returns None for an acyclic graph. The cycle list always starts and
    ends with the same node to make the error message self-evident.
    """
    WHITE, GRAY, BLACK = 0, 1, 2  # unvisited, in-progress, done
    color = dict.fromkeys(deps, WHITE)
    parent: dict[str, str | None] = dict.fromkeys(deps)

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        for neighbour in deps.get(node, ()):
            if color[neighbour] == GRAY:
                # Cycle: walk back from `node` via parent to `neighbour`.
                cycle = [node]
                cursor: str | None = node
                while cursor is not None and cursor != neighbour:
                    cursor = parent[cursor]
                    if cursor is not None:
                        cycle.append(cursor)
                cycle.append(neighbour)
                return list(reversed(cycle))
            if color[neighbour] == WHITE:
                parent[neighbour] = node
                found = dfs(neighbour)
                if found:
                    return found
        color[node] = BLACK
        return None

    for name in deps:
        if color[name] == WHITE:
            cycle = dfs(name)
            if cycle:
                return cycle
    return None

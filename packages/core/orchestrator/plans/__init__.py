"""Plan definitions.

Plans are composed from Step instances. Because Steps now take
dependencies (LLM client, diff client), Plans are built via factories
rather than declared as module-level constants. The factory pattern
lets the same Plan definition be reused with test doubles in tests.
"""

from packages.core.orchestrator.plans.default import DEFAULT_PLAN_NAME, build_default_plan

__all__ = ["DEFAULT_PLAN_NAME", "build_default_plan"]

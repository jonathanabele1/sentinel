"""Sentinel orchestrator: deterministic state machine for executing review plans.

The `plans` subpackage (build_default_plan, DEFAULT_PLAN_NAME) is deliberately
NOT re-exported here. Plans compose the agent Steps, and the agents import back
from this package (orchestrator.context, orchestrator.step); eagerly importing
plans from this __init__ would create an import cycle that surfaces depending
on which module gets imported first. Import plan factories from
packages.core.orchestrator.plans directly.
"""

from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.engine import Engine, StepFailedError
from packages.core.orchestrator.plan import Plan
from packages.core.orchestrator.step import RetryPolicy, Step

__all__ = [
    "Engine",
    "Plan",
    "RetryPolicy",
    "Step",
    "StepContext",
    "StepFailedError",
]

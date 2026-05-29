"""The Step abstraction: a typed, named, retried, timed-out unit of work.

A Step is the basic building block of the orchestrator. Each Step:
  - Has a unique name within its Plan (declared as a class variable).
  - Declares Pydantic input and output models for snapshotting to JSONB.
  - Implements build_inputs(ctx) to construct typed inputs from prior outputs.
  - Implements execute(inputs, ctx) to do the actual work.

The Engine wraps each Step execution with: input snapshotting, transaction
management, retries, timeouts, OTel spans, Prometheus metrics, and DB updates.
Steps themselves stay focused on their own logic.

The build_inputs / execute split is deliberate. build_inputs runs first, its
result is snapshotted to the step_executions.inputs JSONB column, and then
execute is called with those exact inputs. Replay later reads that JSONB row
and calls execute() directly with the recovered inputs, bypassing build_inputs.
That's what makes replay reproducible.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel

from packages.core.orchestrator.context import StepContext


@dataclass(frozen=True)
class RetryPolicy:
    """How to retry a Step on failure.

    Exponential backoff with jitter is the right default for most network and
    LLM operations. The engine applies the policy automatically; Steps don't
    implement retries themselves.
    """

    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 30.0
    jitter_seconds: float = 0.5

    @classmethod
    def no_retry(cls) -> RetryPolicy:
        """Never retry. Use for idempotent-write or otherwise dangerous steps."""
        return cls(max_attempts=1)

    def backoff_for(self, attempt: int) -> float:
        """Return seconds to wait before attempt N (1-indexed).

        attempt=1 → initial_backoff_seconds
        attempt=2 → initial * multiplier
        attempt=3 → initial * multiplier^2
        ...capped at max_backoff_seconds. Jitter is added by the engine.
        """
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        raw = self.initial_backoff_seconds * (self.backoff_multiplier ** (attempt - 1))
        return min(raw, self.max_backoff_seconds)


class Step[InT: BaseModel, OutT: BaseModel](abc.ABC):
    """A typed, named unit of work in a Plan.

    Subclasses declare:
      name           — unique step name (ClassVar)
      input_model    — Pydantic class for inputs (ClassVar)
      output_model   — Pydantic class for outputs (ClassVar)
      timeout_seconds — max execution time per attempt (ClassVar)
      retry_policy   — how to retry on failure (ClassVar)

    And implement:
      build_inputs(ctx) — pull data from prior step outputs into typed inputs
      execute(inputs, ctx) — the actual work

    Generic parameters use PEP 695 syntax (Python 3.12+):
      `class Step[InT: BaseModel, OutT: BaseModel](abc.ABC)`
    This replaces the older `TypeVar` + `Generic[...]` pattern.
    """

    name: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel]]
    timeout_seconds: ClassVar[int] = 30
    retry_policy: ClassVar[RetryPolicy] = RetryPolicy()
    # Names of upstream steps this Step depends on. Empty tuple is "no
    # explicit dependencies" — Plan validation treats that as a linear
    # chain (each step depends on the immediately previous one) so Week
    # 1-3 plans keep working without changes.
    depends_on: ClassVar[tuple[str, ...]] = ()

    @abc.abstractmethod
    def build_inputs(self, ctx: StepContext) -> InT:
        """Construct this step's inputs from prior step outputs.

        Called by the engine before execute(). The result is snapshotted to
        step_executions.inputs as JSONB, then passed to execute(). Replay
        skips this method and uses the snapshotted inputs directly.
        """

    @abc.abstractmethod
    async def execute(self, inputs: InT, ctx: StepContext) -> OutT:
        """Run the step. Must return an instance of output_model.

        Should be deterministic where possible: the same inputs should
        produce equivalent outputs across runs. LLM-backed steps are bounded
        nondeterminism (same inputs → similar but not byte-identical outputs).
        """

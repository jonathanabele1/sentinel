"""StepContext: the typed container passed to every Step.

Carries the run, a DB session, a bound logger, and outputs of prior steps in
the same plan. Steps fetch prior outputs by name with type checking; the
engine populates the outputs dict as steps complete.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from packages.core.models.db import ReviewRun

T = TypeVar("T", bound=BaseModel)


@dataclass
class StepContext:
    """Mutable execution context for one step.

    `outputs` is shared across all steps in a run (so downstream steps can
    read upstream output). The usage accumulators (tokens_in/out, cost_cents)
    are PER-STEP: the engine builds a fresh context per step and reads these
    back after execute() to record on the step_executions row. Steps that
    make LLM calls report usage via add_usage().
    """

    run: ReviewRun
    session: AsyncSession
    log: BoundLogger
    outputs: dict[str, BaseModel] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cents: int = 0

    @property
    def run_id(self) -> uuid.UUID:
        return self.run.id

    def add_usage(self, *, tokens_in: int, tokens_out: int, cost_cents: int) -> None:
        """Accumulate LLM usage for the current step.

        Agents call this after each LLM response so the engine can persist
        per-step token/cost on the step_executions row (and roll it up to
        the run). A step that calls the model multiple times accumulates.
        """
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_cents += cost_cents

    def get_output(self, step_name: str, expected_type: type[T]) -> T:
        """Fetch a prior step's output, asserting its type.

        Raises KeyError if no step with that name has produced output yet,
        and TypeError if the recorded output isn't an instance of the
        expected Pydantic model. The narrow API forces Steps to declare
        what they depend on at the call site.
        """
        if step_name not in self.outputs:
            raise KeyError(
                f"Step '{step_name}' has no recorded output in this context. "
                f"Available: {sorted(self.outputs.keys())}"
            )
        output = self.outputs[step_name]
        if not isinstance(output, expected_type):
            raise TypeError(
                f"Expected output of step '{step_name}' to be "
                f"{expected_type.__name__}, got {type(output).__name__}"
            )
        return output

"""Correctness reviewer: looks for bugs and logic issues in a PR diff.

Patterns the prompt focuses on:
  - Null/None dereferences and missing None checks
  - Missing error handling (silent except, swallowed errors, unhandled paths)
  - Off-by-one errors and incorrect bounds
  - Race conditions (especially in async code, shared mutable state)
  - Resource leaks (unclosed files / connections / sessions)
  - Wrong API usage (mis-ordered arguments, deprecated calls, type confusion)
  - Logic inversions (negated conditions, missing returns, fall-through)
  - Dead code, infinite loops, unreachable branches

Same calibration discipline as the security reviewer: precision over recall,
empty findings list is fine for clean diffs, calibrated confidence. Specialist
instructions live in the uncached tail after the shared context block.
"""

from __future__ import annotations

from packages.core.agents.base import (
    ReviewerInputs,
    build_reviewer_inputs,
    build_reviewer_messages,
)
from packages.core.agents.shared_prefix import (
    REPORT_FINDINGS_TOOL,
    SHARED_SYSTEM,
    shared_tools,
)
from packages.core.llm import LLMClient, complete_structured
from packages.core.models.domain import ModelFindings, ReviewerFindings, ReviewFinding
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.step import RetryPolicy, Step

PROMPT_VERSION = "v1"

SPECIALIST_INSTRUCTIONS = """\
Your task: review this pull request for CORRECTNESS issues only: null/None \
dereferences, missing error handling, off-by-one errors, race conditions, \
resource leaks (unclosed files/connections), wrong API usage, incorrect type \
assumptions, logic inversions, dead code, infinite loops, missing returns.

Task-specific priorities:
  - An empty findings list is the right answer when the diff is \
straightforwardly correct.
  - Calibrate down when you are guessing at intent; calibrate up when you can \
point at the specific bug.
  - Focus on real bugs, not style. "Could be cleaner", "prefer X pattern", and \
"consider renaming" are NOT findings. Stick to observable defects."""


class CorrectnessReviewStep(Step[ReviewerInputs, ReviewerFindings]):
    """LLM-backed Step that produces correctness findings."""

    name = "correctness_review"
    input_model = ReviewerInputs
    output_model = ReviewerFindings
    depends_on = ("analyze_diff",)
    timeout_seconds = 90
    retry_policy = RetryPolicy.no_retry()

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        model: str = "claude-sonnet-4-5",
    ) -> None:
        self._llm = llm_client
        self._model = model

    def build_inputs(self, ctx: StepContext) -> ReviewerInputs:
        return build_reviewer_inputs(ctx)

    async def execute(self, inputs: ReviewerInputs, ctx: StepContext) -> ReviewerFindings:
        messages = build_reviewer_messages(inputs, specialist_instructions=SPECIALIST_INSTRUCTIONS)

        parsed, response = await complete_structured(
            self._llm,
            model=self._model,
            schema=ModelFindings,
            messages=messages,
            system=SHARED_SYSTEM,
            agent="correctness_reviewer",
            tools=shared_tools(),
            forced_tool_name=REPORT_FINDINGS_TOOL,
        )

        ctx.add_usage(
            tokens_in=response.input_tokens_total,
            tokens_out=response.tokens_out,
            cost_cents=response.cost_cents,
        )

        findings = [
            ReviewFinding(reviewer="correctness", **m.model_dump()) for m in parsed.findings
        ]

        ctx.log.info(
            "correctness_reviewer.completed",
            tokens_in=response.tokens_in,
            cache_read_tokens=response.cache_read_tokens,
            cache_creation_tokens=response.cache_creation_tokens,
            tokens_out=response.tokens_out,
            cost_cents=response.cost_cents,
            prompt_version=PROMPT_VERSION,
            finding_count=len(findings),
            truncated_diff=inputs.truncated_diff,
        )
        return ReviewerFindings(findings=findings)

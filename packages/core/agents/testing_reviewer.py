"""Testing reviewer: looks for gaps in test coverage in a PR diff.

Patterns the prompt focuses on:
  - Missing tests for new non-trivial code
  - Brittle assertions (asserts that don't actually test what they claim)
  - Tests that test the mocks instead of the code
  - Missing edge cases (empty input, boundary values, error paths)
  - Missing error-path tests
  - Coverage regressions

This reviewer is the noisiest by default because every PR could theoretically
have more tests. The instructions aggressively gate against "more tests would
be nice" hand-waving; the consolidator's posting threshold filters further.
Specialist instructions live in the uncached tail after the shared context
block.
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
Your task: review this pull request for TESTING gaps only: missing tests for \
new non-trivial code, brittle assertions, tests that don't actually test what \
they claim, missing edge cases, missing error-path tests.

Task-specific priorities:
  - Don't ask for tests on trivial changes (docstring updates, comment edits, \
simple formatting, renames). An empty findings list is the right answer when \
testing is not the missing piece.
  - Calibrate high (>0.8) only when you can identify the specific untested \
path. "This could have more tests" without specifics is hand-waving; \
calibrate that under 0.5 or drop it entirely.
  - Always cite the function or behaviour that lacks coverage. "Add a test for \
the empty-list case in foo()" is fine; "more test coverage needed" is not."""


class TestingReviewStep(Step[ReviewerInputs, ReviewerFindings]):
    """LLM-backed Step that produces testing findings."""

    name = "testing_review"
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
            agent="testing_reviewer",
            tools=shared_tools(),
            forced_tool_name=REPORT_FINDINGS_TOOL,
        )

        ctx.add_usage(
            tokens_in=response.input_tokens_total,
            tokens_out=response.tokens_out,
            cost_cents=response.cost_cents,
        )

        findings = [ReviewFinding(reviewer="testing", **m.model_dump()) for m in parsed.findings]

        ctx.log.info(
            "testing_reviewer.completed",
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

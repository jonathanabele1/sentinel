"""Security reviewer: looks for security risks in a PR diff.

Patterns the prompt focuses on:
  - Injection (SQL, command, prompt, log injection)
  - Missing input validation
  - Broken authentication / authorization
  - Insecure deserialization (pickle, yaml.unsafe_load, eval)
  - Secrets or credentials in code or logs
  - Weak cryptography
  - Path traversal, SSRF
  - Insecure defaults
  - Sensitive data in logs

Designed for precision over recall: an empty findings list is the correct
answer for PRs without security-relevant changes. The consolidator's posting
threshold filters further before any human sees a finding.

The specialist instructions live in the uncached tail of the request (after
the shared, cache-marked context block). The reviewer field on every finding
is forced to "security" after parsing, as a defense-in-depth check against the
model mislabelling its own work.
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
Your task: review this pull request for SECURITY issues only: injection \
(SQL/command/prompt/log), missing input validation, broken authn/authz, \
insecure deserialization (pickle, yaml.unsafe_load, eval), secrets or \
credentials in code or logs, weak cryptography, path traversal, SSRF, \
insecure defaults, sensitive data in logs.

Task-specific priorities:
  - An empty findings list is the correct answer for PRs without \
security-relevant changes.
  - Only exceed 0.8 confidence when the code clearly demonstrates the \
vulnerability; calibrate down for speculative findings.
  - Tell the human reviewer what is wrong and what to do about it. Quote the \
exact code in `evidence`."""


class SecurityReviewStep(Step[ReviewerInputs, ReviewerFindings]):
    """LLM-backed Step that produces security findings."""

    name = "security_review"
    input_model = ReviewerInputs
    output_model = ReviewerFindings
    depends_on = ("analyze_diff",)
    timeout_seconds = 90
    retry_policy = RetryPolicy.no_retry()  # LLM client handles its own retries.

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
            agent="security_reviewer",
            tools=shared_tools(),
            forced_tool_name=REPORT_FINDINGS_TOOL,
        )

        ctx.add_usage(
            tokens_in=response.input_tokens_total,
            tokens_out=response.tokens_out,
            cost_cents=response.cost_cents,
        )

        # reviewer is set by us (not the model); id and posted use their defaults.
        findings = [ReviewFinding(reviewer="security", **m.model_dump()) for m in parsed.findings]

        ctx.log.info(
            "security_reviewer.completed",
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

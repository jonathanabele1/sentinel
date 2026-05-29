"""Diff analyzer: categorise a PR's diff and surface risk hints.

Two things live here:

  DiffAnalysisInputs - what AnalyzeDiffStep sends to the LLM (a trimmed view
    of the PR's diff). Snapshotted to step_executions.inputs.

  AnalyzeDiffStep - the Step that wires inputs to output. Reads FetchDiffStep's
    output via the context, builds inputs with token-budget trimming, calls
    Claude via complete_structured using the SHARED, cache-marked prefix, and
    returns the typed DiffAnalysis. Because it runs first in the plan (alone in
    its DAG round) and sends the shared prefix, its call writes the diff into
    Anthropic's prompt cache; the reviewers that fan out in the next round read
    it back. See docs/design-decisions.md (ADR-001).

DiffAnalysis itself lives in packages.core.models.domain because it crosses
several boundaries (analyzer output, reviewer input, webhook rendering) and the
shared-prefix builder needs it without importing this module.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from packages.core.agents._utils import trim_diff
from packages.core.agents.shared_prefix import (
    DIFF_ANALYSIS_TOOL,
    MAX_PREFIX_DIFF_CHARS,
    SHARED_PREFIX_VERSION,
    SHARED_SYSTEM,
    render_context_block,
    shared_tools,
)
from packages.core.github.diff import ChangedFile, PullRequestDiff
from packages.core.llm import LLMClient, complete_structured
from packages.core.models.domain import DiffAnalysis
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.step import RetryPolicy, Step


class DiffAnalysisInputs(BaseModel):
    """Inputs to the diff analyzer.

    The unified_diff is already trimmed to the shared char budget. Files come
    pre-summarised (path + status + sizes) so the model can quickly scan what's
    in the PR without re-parsing the diff text. Snapshotted to
    step_executions.inputs for replay.
    """

    repo_full_name: str
    pr_number: int
    head_sha: str
    unified_diff: str
    files: list[ChangedFile]
    # Paths that changed in the PR but were filtered out of the diff by repo
    # policy (lockfiles, generated files). Names only; surfaced to the model so
    # it knows those files exist and were updated.
    ignored_files: list[str] = Field(default_factory=list)
    truncated_files: bool = False
    truncated_diff: bool = False


# PROMPT_VERSION versions this agent's task tail (the text after the shared,
# cached context block). The shared prefix is versioned separately as
# SHARED_PREFIX_VERSION; bump that when SHARED_SYSTEM, the tools, or the
# context-block format change.
PROMPT_VERSION = "v1"

# The analyzer's task instruction. It follows the shared context block and is
# NOT part of the cached prefix, so it can differ from the reviewers' tails
# without affecting the cache.
ANALYZER_TASK = """\
Your task: produce a high-level analysis of this pull request. Summarise what \
it changes in 2-4 sentences, list which categories of file it touches, note \
any risks a human reviewer should know about (missing tests, new external \
dependencies, schema changes, secrets, scope creep), and identify the primary \
language. An empty risk list is fine for low-risk PRs; do not invent risks.

Call the `diff_analysis` tool with your structured analysis.
"""

# Alias of the shared budget so existing imports (and tests) keep working; the
# single source of truth is shared_prefix.MAX_PREFIX_DIFF_CHARS.
MAX_DIFF_CHARS = MAX_PREFIX_DIFF_CHARS


class AnalyzeDiffStep(Step[DiffAnalysisInputs, DiffAnalysis]):
    """Calls Claude to produce a typed DiffAnalysis from a fetched diff."""

    name = "analyze_diff"
    input_model = DiffAnalysisInputs
    output_model = DiffAnalysis
    timeout_seconds = 90
    # LLMClient handles its own per-call retries; one Step-level attempt is
    # enough. If structured-output validation exhausts its retries inside the
    # LLM client, the engine shouldn't try the whole step again.
    retry_policy = RetryPolicy.no_retry()

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        model: str = "claude-sonnet-4-5",
    ) -> None:
        self._llm = llm_client
        self._model = model

    def build_inputs(self, ctx: StepContext) -> DiffAnalysisInputs:
        diff = ctx.get_output("fetch_diff", PullRequestDiff)
        trimmed_diff, truncated = trim_diff(diff.unified_diff, MAX_PREFIX_DIFF_CHARS)
        return DiffAnalysisInputs(
            repo_full_name=diff.repo_full_name,
            pr_number=diff.pr_number,
            head_sha=diff.head_sha,
            unified_diff=trimmed_diff,
            files=diff.files,
            ignored_files=diff.ignored_files,
            truncated_files=diff.truncated_files,
            truncated_diff=truncated,
        )

    async def execute(self, inputs: DiffAnalysisInputs, ctx: StepContext) -> DiffAnalysis:
        context_block = render_context_block(
            repo_full_name=inputs.repo_full_name,
            pr_number=inputs.pr_number,
            head_sha=inputs.head_sha,
            files=inputs.files,
            unified_diff=inputs.unified_diff,
            ignored_files=inputs.ignored_files,
            truncated_files=inputs.truncated_files,
            truncated_diff=inputs.truncated_diff,
        )
        messages = [
            {
                "role": "user",
                "content": [context_block, {"type": "text", "text": ANALYZER_TASK}],
            }
        ]

        parsed, response = await complete_structured(
            self._llm,
            model=self._model,
            schema=DiffAnalysis,
            messages=messages,
            system=SHARED_SYSTEM,
            agent="diff_analyzer",
            tools=shared_tools(),
            forced_tool_name=DIFF_ANALYSIS_TOOL,
        )

        ctx.add_usage(
            tokens_in=response.input_tokens_total,
            tokens_out=response.tokens_out,
            cost_cents=response.cost_cents,
        )

        ctx.log.info(
            "diff_analyzer.completed",
            tokens_in=response.tokens_in,
            cache_read_tokens=response.cache_read_tokens,
            cache_creation_tokens=response.cache_creation_tokens,
            tokens_out=response.tokens_out,
            cost_cents=response.cost_cents,
            prompt_version=PROMPT_VERSION,
            prefix_version=SHARED_PREFIX_VERSION,
            categories=parsed.categories,
            risk_hint_count=len(parsed.risk_hints),
            truncated_diff=inputs.truncated_diff,
        )
        return parsed

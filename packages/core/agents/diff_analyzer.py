"""Diff analyzer: categorise a PR's diff and surface risk hints.

Three things live here:

  DiffAnalysisInputs — what AnalyzeDiffStep sends to the LLM (a trimmed
    view of the PR's diff). Snapshotted to step_executions.inputs.

  DiffAnalysis — the structured output we force the model to produce.
    Snapshotted to step_executions.outputs. The Pydantic class doubles
    as the Anthropic tool's input_schema; the model's tool call IS the
    structured response.

  AnalyzeDiffStep — the Step that wires them together. Reads
    FetchDiffStep's output via the context, builds inputs with token-
    budget trimming, calls Claude via complete_structured, returns the
    typed DiffAnalysis.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from packages.core.github.diff import ChangedFile, PullRequestDiff
from packages.core.llm import LLMClient, complete_structured
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.step import RetryPolicy, Step

FileCategory = Literal[
    "source",
    "test",
    "config",
    "docs",
    "infra",
    "build",
    "ci",
    "data",
    "other",
]


class DiffAnalysisInputs(BaseModel):
    """Inputs to the diff analyzer.

    The unified_diff is included verbatim. Files come pre-summarised
    (path + status + sizes) so the model can quickly scan what's in
    the PR without re-parsing the diff text.

    Token-budget management: the analyzer step truncates unified_diff
    to a configured byte budget before building these inputs. Real
    production would use proper tokenization; for Week 3 a byte cap
    is good enough.
    """

    repo_full_name: str
    pr_number: int
    head_sha: str
    unified_diff: str
    files: list[ChangedFile]
    truncated_files: bool = False
    truncated_diff: bool = False


class DiffAnalysis(BaseModel):
    """Structured summary of what changed in a PR.

    Used both as the analyzer step's output schema (snapshotted to
    step_executions.outputs as JSONB) AND as the JSON schema given to
    the LLM via Anthropic tool use. The same Pydantic class enforces
    the shape on both sides of the LLM boundary.
    """

    summary: str = Field(
        description=(
            "One-paragraph plain-English description of what this PR changes. "
            "Aim for 2-4 sentences. Mention the touched components and the user-facing effect."
        ),
    )
    categories: list[FileCategory] = Field(
        description=(
            "Which buckets of file the PR touches. Pick all that apply. "
            "Examples: 'source' for application code, 'test' for tests, 'config' for "
            "configuration files, 'infra' for Dockerfiles/k8s/Terraform, 'ci' for "
            "GitHub Actions workflows, 'docs' for markdown, 'build' for pyproject/lockfiles."
        ),
        default_factory=list,
    )
    risk_hints: list[str] = Field(
        description=(
            "Short bullet-form notes about anything worth flagging to a human reviewer: "
            "missing tests, new external integrations, schema changes, secrets handling, "
            "scope creep, etc. Two to six items is a healthy size. Empty list is fine "
            "for low-risk PRs."
        ),
        default_factory=list,
    )
    file_count: int = Field(
        ge=0,
        description="Number of files touched (use the count from the files input).",
    )
    primary_language: str | None = Field(
        default=None,
        description=(
            "The dominant programming language in this PR, lowercased "
            "(e.g. 'python', 'typescript', 'go'). None if it's mixed or unclear."
        ),
    )


# --- Prompts ---
# PROMPT_VERSION is the cache-busting key. Bump it whenever you change the
# system prompt or user-template wording. Cached outputs (Week 6) include
# this in the cache key so stale outputs don't survive prompt changes.
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """\
You are Sentinel's diff analyzer. Your job is to read a GitHub pull request \
diff and produce a structured high-level analysis: a brief plain-English \
summary, which categories of file the PR touches, any risks worth flagging \
to a human reviewer, and the primary programming language.

You will respond by calling the `diff_analysis` tool. Do not respond with \
free-form text. The tool's schema describes what each field should contain.

Be concise. Reviewers will see your summary at the top of every PR; long \
summaries get skipped. Two to four sentences is the right length.

For risk_hints, focus on things a human reviewer should know about: missing \
tests for non-trivial changes, new external dependencies, schema changes, \
secrets or credentials, scope creep beyond the PR title. Empty list is fine \
for low-risk PRs. Don't invent risks to fill the list.
"""

USER_TEMPLATE = """\
Repository: {repo_full_name}
Pull request: #{pr_number}
Head SHA: {head_sha}

Files changed ({file_count}{files_truncated_note}):
{files_summary}

Unified diff{diff_truncated_note}:
```diff
{unified_diff}
```

Call the `diff_analysis` tool with your structured analysis.
"""


# --- Step ---


# Rough char budget for the unified diff inside the prompt. Anthropic's
# 200k-token context can hold ~750k chars; we leave room for the rest of
# the prompt + the response. Trims aggressively over this so we don't blow
# the budget on huge PRs.
MAX_DIFF_CHARS = 80_000


class AnalyzeDiffStep(Step[DiffAnalysisInputs, DiffAnalysis]):
    """Calls Claude to produce a typed DiffAnalysis from a fetched diff."""

    name = "analyze_diff"
    input_model = DiffAnalysisInputs
    output_model = DiffAnalysis
    timeout_seconds = 90
    # LLMClient handles its own per-call retries; one Step-level attempt
    # is enough. If the structured-output validation exhausts its retries
    # inside the LLM client, the engine shouldn't try the whole step again.
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
        trimmed_diff, truncated = _trim_diff(diff.unified_diff, MAX_DIFF_CHARS)
        return DiffAnalysisInputs(
            repo_full_name=diff.repo_full_name,
            pr_number=diff.pr_number,
            head_sha=diff.head_sha,
            unified_diff=trimmed_diff,
            files=diff.files,
            truncated_files=diff.truncated_files,
            truncated_diff=truncated,
        )

    async def execute(self, inputs: DiffAnalysisInputs, ctx: StepContext) -> DiffAnalysis:
        user_message = USER_TEMPLATE.format(
            repo_full_name=inputs.repo_full_name,
            pr_number=inputs.pr_number,
            head_sha=inputs.head_sha,
            file_count=len(inputs.files),
            files_truncated_note=(" (files list truncated)" if inputs.truncated_files else ""),
            files_summary=_format_files_summary(inputs.files),
            diff_truncated_note=(
                " (diff truncated to fit token budget)" if inputs.truncated_diff else ""
            ),
            unified_diff=inputs.unified_diff,
        )

        parsed, response = await complete_structured(
            self._llm,
            model=self._model,
            schema=DiffAnalysis,
            messages=[{"role": "user", "content": user_message}],
            system=SYSTEM_PROMPT,
            agent="diff_analyzer",
            tool_name="diff_analysis",
        )

        ctx.log.info(
            "diff_analyzer.completed",
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_cents=response.cost_cents,
            prompt_version=PROMPT_VERSION,
            categories=parsed.categories,
            risk_hint_count=len(parsed.risk_hints),
            truncated_diff=inputs.truncated_diff,
        )
        return parsed


# --- Helpers ---


def _trim_diff(diff_text: str, max_chars: int) -> tuple[str, bool]:
    """Return (possibly-truncated diff, truncated_flag).

    Strategy: keep the head, drop the tail with a marker. Most diffs are
    interesting at the top (the first few files); cutting the tail is the
    least bad way to fit a budget without proper tokenization.
    """
    if len(diff_text) <= max_chars:
        return diff_text, False
    marker = (
        f"\n\n[diff truncated by Sentinel: showing first {max_chars} of {len(diff_text)} chars]"
    )
    return diff_text[: max_chars - len(marker)] + marker, True


def _format_files_summary(files: list[ChangedFile]) -> str:
    """Render the per-file metadata as a compact bullet list for the prompt."""
    if not files:
        return "  (none)"
    lines = []
    for f in files:
        lines.append(f"  - {f.path} [{f.status}, +{f.additions}/-{f.deletions}]")
    return "\n".join(lines)

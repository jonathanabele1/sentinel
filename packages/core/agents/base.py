"""Shared scaffolding for specialist reviewer agents.

Every specialist reviewer (security, correctness, testing) takes the same
inputs (a fetched PR diff + the upstream diff analysis) and returns the same
shape of output (ReviewerFindings). They also send an identical, cache-marked
request prefix (see shared_prefix.py): the same tools, the same system
preamble, and the same context block. Only the task tail differs - each
reviewer supplies its specialist instructions, which are appended AFTER the
cached context block so they do not affect the cache.

This file centralises the reviewer-specific parts that do not vary:
  - ReviewerInputs: the typed Pydantic input model (snapshotted for replay).
  - build_reviewer_inputs: reads fetch_diff + analyze_diff out of the
    StepContext, trims the diff to the SHARED budget, returns ReviewerInputs.
  - build_reviewer_messages: assembles [cached context block, uncached tail],
    where the tail carries the upstream analysis plus the reviewer's own
    specialist instructions.

Reviewer files (security_reviewer.py etc.) supply only their specialist
instructions, their reviewer label, and the Step subclass that wires them up.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from packages.core.agents._utils import trim_diff
from packages.core.agents.shared_prefix import (
    MAX_PREFIX_DIFF_CHARS,
    REPORT_FINDINGS_TOOL,
    render_context_block,
)
from packages.core.github.diff import ChangedFile, PullRequestDiff
from packages.core.models.domain import DiffAnalysis, FileCategory
from packages.core.orchestrator.context import StepContext


class ReviewerInputs(BaseModel):
    """Inputs every specialist reviewer takes.

    Snapshotted to step_executions.inputs JSONB. The analysis fields
    (analysis_summary, analysis_categories) come from the upstream analyze_diff
    step and give the reviewer high-level context before it dives into the diff
    lines. The unified_diff is already trimmed to the shared char budget so it
    matches the analyzer's diff byte-for-byte (which is what lets the cache
    hit).
    """

    repo_full_name: str
    pr_number: int
    head_sha: str
    unified_diff: str
    files: list[ChangedFile]
    analysis_summary: str
    analysis_categories: list[FileCategory]
    # Paths changed in the PR but filtered out of the diff by repo policy
    # (lockfiles, generated files). Names only; identical to the analyzer's so
    # the cached context block stays byte-for-byte the same.
    ignored_files: list[str] = Field(default_factory=list)
    truncated_diff: bool = False
    truncated_files: bool = False


_REVIEWER_TAIL_TEMPLATE = """\
Upstream diff analysis:
{analysis_summary}

Categories: {categories}

{specialist_instructions}

Call the `{tool_name}` tool with your findings."""


def build_reviewer_inputs(ctx: StepContext) -> ReviewerInputs:
    """Pull fetch_diff + analyze_diff outputs and assemble ReviewerInputs.

    Used by all three reviewer steps. Trims the diff to the SAME budget the
    analyzer uses (MAX_PREFIX_DIFF_CHARS) so the cached context block is
    byte-identical and the cache hits.
    """
    diff = ctx.get_output("fetch_diff", PullRequestDiff)
    analysis = ctx.get_output("analyze_diff", DiffAnalysis)
    trimmed_diff, truncated_diff = trim_diff(diff.unified_diff, MAX_PREFIX_DIFF_CHARS)
    return ReviewerInputs(
        repo_full_name=diff.repo_full_name,
        pr_number=diff.pr_number,
        head_sha=diff.head_sha,
        unified_diff=trimmed_diff,
        files=diff.files,
        analysis_summary=analysis.summary,
        analysis_categories=analysis.categories,
        ignored_files=diff.ignored_files,
        truncated_diff=truncated_diff,
        truncated_files=diff.truncated_files,
    )


def build_reviewer_messages(
    inputs: ReviewerInputs,
    *,
    specialist_instructions: str,
) -> list[dict[str, Any]]:
    """Build the reviewer's user turn: cached context block + uncached tail.

    The context block is identical to the analyzer's (and the other
    reviewers'), so it reads from the warm cache. The tail carries the upstream
    analysis and this reviewer's specialist instructions, and is not cached.
    """
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
    tail = _REVIEWER_TAIL_TEMPLATE.format(
        analysis_summary=inputs.analysis_summary,
        categories=", ".join(f"`{c}`" for c in inputs.analysis_categories) or "(none)",
        specialist_instructions=specialist_instructions,
        tool_name=REPORT_FINDINGS_TOOL,
    )
    return [
        {
            "role": "user",
            "content": [context_block, {"type": "text", "text": tail}],
        }
    ]

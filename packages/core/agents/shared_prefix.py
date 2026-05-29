"""Shared, cacheable request prefix for the LLM agents.

All four LLM-backed agents (the diff analyzer and the three specialist
reviewers) send the same prefix to Claude: the same tool declarations, the
same system preamble, and the same context block (repo metadata + file
breakdown + unified diff). Only the task that follows the context block
differs per agent.

Keeping that prefix byte-identical is what lets Anthropic prompt caching pay
off: `analyze_diff` runs first in the plan and writes the prefix to the cache,
and the three reviewers (which run after it, in parallel) read it back instead
of re-paying for the diff. See docs/design-decisions.md (ADR-001).

The single source of truth for the prefix lives here so the analyzer and
reviewers cannot drift apart and silently break the cache. A unit test asserts
the rendered context block is identical across agents.
"""

from __future__ import annotations

from typing import Any

from packages.core.agents._utils import format_files_summary
from packages.core.github.diff import ChangedFile
from packages.core.llm.structured import schema_to_tool
from packages.core.models.domain import DiffAnalysis, ModelFindings

# Bump when any part of the shared prefix changes (SHARED_SYSTEM, the tool
# definitions, or the context-block format). Because the prefix is shared,
# this version is shared too: changing it invalidates the cache for all four
# agents at once, which is the intended behaviour. Do not version the shared
# prefix per-agent.
SHARED_PREFIX_VERSION = "v2"

# One char budget for the diff across every agent. It MUST be the same for the
# analyzer and the reviewers, or their context blocks differ and the cache
# never hits. ~80k chars leaves headroom under Claude's context window.
MAX_PREFIX_DIFF_CHARS = 80_000

# Tool names. The three reviewers share a single findings tool: the
# security/correctness/testing label is stamped in Python after parsing, so the
# tool name carries no meaning, and unifying it lets all reviewers (plus the
# analyzer, which declares it but does not use it) share one tool block.
DIFF_ANALYSIS_TOOL = "diff_analysis"
REPORT_FINDINGS_TOOL = "report_findings"

SHARED_SYSTEM = """\
You are a component of Sentinel, an automated GitHub pull request review \
system. You will be given a pull request's file breakdown and unified diff, \
followed by a specific task. Always respond by calling the tool named in your \
task; never respond with free-form text.

Operating principles that apply to every task:
  - Precision over recall. An empty result is the correct answer when nothing \
in your task applies. Reviewers ignore noisy bots.
  - Calibrated confidence where the task asks for it: a 0.9-confidence \
judgement should be correct about 90% of the time.
  - Be specific and actionable, and quote the exact code as evidence.
"""

_CONTEXT_TEMPLATE = """\
Repository: {repo_full_name}
Pull request: #{pr_number}
Head SHA: {head_sha}

Files changed ({file_count}{files_truncated_note}):
{files_summary}
{excluded_block}
Unified diff{diff_truncated_note}:
```diff
{unified_diff}
```"""


def shared_tools() -> list[dict[str, Any]]:
    """The tool declarations every agent sends, in a fixed order.

    Both tools appear in every call so the prefix is identical across agents;
    `tool_choice` (set per agent) forces the one each actually uses. The
    definitions are deterministic functions of the Pydantic schemas, so the
    rendered JSON is byte-identical call to call.
    """
    return [
        schema_to_tool(DiffAnalysis, tool_name=DIFF_ANALYSIS_TOOL),
        schema_to_tool(ModelFindings, tool_name=REPORT_FINDINGS_TOOL),
    ]


def render_context_block(
    *,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    files: list[ChangedFile],
    unified_diff: str,
    ignored_files: list[str],
    truncated_files: bool,
    truncated_diff: bool,
) -> dict[str, Any]:
    """Build the cached context block: repo metadata + files + diff.

    Returns a single Anthropic text content block carrying a cache_control
    breakpoint. Everything before it in the request (tools, then system) plus
    this block forms the cached prefix. The text MUST be identical across the
    analyzer and the reviewers; this is the one function that produces it, so
    they cannot drift. `unified_diff` is expected to be already trimmed to
    MAX_PREFIX_DIFF_CHARS by the caller's build_inputs.

    `ignored_files` are paths that changed in the PR but were filtered out of
    the diff by repo policy (lockfiles, generated files). Listing their names
    (not their content) tells the model those files exist and were updated, so
    it does not falsely flag them as missing, and can still reason about them
    (e.g. "dependency added but the lockfile is NOT among the changed files").
    """
    excluded_block = ""
    if ignored_files:
        excluded_block = (
            "\nExcluded from this diff by repo policy (changed in the PR but not shown "
            "for review; e.g. lockfiles, generated files): " + ", ".join(ignored_files) + "\n"
        )
    text = _CONTEXT_TEMPLATE.format(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        file_count=len(files),
        files_truncated_note=" (files list truncated)" if truncated_files else "",
        files_summary=format_files_summary(files),
        excluded_block=excluded_block,
        diff_truncated_note=" (diff truncated to fit token budget)" if truncated_diff else "",
        unified_diff=unified_diff,
    )
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }

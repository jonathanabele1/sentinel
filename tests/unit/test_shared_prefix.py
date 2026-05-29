"""Unit tests for the shared, cacheable agent prefix.

The whole prompt-caching scheme (ADR-001) depends on the analyzer and the
three reviewers sending a byte-identical cached prefix: the same tools, the
same system, and the same context block built from the same diff. These tests
are the guard against that prefix silently drifting (which would degrade the
cache to writes-only without any visible failure).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import structlog
from packages.core.agents.base import build_reviewer_inputs, build_reviewer_messages
from packages.core.agents.diff_analyzer import AnalyzeDiffStep
from packages.core.agents.shared_prefix import (
    DIFF_ANALYSIS_TOOL,
    MAX_PREFIX_DIFF_CHARS,
    REPORT_FINDINGS_TOOL,
    render_context_block,
    shared_tools,
)
from packages.core.github.diff import ChangedFile, PullRequestDiff
from packages.core.models.db import ReviewRun
from packages.core.models.domain import DiffAnalysis
from packages.core.orchestrator.context import StepContext


def _ctx_with_diff(unified_diff: str, ignored_files: list[str] | None = None) -> StepContext:
    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url="https://github.com/acme/api/pull/7",
        repo_full_name="acme/api",
        pr_number=7,
        head_sha="abc123",
        installation_id=12345,
        plan_name="default",
        status="running",
    )
    ctx = StepContext(run=run, session=MagicMock(), log=structlog.get_logger())
    ctx.outputs["fetch_diff"] = PullRequestDiff(
        repo_full_name="acme/api",
        pr_number=7,
        head_sha="abc123",
        base_sha="def456",
        unified_diff=unified_diff,
        files=[
            ChangedFile(path="src/foo.py", status="modified", additions=3, deletions=1, changes=4),
            ChangedFile(path="src/bar.py", status="added", additions=20, deletions=0, changes=20),
        ],
        ignored_files=ignored_files or [],
    )
    return ctx


def _context_block_for_each_agent(
    unified_diff: str, ignored_files: list[str] | None = None
) -> tuple[dict, dict]:
    """Build the context block the way the analyzer and a reviewer would.

    Returns (analyzer_block, reviewer_block). They must be equal.
    """
    ctx = _ctx_with_diff(unified_diff, ignored_files)

    analyzer_inputs = AnalyzeDiffStep(llm_client=MagicMock()).build_inputs(ctx)
    analyzer_block = render_context_block(
        repo_full_name=analyzer_inputs.repo_full_name,
        pr_number=analyzer_inputs.pr_number,
        head_sha=analyzer_inputs.head_sha,
        files=analyzer_inputs.files,
        unified_diff=analyzer_inputs.unified_diff,
        ignored_files=analyzer_inputs.ignored_files,
        truncated_files=analyzer_inputs.truncated_files,
        truncated_diff=analyzer_inputs.truncated_diff,
    )

    # Reviewers also need analyze_diff's output in context.
    ctx.outputs["analyze_diff"] = DiffAnalysis(
        summary="Touches foo and bar.",
        categories=["source"],
        risk_hints=[],
        file_count=2,
    )
    reviewer_inputs = build_reviewer_inputs(ctx)
    reviewer_block = render_context_block(
        repo_full_name=reviewer_inputs.repo_full_name,
        pr_number=reviewer_inputs.pr_number,
        head_sha=reviewer_inputs.head_sha,
        files=reviewer_inputs.files,
        unified_diff=reviewer_inputs.unified_diff,
        ignored_files=reviewer_inputs.ignored_files,
        truncated_files=reviewer_inputs.truncated_files,
        truncated_diff=reviewer_inputs.truncated_diff,
    )
    return analyzer_block, reviewer_block


def test_context_block_identical_for_small_diff() -> None:
    analyzer_block, reviewer_block = _context_block_for_each_agent(
        "diff --git a/x b/x\n+one line\n"
    )
    assert analyzer_block == reviewer_block
    assert analyzer_block["cache_control"] == {"type": "ephemeral"}


def test_context_block_identical_when_diff_is_truncated() -> None:
    # A diff larger than the shared budget must be trimmed identically by both
    # agents, or the cached prefix diverges exactly on the big PRs caching is
    # supposed to help most.
    huge_diff = "x" * (MAX_PREFIX_DIFF_CHARS + 5_000)
    analyzer_block, reviewer_block = _context_block_for_each_agent(huge_diff)
    assert analyzer_block == reviewer_block
    assert "truncated" in analyzer_block["text"].lower()


def test_excluded_files_are_surfaced_and_identical_across_agents() -> None:
    # A changed-but-filtered lockfile should be named in the context block so
    # the model knows it exists (and was updated) rather than falsely flagging
    # it as missing. Both agents must still render the same block.
    analyzer_block, reviewer_block = _context_block_for_each_agent(
        "diff --git a/pyproject.toml b/pyproject.toml\n+fastapi\n",
        ignored_files=["uv.lock"],
    )
    assert analyzer_block == reviewer_block
    assert "uv.lock" in analyzer_block["text"]
    assert "Excluded from this diff" in analyzer_block["text"]


def test_no_excluded_section_when_nothing_filtered() -> None:
    analyzer_block, _ = _context_block_for_each_agent("diff --git a/x b/x\n+one\n")
    assert "Excluded from this diff" not in analyzer_block["text"]


def test_shared_tools_are_deterministic_and_ordered() -> None:
    first = shared_tools()
    second = shared_tools()
    assert first == second  # byte-identical across calls
    assert [t["name"] for t in first] == [DIFF_ANALYSIS_TOOL, REPORT_FINDINGS_TOOL]


def test_reviewer_tail_is_separate_uncached_block() -> None:
    # The reviewer's user turn must be [cached context block, uncached tail],
    # and only the first block carries cache_control.
    ctx = _ctx_with_diff("diff --git a/x b/x\n+one\n")
    ctx.outputs["analyze_diff"] = DiffAnalysis(
        summary="s", categories=["source"], risk_hints=[], file_count=2
    )
    inputs = build_reviewer_inputs(ctx)
    messages = build_reviewer_messages(inputs, specialist_instructions="FOCUS ON X.")

    assert len(messages) == 1
    content = messages[0]["content"]
    assert len(content) == 2
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in content[1]
    assert "FOCUS ON X." in content[1]["text"]
    assert f"`{REPORT_FINDINGS_TOOL}`" in content[1]["text"]

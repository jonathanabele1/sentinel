"""Unit tests for the diff analyzer agent."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
import structlog
from packages.core.agents.diff_analyzer import (
    MAX_DIFF_CHARS,
    AnalyzeDiffStep,
    DiffAnalysis,
    DiffAnalysisInputs,
    _format_files_summary,
    _trim_diff,
)
from packages.core.github.diff import ChangedFile, PullRequestDiff
from packages.core.models.db import ReviewRun
from packages.core.orchestrator.context import StepContext

# --- Helper tests ---


def test_trim_diff_passes_short_diff_through() -> None:
    diff = "diff --git a/foo b/foo\n+1 line\n"
    trimmed, truncated = _trim_diff(diff, max_chars=1000)
    assert trimmed == diff
    assert truncated is False


def test_trim_diff_truncates_long_diff() -> None:
    diff = "x" * 200_000
    trimmed, truncated = _trim_diff(diff, max_chars=1000)
    assert truncated is True
    assert len(trimmed) <= 1000
    assert "truncated" in trimmed.lower()


def test_trim_diff_at_exact_boundary() -> None:
    diff = "x" * 1000
    trimmed, truncated = _trim_diff(diff, max_chars=1000)
    assert truncated is False
    assert trimmed == diff


def test_format_files_summary_empty_list() -> None:
    assert _format_files_summary([]).strip() == "(none)"


def test_format_files_summary_includes_each_file() -> None:
    files = [
        ChangedFile(path="a.py", status="added", additions=10, deletions=0, changes=10),
        ChangedFile(path="b.py", status="modified", additions=3, deletions=5, changes=8),
    ]
    out = _format_files_summary(files)
    assert "a.py" in out
    assert "b.py" in out
    assert "+10/-0" in out
    assert "+3/-5" in out
    assert "[added" in out
    assert "[modified" in out


# --- build_inputs tests ---


def _make_ctx_with_diff(unified_diff: str = "diff --git ...") -> StepContext:
    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url="https://github.com/acme/api/pull/1",
        repo_full_name="acme/api",
        pr_number=1,
        head_sha="abc123",
        installation_id=12345,
        plan_name="default",
        status="running",
    )
    ctx = StepContext(run=run, session=MagicMock(), log=structlog.get_logger())
    ctx.outputs["fetch_diff"] = PullRequestDiff(
        repo_full_name="acme/api",
        pr_number=1,
        head_sha="abc123",
        base_sha="def456",
        unified_diff=unified_diff,
        files=[
            ChangedFile(
                path="src/foo.py",
                status="modified",
                additions=3,
                deletions=1,
                changes=4,
            )
        ],
    )
    return ctx


def test_build_inputs_reads_from_fetch_diff_output() -> None:
    step = AnalyzeDiffStep(llm_client=MagicMock())
    ctx = _make_ctx_with_diff(unified_diff="diff --git a/x b/x")

    inputs = step.build_inputs(ctx)

    assert isinstance(inputs, DiffAnalysisInputs)
    assert inputs.repo_full_name == "acme/api"
    assert inputs.pr_number == 1
    assert inputs.head_sha == "abc123"
    assert inputs.unified_diff == "diff --git a/x b/x"
    assert inputs.truncated_diff is False
    assert len(inputs.files) == 1


def test_build_inputs_flags_truncated_diff_for_large_input() -> None:
    step = AnalyzeDiffStep(llm_client=MagicMock())
    huge_diff = "x" * (MAX_DIFF_CHARS + 1000)
    ctx = _make_ctx_with_diff(unified_diff=huge_diff)

    inputs = step.build_inputs(ctx)
    assert inputs.truncated_diff is True
    assert len(inputs.unified_diff) <= MAX_DIFF_CHARS


def test_build_inputs_raises_when_fetch_diff_output_missing() -> None:
    step = AnalyzeDiffStep(llm_client=MagicMock())
    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url="...",
        repo_full_name="acme/api",
        pr_number=1,
        head_sha="abc123",
        installation_id=1,
        plan_name="default",
        status="running",
    )
    empty_ctx = StepContext(run=run, session=MagicMock(), log=structlog.get_logger())

    with pytest.raises(KeyError, match="fetch_diff"):
        step.build_inputs(empty_ctx)


# --- DiffAnalysis schema sanity ---


def test_diff_analysis_validates_categories() -> None:
    """Categories must be from the closed enum."""
    from pydantic import ValidationError

    DiffAnalysis(
        summary="ok",
        categories=["source", "test"],
        risk_hints=[],
        file_count=2,
    )

    with pytest.raises(ValidationError):
        DiffAnalysis(
            summary="ok",
            categories=["random_category"],  # type: ignore[list-item]
            risk_hints=[],
            file_count=2,
        )


def test_diff_analysis_rejects_negative_file_count() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DiffAnalysis(summary="ok", categories=[], risk_hints=[], file_count=-1)


def test_class_level_attributes() -> None:
    assert AnalyzeDiffStep.name == "analyze_diff"
    assert AnalyzeDiffStep.input_model is DiffAnalysisInputs
    assert AnalyzeDiffStep.output_model is DiffAnalysis
    # Retries are off because LLMClient + structured retries cover it.
    assert AnalyzeDiffStep.retry_policy.max_attempts == 1

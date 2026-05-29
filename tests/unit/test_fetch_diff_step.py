"""Unit tests for FetchDiffStep."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from packages.core.github.diff import ChangedFile, PullRequestDiff
from packages.core.models.db import ReviewRun
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.steps.fetch_diff import FetchDiffStep


def _make_ctx(installation_id: int | None = 12345) -> StepContext:
    run = ReviewRun(
        id=uuid.uuid4(),
        pr_url="https://github.com/acme/api/pull/1",
        repo_full_name="acme/api",
        pr_number=1,
        head_sha="abc123",
        installation_id=installation_id,
        plan_name="default",
        status="pending",
    )
    return StepContext(run=run, session=MagicMock(), log=structlog.get_logger())


def test_build_inputs_pulls_from_run() -> None:
    step = FetchDiffStep(diff_client=MagicMock())
    ctx = _make_ctx(installation_id=99)

    inputs = step.build_inputs(ctx)

    assert inputs.repo_full_name == "acme/api"
    assert inputs.pr_number == 1
    assert inputs.head_sha == "abc123"
    assert inputs.installation_id == 99


def test_build_inputs_raises_when_installation_id_missing() -> None:
    step = FetchDiffStep(diff_client=MagicMock())
    ctx = _make_ctx(installation_id=None)

    with pytest.raises(ValueError, match="missing installation_id"):
        step.build_inputs(ctx)


async def test_execute_delegates_to_diff_client() -> None:
    """execute() forwards the typed inputs to the diff client and returns the
    (ignore-filtered) result. With no ignored files the content is unchanged."""
    fake_diff = PullRequestDiff(
        repo_full_name="acme/api",
        pr_number=1,
        head_sha="abc123",
        base_sha="def456",
        unified_diff="diff --git a/src/foo.py b/src/foo.py\n@@ ...\n+code\n",
        files=[
            ChangedFile(
                path="src/foo.py",
                status="modified",
                additions=3,
                deletions=1,
                changes=4,
                patch="@@ ...",
            )
        ],
    )

    diff_client = MagicMock()
    diff_client.get_pr_diff = AsyncMock(return_value=fake_diff)
    step = FetchDiffStep(diff_client=diff_client)
    ctx = _make_ctx()
    inputs = step.build_inputs(ctx)

    result = await step.execute(inputs, ctx)

    # src/foo.py isn't an ignored path, so nothing is filtered out.
    assert result.unified_diff == fake_diff.unified_diff
    assert [f.path for f in result.files] == ["src/foo.py"]
    assert result.ignored_files == []
    diff_client.get_pr_diff.assert_awaited_once_with(
        installation_id=12345,
        repo_full_name="acme/api",
        pr_number=1,
    )


async def test_execute_filters_lockfiles() -> None:
    """A uv.lock file is dropped from both the diff and the files list."""
    unified = (
        "diff --git a/main.py b/main.py\n@@ ...\n+code\n"
        "diff --git a/uv.lock b/uv.lock\n@@ ...\n+thousands of lines\n"
    )
    fake_diff = PullRequestDiff(
        repo_full_name="acme/api",
        pr_number=1,
        head_sha="abc123",
        base_sha="def456",
        unified_diff=unified,
        files=[
            ChangedFile(path="main.py", status="modified", additions=1, deletions=0, changes=1),
            ChangedFile(path="uv.lock", status="modified", additions=900, deletions=0, changes=900),
        ],
    )

    diff_client = MagicMock()
    diff_client.get_pr_diff = AsyncMock(return_value=fake_diff)
    step = FetchDiffStep(diff_client=diff_client)
    ctx = _make_ctx()
    result = await step.execute(step.build_inputs(ctx), ctx)

    assert "uv.lock" not in result.unified_diff
    assert "main.py" in result.unified_diff
    assert [f.path for f in result.files] == ["main.py"]
    assert result.ignored_files == ["uv.lock"]


def test_class_level_attributes() -> None:
    assert FetchDiffStep.name == "fetch_diff"
    assert FetchDiffStep.timeout_seconds == 30

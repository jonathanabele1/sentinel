"""FetchDiffStep: pull the PR's unified diff from GitHub.

First non-noop step in the default plan. Takes the run's repo + PR number +
installation_id from the context, calls GitHubDiffClient, returns the typed
PullRequestDiff which the engine snapshots to step_executions.outputs as JSONB.

The unified diff and per-file metadata are then available to downstream
steps via ctx.get_output("fetch_diff", PullRequestDiff). AnalyzeDiffStep
is the first consumer.

Implementation note: this step takes the GitHubDiffClient via __init__
rather than reaching for a global. Steps that need external dependencies
follow this pattern; the Plan composes pre-configured Step instances.
"""

from __future__ import annotations

from pydantic import BaseModel

from packages.core.github.diff import GitHubDiffClient, PullRequestDiff
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.step import RetryPolicy, Step
from packages.core.policy import (
    DEFAULT_IGNORE_PATTERNS,
    filter_unified_diff,
    path_is_ignored,
)


class FetchDiffInputs(BaseModel):
    """Inputs snapshotted to step_executions.inputs JSONB.

    Carries the identity of the PR to fetch. installation_id is needed
    so the diff client can mint the right per-installation access token.
    """

    repo_full_name: str
    pr_number: int
    head_sha: str
    installation_id: int


class FetchDiffStep(Step[FetchDiffInputs, PullRequestDiff]):
    """Pulls the unified diff and per-file metadata for a PR."""

    name = "fetch_diff"
    input_model = FetchDiffInputs
    output_model = PullRequestDiff
    timeout_seconds = 30
    # GitHub's API is usually fast and reliable. 2 attempts is enough; 5xx
    # storms are rare and we'd rather fail fast than dogpile on retries.
    retry_policy = RetryPolicy(
        max_attempts=2,
        initial_backoff_seconds=1.0,
        backoff_multiplier=2.0,
        max_backoff_seconds=5.0,
        jitter_seconds=0.5,
    )

    def __init__(
        self,
        *,
        diff_client: GitHubDiffClient,
        ignore_patterns: list[str] | tuple[str, ...] = DEFAULT_IGNORE_PATTERNS,
    ) -> None:
        self._diff_client = diff_client
        self._ignore_patterns = tuple(ignore_patterns)

    def build_inputs(self, ctx: StepContext) -> FetchDiffInputs:
        if ctx.run.installation_id is None:
            raise ValueError(
                "ReviewRun is missing installation_id; the webhook handler "
                "must set it before running the engine for steps that call the GitHub API."
            )
        return FetchDiffInputs(
            repo_full_name=ctx.run.repo_full_name,
            pr_number=ctx.run.pr_number,
            head_sha=ctx.run.head_sha,
            installation_id=ctx.run.installation_id,
        )

    async def execute(self, inputs: FetchDiffInputs, ctx: StepContext) -> PullRequestDiff:
        diff = await self._diff_client.get_pr_diff(
            installation_id=inputs.installation_id,
            repo_full_name=inputs.repo_full_name,
            pr_number=inputs.pr_number,
        )

        # Drop generated/lock/vendored files BEFORE the diff reaches any LLM.
        # This is the biggest cost lever: a single uv.lock can be 100k+ chars.
        filtered_diff, dropped = filter_unified_diff(diff.unified_diff, self._ignore_patterns)
        kept_files = [f for f in diff.files if not path_is_ignored(f.path, self._ignore_patterns)]

        if dropped:
            ctx.log.info(
                "fetch_diff.ignored_files",
                count=len(dropped),
                files=dropped,
                bytes_removed=len(diff.unified_diff) - len(filtered_diff),
            )

        return diff.model_copy(
            update={
                "unified_diff": filtered_diff,
                "files": kept_files,
                "ignored_files": dropped,
            }
        )

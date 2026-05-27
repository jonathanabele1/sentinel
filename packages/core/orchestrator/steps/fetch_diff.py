"""FetchDiffStep: pull the PR's unified diff from GitHub.

First non-noop step in the default plan. Takes the run's repo + PR number +
installation_id from the context, calls GitHubDiffClient, returns the typed
PullRequestDiff which the engine snapshots to step_executions.outputs as JSONB.

The unified diff and per-file metadata are then available to downstream
steps via ctx.get_output("fetch_diff", PullRequestDiff). AnalyzeDiffStep
(chunk 5) is the first consumer.

Implementation note: this step takes the GitHubDiffClient via __init__
rather than reaching for a global. Steps that need external dependencies
follow this pattern; the Plan composes pre-configured Step instances.
"""

from __future__ import annotations

from pydantic import BaseModel

from packages.core.github.diff import GitHubDiffClient, PullRequestDiff
from packages.core.orchestrator.context import StepContext
from packages.core.orchestrator.step import RetryPolicy, Step


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

    def __init__(self, *, diff_client: GitHubDiffClient) -> None:
        self._diff_client = diff_client

    def build_inputs(self, ctx: StepContext) -> FetchDiffInputs:
        if ctx.run.installation_id is None:
            raise ValueError(
                "ReviewRun is missing installation_id; the webhook handler "
                "must set it before running the engine for Week 3+ steps."
            )
        return FetchDiffInputs(
            repo_full_name=ctx.run.repo_full_name,
            pr_number=ctx.run.pr_number,
            head_sha=ctx.run.head_sha,
            installation_id=ctx.run.installation_id,
        )

    async def execute(self, inputs: FetchDiffInputs, ctx: StepContext) -> PullRequestDiff:
        return await self._diff_client.get_pr_diff(
            installation_id=inputs.installation_id,
            repo_full_name=inputs.repo_full_name,
            pr_number=inputs.pr_number,
        )

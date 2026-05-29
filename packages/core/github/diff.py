"""Fetch PR diffs from the GitHub API.

The diff fetcher is split into two responsibilities:
  1. Authentication, via the existing GitHubAppClient (JWT → installation token).
  2. Diff retrieval, via two endpoints:
       - GET /repos/{owner}/{repo}/pulls/{number}  with Accept: ...diff
            → the unified diff as text
       - GET /repos/{owner}/{repo}/pulls/{number}/files
            → structured per-file metadata (additions/deletions/patch)

Both endpoints are needed because each gives information the other doesn't:
  - The diff text is what we feed to the LLM.
  - The files endpoint gives us additions/deletions counts, file status
    (added/modified/removed/renamed), and the per-file patch hunks broken out.

Returned models are Pydantic so they snapshot cleanly to JSONB in step_executions.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from packages.core.github.app import GITHUB_API, GitHubAppClient
from packages.core.observability.logging import get_logger

_log = get_logger(__name__)

# Conservative cap on how many files we'll inspect per PR. Real production
# would push back on PRs touching > 200 files; for now we just truncate
# and log so the agent doesn't drown in noise.
MAX_FILES_PER_PR = 100


class ChangedFile(BaseModel):
    """One file's worth of change metadata from the PR files endpoint.

    `patch` is the diff hunks for this specific file (sometimes None for
    very large or binary files). Useful when the agent wants per-file
    reasoning rather than parsing the unified diff itself.
    """

    path: str
    status: Literal["added", "modified", "removed", "renamed", "copied", "changed", "unchanged"]
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    patch: str | None = None
    previous_filename: str | None = None


class PullRequestDiff(BaseModel):
    """The full diff payload for a PR. Snapshotted to step_executions.outputs.

    Stored entirely as JSONB; large diffs will produce large rows. We don't
    truncate the unified_diff here because the LLM agent will do its own
    token-budget management on the way into the prompt.
    """

    repo_full_name: str
    pr_number: int
    head_sha: str
    base_sha: str
    unified_diff: str
    files: list[ChangedFile]
    truncated_files: bool = Field(
        default=False,
        description="True if the files list was truncated at MAX_FILES_PER_PR.",
    )
    ignored_files: list[str] = Field(
        default_factory=list,
        description=(
            "Paths excluded from the diff by ignore patterns (lockfiles, "
            "generated files, etc.). Recorded so the summary can report what "
            "was skipped; these never reach the LLM reviewers."
        ),
    )


class GitHubDiffClient:
    """Fetches PR diffs using a GitHubAppClient for authentication.

    Construct once per process; safe to share across requests. The
    underlying httpx client is reused; tokens come from the App client's
    cache so we don't re-mint JWTs for every diff request.
    """

    def __init__(
        self,
        *,
        app_client: GitHubAppClient,
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._app = app_client
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)

    async def get_pr_diff(
        self,
        *,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
    ) -> PullRequestDiff:
        """Fetch a PR's unified diff + per-file metadata as a typed model."""
        token = await self._app.installation_token(installation_id)
        headers_base = {
            "Authorization": f"token {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sentinel",
        }

        # 1. Pull request metadata (for head_sha, base_sha).
        meta_resp = await self._http.get(
            f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}",
            headers={**headers_base, "Accept": "application/vnd.github+json"},
        )
        meta_resp.raise_for_status()
        meta: dict[str, Any] = meta_resp.json()
        head_sha = meta["head"]["sha"]
        base_sha = meta["base"]["sha"]

        # 2. Unified diff text (different Accept header on the same endpoint).
        diff_resp = await self._http.get(
            f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}",
            headers={**headers_base, "Accept": "application/vnd.github.v3.diff"},
        )
        diff_resp.raise_for_status()
        unified_diff = diff_resp.text

        # 3. Per-file metadata. The endpoint paginates; we cap at one page
        #    (100 files) for simplicity. Pagination support comes
        #    when we hit a PR that legitimately exceeds 100 files.
        files_resp = await self._http.get(
            f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}/files",
            headers={**headers_base, "Accept": "application/vnd.github+json"},
            params={"per_page": MAX_FILES_PER_PR},
        )
        files_resp.raise_for_status()
        files_data: list[dict[str, Any]] = files_resp.json()

        truncated = len(files_data) >= MAX_FILES_PER_PR
        if truncated:
            _log.warning(
                "github.diff.files_truncated",
                repo=repo_full_name,
                pr=pr_number,
                file_count=len(files_data),
                cap=MAX_FILES_PER_PR,
            )

        files = [self._parse_file(f) for f in files_data]

        return PullRequestDiff(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            base_sha=base_sha,
            unified_diff=unified_diff,
            files=files,
            truncated_files=truncated,
        )

    @staticmethod
    def _parse_file(raw: dict[str, Any]) -> ChangedFile:
        return ChangedFile(
            path=raw["filename"],
            status=raw["status"],
            additions=raw["additions"],
            deletions=raw["deletions"],
            changes=raw["changes"],
            patch=raw.get("patch"),
            previous_filename=raw.get("previous_filename"),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

"""GitHub App authentication and a minimal Apps API client.

GitHub Apps authenticate in two steps:
  1. The App itself signs a short-lived JWT (10 min max) using its private key.
  2. The App exchanges that JWT for an installation access token scoped to a
     single installation. Installation tokens last ~1 hour.

This module exposes a `GitHubAppClient` that handles both, caches installation
tokens until expiry, and exposes the few endpoints we need (post issue
comment on a PR, post commit-status). Bigger surface area lives in
packages/core/github/comments.py and diff.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt

GITHUB_API = "https://api.github.com"
JWT_TTL_SECONDS = 540  # 9 min, leaves margin under GitHub's 10-min cap


@dataclass
class InstallationToken:
    token: str
    expires_at: datetime

    def is_expired(self, *, skew_seconds: int = 60) -> bool:
        return datetime.now(UTC) >= self.expires_at - timedelta(seconds=skew_seconds)


class GitHubAppClient:
    """Minimal async GitHub Apps client.

    Construct once and reuse: it caches installation tokens. Not thread-safe;
    use one per asyncio task or guard with a lock if you need concurrent reuse.
    """

    def __init__(
        self,
        *,
        app_id: str,
        private_key_path: Path,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_id = app_id
        self._private_key = private_key_path.read_text()
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._tokens: dict[int, InstallationToken] = {}

    def _app_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 30, "exp": now + JWT_TTL_SECONDS, "iss": self._app_id}
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def installation_token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and not cached.is_expired():
            return cached.token

        resp = await self._http.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self._app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        token = InstallationToken(
            token=data["token"],
            expires_at=datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")),
        )
        self._tokens[installation_id] = token
        return token.token

    async def post_issue_comment(
        self,
        *,
        installation_id: int,
        repo_full_name: str,
        issue_number: int,
        body: str,
    ) -> dict[str, Any]:
        """Post a top-level comment on a PR or issue."""
        token = await self.installation_token(installation_id)
        resp = await self._http.post(
            f"{GITHUB_API}/repos/{repo_full_name}/issues/{issue_number}/comments",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"body": body},
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    async def post_pr_review(
        self,
        *,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        commit_sha: str,
        body: str,
        comments: list[dict[str, Any]],
        event: str = "COMMENT",
    ) -> dict[str, Any]:
        """Post a PR review with inline comments in one shot.

        GitHub's "reviews" endpoint lets you submit a top-level review body
        plus an arbitrary number of inline comments in a single request.
        That's the right primitive for Sentinel: posting findings
        one-by-one would burn one API call per finding (and one Sentinel
        comment per finding shows up as N notification emails to the
        author).

        `comments` is a list of dicts shaped like:
            {"path": "src/foo.py", "line": 42, "body": "..."}
            {"path": "src/foo.py", "start_line": 40, "line": 42, "body": "..."}

        `event` is one of "COMMENT", "APPROVE", "REQUEST_CHANGES". We
        always use COMMENT so Sentinel doesn't block merges; humans decide.

        `commit_sha` must be the head SHA at the time the review is posted.
        """
        token = await self.installation_token(installation_id)
        resp = await self._http.post(
            f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}/reviews",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "commit_id": commit_sha,
                "body": body,
                "event": event,
                "comments": comments,
            },
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    async def get_file_contents(
        self,
        *,
        installation_id: int,
        repo_full_name: str,
        path: str,
        ref: str | None = None,
    ) -> str | None:
        """Fetch a file's decoded text contents, or None if it doesn't exist.

        Used to read `.sentinel.yml` from a repo's default branch. A 404 (no
        such file) returns None rather than raising — most repos won't have
        a config file and that's fine.

        `ref` selects a branch/tag/SHA; None uses the repo's default branch.
        """
        token = await self.installation_token(installation_id)
        params: dict[str, str] = {}
        if ref is not None:
            params["ref"] = ref
        resp = await self._http.get(
            f"{GITHUB_API}/repos/{repo_full_name}/contents/{path}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.raw+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        # With the raw media type, the body IS the file contents.
        return resp.text

    async def aclose(self) -> None:
        await self._http.aclose()

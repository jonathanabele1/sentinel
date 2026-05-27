"""Shared FastAPI dependencies.

Lazy singletons for objects that are stateless and safe to share across
requests: the orchestrator engine, the GitHub Apps client, the GitHub diff
client, the LLM client. Each one is constructed once per process and reused.

Constructing in __init__ would force everything to be importable at import
time, including dependencies that need env vars. Lazy construction means
import failures don't cascade and tests can avoid touching dependencies they
don't need.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from packages.core.github.app import GitHubAppClient
from packages.core.github.diff import GitHubDiffClient
from packages.core.llm import LLMClient
from packages.core.orchestrator import Engine

from apps.api.config import Settings, get_settings

_engine: Engine | None = None
_github_client: GitHubAppClient | None = None
_github_diff_client: GitHubDiffClient | None = None
_llm_client: LLMClient | None = None


def get_engine() -> Engine:
    """Module-level singleton. Engine is stateless across runs."""
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


def get_github_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitHubAppClient:
    """Lazy singleton. Caches installation tokens across requests."""
    global _github_client
    if _github_client is None:
        _github_client = GitHubAppClient(
            app_id=settings.github_app_id,
            private_key_path=settings.github_app_private_key_path,
        )
    return _github_client


def get_github_diff_client(
    app_client: Annotated[GitHubAppClient, Depends(get_github_client)],
) -> GitHubDiffClient:
    """Lazy singleton. Reuses the GitHubAppClient for auth."""
    global _github_diff_client
    if _github_diff_client is None:
        _github_diff_client = GitHubDiffClient(app_client=app_client)
    return _github_diff_client


def get_llm_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LLMClient:
    """Lazy singleton. The Anthropic SDK client is thread-safe + pooled."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient(api_key=settings.anthropic_api_key)
    return _llm_client

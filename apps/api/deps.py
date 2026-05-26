"""Shared FastAPI dependencies.

Lazy singletons for objects that are stateless and safe to share across
requests: the orchestrator engine, the GitHub Apps client. Each one is
constructed once per process and reused.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from packages.core.github.app import GitHubAppClient
from packages.core.orchestrator import Engine

from apps.api.config import Settings, get_settings

_engine: Engine | None = None
_github_client: GitHubAppClient | None = None


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

"""Pytest fixtures shared across the test suite."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force test-mode settings for every test."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("GITHUB_APP_ID", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    os.environ.pop("DATABASE_URL", None)
    yield

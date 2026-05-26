"""Unit tests for RetryPolicy backoff math."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from packages.core.orchestrator.step import RetryPolicy


def test_default_policy_values() -> None:
    p = RetryPolicy()
    assert p.max_attempts == 3
    assert p.initial_backoff_seconds == 1.0
    assert p.backoff_multiplier == 2.0
    assert p.max_backoff_seconds == 30.0
    assert p.jitter_seconds == 0.5


def test_backoff_for_grows_exponentially() -> None:
    p = RetryPolicy(initial_backoff_seconds=1.0, backoff_multiplier=2.0)
    assert p.backoff_for(1) == 1.0
    assert p.backoff_for(2) == 2.0
    assert p.backoff_for(3) == 4.0
    assert p.backoff_for(4) == 8.0


def test_backoff_caps_at_max() -> None:
    p = RetryPolicy(
        initial_backoff_seconds=1.0,
        backoff_multiplier=2.0,
        max_backoff_seconds=5.0,
    )
    assert p.backoff_for(1) == 1.0
    assert p.backoff_for(2) == 2.0
    assert p.backoff_for(3) == 4.0
    assert p.backoff_for(4) == 5.0  # capped
    assert p.backoff_for(10) == 5.0  # still capped


def test_backoff_rejects_invalid_attempt() -> None:
    p = RetryPolicy()
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        p.backoff_for(0)
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        p.backoff_for(-1)


def test_no_retry_factory() -> None:
    p = RetryPolicy.no_retry()
    assert p.max_attempts == 1


def test_policy_is_frozen() -> None:
    p = RetryPolicy()
    with pytest.raises(FrozenInstanceError):
        p.max_attempts = 99  # type: ignore[misc]

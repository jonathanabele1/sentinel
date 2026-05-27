"""Unit tests for LLM pricing math."""

from __future__ import annotations

from packages.core.llm.pricing import cents_for, has_pricing


def test_known_model_full_million_tokens() -> None:
    # Sonnet 4.5: $3/M input, $15/M output.
    # 1M in + 1M out = $3 + $15 = $18 = 1800 cents
    assert cents_for("claude-sonnet-4-5", tokens_in=1_000_000, tokens_out=1_000_000) == 1800


def test_small_call_rounds_up() -> None:
    # 100 tokens at $3/M = $0.0003 = 0.03 cents. We round up to 1 cent so
    # nothing gets reported as "free" by mistake.
    assert cents_for("claude-sonnet-4-5", tokens_in=100, tokens_out=0) == 1


def test_zero_tokens_returns_zero() -> None:
    assert cents_for("claude-sonnet-4-5", tokens_in=0, tokens_out=0) == 0


def test_input_and_output_priced_independently() -> None:
    in_only = cents_for("claude-sonnet-4-5", tokens_in=1_000_000, tokens_out=0)
    out_only = cents_for("claude-sonnet-4-5", tokens_in=0, tokens_out=1_000_000)
    both = cents_for("claude-sonnet-4-5", tokens_in=1_000_000, tokens_out=1_000_000)
    # The sums shouldn't double-count.
    assert in_only + out_only == both
    assert in_only == 300  # $3
    assert out_only == 1500  # $15


def test_unknown_model_returns_zero_without_raising() -> None:
    # Missing pricing must NOT crash a review; it returns 0 and the caller
    # is expected to log a warning.
    assert cents_for("claude-future-snapshot", tokens_in=1_000, tokens_out=1_000) == 0


def test_has_pricing() -> None:
    assert has_pricing("claude-sonnet-4-5")
    assert has_pricing("claude-opus-4-1")
    assert not has_pricing("claude-future-snapshot")


def test_opus_costs_more_than_sonnet() -> None:
    """Sanity check: opus pricing is more expensive than sonnet."""
    sonnet = cents_for("claude-sonnet-4-5", tokens_in=1_000_000, tokens_out=1_000_000)
    opus = cents_for("claude-opus-4-1", tokens_in=1_000_000, tokens_out=1_000_000)
    assert opus > sonnet

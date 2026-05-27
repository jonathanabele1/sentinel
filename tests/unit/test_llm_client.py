"""Unit tests for LLMClient retry policy + happy-path with a mocked SDK."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from packages.core.llm.client import LLMClient, LLMRetryPolicy

# --- LLMRetryPolicy (pure math) ---


def test_default_policy_values() -> None:
    p = LLMRetryPolicy()
    assert p.max_attempts == 3
    assert p.initial_backoff_seconds == 1.0
    assert p.backoff_multiplier == 2.0


def test_backoff_grows_exponentially() -> None:
    p = LLMRetryPolicy(initial_backoff_seconds=1.0, backoff_multiplier=2.0)
    assert p.backoff_for(1) == 1.0
    assert p.backoff_for(2) == 2.0
    assert p.backoff_for(3) == 4.0


def test_backoff_caps_at_max() -> None:
    p = LLMRetryPolicy(
        initial_backoff_seconds=1.0,
        backoff_multiplier=2.0,
        max_backoff_seconds=5.0,
    )
    assert p.backoff_for(10) == 5.0


def test_backoff_rejects_invalid_attempt() -> None:
    p = LLMRetryPolicy()
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        p.backoff_for(0)


def test_policy_is_frozen() -> None:
    p = LLMRetryPolicy()
    with pytest.raises(FrozenInstanceError):
        p.max_attempts = 99  # type: ignore[misc]


# --- LLMClient construction ---


def test_client_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key is required"):
        LLMClient(api_key="")


# --- LLMClient happy path via mocked SDK ---


def _fake_message(
    *,
    text: str = "ok",
    tokens_in: int = 100,
    tokens_out: int = 50,
    stop_reason: str = "end_turn",
) -> MagicMock:
    """Build a MagicMock shaped like anthropic.types.Message."""
    block = SimpleNamespace(type="text", text=text)
    return MagicMock(
        content=[block],
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
        stop_reason=stop_reason,
    )


async def test_complete_returns_typed_response_with_cost() -> None:
    client = LLMClient(api_key="fake-key")
    fake = _fake_message(tokens_in=1_000_000, tokens_out=1_000_000)
    client._client.messages.create = AsyncMock(return_value=fake)  # type: ignore[method-assign]

    response = await client.complete(
        model="claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
        agent="test",
    )

    assert response.text == "ok"
    assert response.tokens_in == 1_000_000
    assert response.tokens_out == 1_000_000
    assert response.cost_cents == 1800  # $3 + $15
    assert response.model == "claude-sonnet-4-5"
    assert response.stop_reason == "end_turn"

    # Single call, no retries.
    assert client._client.messages.create.call_count == 1


async def test_complete_forwards_tools_and_tool_choice() -> None:
    """Tools / tool_choice get passed through to the SDK call."""
    client = LLMClient(api_key="fake-key")
    client._client.messages.create = AsyncMock(return_value=_fake_message())  # type: ignore[method-assign]

    tool = {"name": "report", "description": "...", "input_schema": {}}
    await client.complete(
        model="claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
        agent="test",
        tools=[tool],
        tool_choice={"type": "tool", "name": "report"},
    )

    call_kwargs = client._client.messages.create.call_args.kwargs
    assert call_kwargs["tools"] == [tool]
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "report"}


async def test_complete_does_not_send_system_when_none() -> None:
    """system isn't passed unless provided (some models reject empty system)."""
    client = LLMClient(api_key="fake-key")
    client._client.messages.create = AsyncMock(return_value=_fake_message())  # type: ignore[method-assign]

    await client.complete(
        model="claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
        agent="test",
    )

    call_kwargs = client._client.messages.create.call_args.kwargs
    assert "system" not in call_kwargs


async def test_complete_handles_response_with_no_text_blocks() -> None:
    """When the response is all tool_use (no text), `text` is empty string."""
    client = LLMClient(api_key="fake-key")
    tool_block = SimpleNamespace(type="tool_use", name="x", input={})
    fake = MagicMock(
        content=[tool_block],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
        stop_reason="tool_use",
    )
    client._client.messages.create = AsyncMock(return_value=fake)  # type: ignore[method-assign]

    response = await client.complete(
        model="claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
        agent="test",
    )
    assert response.text == ""
    assert response.stop_reason == "tool_use"

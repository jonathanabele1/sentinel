"""Unit tests for the structured-output helper.

Mocks the LLMClient so no real Anthropic calls happen. Focuses on the
validation-retry behaviour and schema-to-tool generation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from packages.core.llm.client import LLMResponse
from packages.core.llm.structured import (
    StructuredOutputError,
    _format_validation_error,
    _schema_to_tool,
    _to_snake_case,
    complete_structured,
)
from pydantic import BaseModel, Field


class SimpleSchema(BaseModel):
    """A toy schema for testing."""

    summary: str = Field(description="A summary")
    count: int = Field(ge=0, description="A non-negative integer")


# --- Helper conversion tests ---


def test_to_snake_case() -> None:
    assert _to_snake_case("DiffAnalysis") == "diff_analysis"
    assert _to_snake_case("SimpleSchema") == "simple_schema"
    assert _to_snake_case("Already_snake") == "already_snake"
    assert _to_snake_case("XMLParser") == "xml_parser"


def test_schema_to_tool_basics() -> None:
    tool = _schema_to_tool(SimpleSchema)
    assert tool["name"] == "simple_schema"
    assert "toy schema" in tool["description"].lower()
    schema = tool["input_schema"]
    assert schema["type"] == "object"
    assert "summary" in schema["properties"]
    assert "count" in schema["properties"]


def test_schema_to_tool_name_override() -> None:
    tool = _schema_to_tool(SimpleSchema, tool_name="custom_name")
    assert tool["name"] == "custom_name"


def test_format_validation_error_renders_field_paths() -> None:
    from pydantic import ValidationError

    try:
        SimpleSchema(summary=42, count=-1)  # type: ignore[arg-type]
    except ValidationError as exc:
        rendered = _format_validation_error(exc)

    assert "summary" in rendered
    assert "count" in rendered
    assert "validation" in rendered.lower()


# --- complete_structured behaviour ---


def _tool_use_response(name: str, payload: dict[str, Any]) -> LLMResponse:
    """Build an LLMResponse with a single tool_use block."""
    # Use a class that ToolUseBlock isinstance() will accept. The real type
    # check in structured.py is `isinstance(block, ToolUseBlock)` so we use
    # the real class with a SimpleNamespace-ish construction.
    from anthropic.types import ToolUseBlock

    block = ToolUseBlock.model_construct(type="tool_use", id="toolu_test", name=name, input=payload)
    raw = MagicMock(content=[block], stop_reason="tool_use")
    raw.content = [block]
    return LLMResponse(
        text="",
        tokens_in=100,
        tokens_out=50,
        cost_cents=1,
        model="claude-sonnet-4-5",
        stop_reason="tool_use",
        raw=raw,
    )


async def test_complete_structured_happy_path() -> None:
    """Valid tool output returns the typed Pydantic object."""
    client = MagicMock()
    client.complete = AsyncMock(
        return_value=_tool_use_response("simple_schema", {"summary": "all good", "count": 5})
    )

    parsed, response = await complete_structured(
        client,
        model="claude-sonnet-4-5",
        schema=SimpleSchema,
        messages=[{"role": "user", "content": "do the thing"}],
        agent="test_agent",
    )

    assert isinstance(parsed, SimpleSchema)
    assert parsed.summary == "all good"
    assert parsed.count == 5
    assert client.complete.call_count == 1
    assert response.tokens_in == 100


async def test_complete_structured_retries_on_validation_error() -> None:
    """First call returns invalid data; second returns valid; result is valid."""
    bad = _tool_use_response("simple_schema", {"summary": "ok", "count": -5})  # ge=0 fails
    good = _tool_use_response("simple_schema", {"summary": "ok", "count": 1})

    client = MagicMock()
    client.complete = AsyncMock(side_effect=[bad, good])

    parsed, _ = await complete_structured(
        client,
        model="claude-sonnet-4-5",
        schema=SimpleSchema,
        messages=[{"role": "user", "content": "do it"}],
        agent="test_agent",
    )
    assert parsed.count == 1
    assert client.complete.call_count == 2


async def test_complete_structured_raises_after_retries_exhausted() -> None:
    """All attempts return invalid data; StructuredOutputError raised."""
    bad = _tool_use_response("simple_schema", {"summary": "ok", "count": -1})

    client = MagicMock()
    client.complete = AsyncMock(side_effect=[bad, bad, bad])

    with pytest.raises(StructuredOutputError) as exc_info:
        await complete_structured(
            client,
            model="claude-sonnet-4-5",
            schema=SimpleSchema,
            messages=[{"role": "user", "content": "do it"}],
            agent="test_agent",
            max_validation_retries=2,
        )
    assert exc_info.value.attempts == 3  # initial + 2 retries


async def test_complete_structured_appends_error_feedback_on_retry() -> None:
    """Failed attempt's conversation includes a tool_result with the error."""
    bad = _tool_use_response("simple_schema", {"summary": "ok", "count": -1})
    good = _tool_use_response("simple_schema", {"summary": "ok", "count": 1})

    client = MagicMock()
    client.complete = AsyncMock(side_effect=[bad, good])

    await complete_structured(
        client,
        model="claude-sonnet-4-5",
        schema=SimpleSchema,
        messages=[{"role": "user", "content": "go"}],
        agent="test_agent",
    )

    # The second call's messages should include the assistant turn and a
    # tool_result with is_error=True referencing the prior tool_use_id.
    second_call_messages = client.complete.call_args_list[1].kwargs["messages"]
    assert len(second_call_messages) >= 3  # original user + assistant + tool_result user
    last = second_call_messages[-1]
    assert last["role"] == "user"
    assert isinstance(last["content"], list)
    tool_result = last["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["is_error"] is True
    assert tool_result["tool_use_id"] == "toolu_test"


async def test_complete_structured_raises_when_no_tool_use_block() -> None:
    """If the model returns text instead of a tool call, fail loudly."""
    text_response = LLMResponse(
        text="I refuse to call the tool.",
        tokens_in=10,
        tokens_out=10,
        cost_cents=1,
        model="claude-sonnet-4-5",
        stop_reason="end_turn",
        raw=SimpleNamespace(content=[], stop_reason="end_turn"),
    )
    client = MagicMock()
    client.complete = AsyncMock(side_effect=[text_response, text_response, text_response])

    with pytest.raises(StructuredOutputError, match="failed to call tool"):
        await complete_structured(
            client,
            model="claude-sonnet-4-5",
            schema=SimpleSchema,
            messages=[{"role": "user", "content": "go"}],
            agent="test_agent",
        )

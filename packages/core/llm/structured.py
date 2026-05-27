"""Structured outputs from Claude via the tool-use feature.

Forces the model to call a tool whose input_schema is generated from a
Pydantic class. The model's tool call IS the structured response; we
validate it against the Pydantic class and return a typed object.

If validation fails, we send the validation error back as a tool_result
with is_error=true and let the model try again. Up to N retries.

This module is the bridge between the LLM and the orchestrator's typed
pipeline. Agents define Pydantic input/output models; this helper makes
the LLM produce data conforming to those models.

Usage:
    parsed, response = await complete_structured(
        client,
        model="claude-sonnet-4-5",
        schema=DiffAnalysis,
        messages=[{"role": "user", "content": "Analyze this diff: ..."}],
        agent="diff_analyzer",
    )
    # parsed is a typed DiffAnalysis instance.
"""

from __future__ import annotations

import re
from typing import Any

from anthropic.types import ToolUseBlock
from pydantic import BaseModel, ValidationError

from packages.core.llm.client import LLMClient, LLMResponse
from packages.core.observability.logging import get_logger

_log = get_logger(__name__)


class StructuredOutputError(Exception):
    """Raised when the model fails to produce schema-conforming output after retries."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        last_error: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


async def complete_structured[T: BaseModel](
    client: LLMClient,
    *,
    model: str,
    schema: type[T],
    messages: list[dict[str, Any]],
    agent: str,
    system: str | None = None,
    tool_name: str | None = None,
    tool_description: str | None = None,
    max_tokens: int = 4096,
    max_validation_retries: int = 2,
) -> tuple[T, LLMResponse]:
    """Call Claude and return a Pydantic-validated typed object.

    Returns a (parsed_model, raw_response) tuple. The raw response carries
    token counts and cost so the caller can attribute them to a step.

    Raises StructuredOutputError if the model fails to produce
    schema-conforming output after `max_validation_retries` attempts
    beyond the initial call.

    Arguments:
        client: the shared LLMClient instance.
        model: Anthropic model name (e.g. "claude-sonnet-4-5").
        schema: Pydantic class describing the desired output shape.
        messages: conversation seed (typically a single user turn).
        agent: metrics label identifying who's calling (e.g. "diff_analyzer").
        system: optional system prompt.
        tool_name: override the auto-generated tool name (snake_case of schema).
        tool_description: override the auto-generated description (schema docstring).
        max_tokens: cap on output tokens per call.
        max_validation_retries: how many additional attempts after the first.
    """
    tool = _schema_to_tool(schema, tool_name=tool_name, description=tool_description)
    actual_tool_name = tool["name"]

    conversation = list(messages)
    last_error: ValidationError | None = None

    total_attempts = max_validation_retries + 1  # initial + retries
    for attempt in range(1, total_attempts + 1):
        response = await client.complete(
            model=model,
            messages=conversation,
            system=system,
            max_tokens=max_tokens,
            agent=agent,
            tools=[tool],
            tool_choice={"type": "tool", "name": actual_tool_name},
        )

        tool_block = _find_tool_use_block(response, actual_tool_name)
        if tool_block is None:
            # Force-tool was set; if the model still didn't call it, append
            # the assistant turn and re-prompt. Counts as a validation retry.
            _log.warning(
                "structured.no_tool_use_block",
                agent=agent,
                attempt=attempt,
                stop_reason=response.stop_reason,
            )
            if attempt >= total_attempts:
                raise StructuredOutputError(
                    f"Model failed to call tool {actual_tool_name!r} after {attempt} attempt(s)",
                    attempts=attempt,
                )
            conversation.append(_assistant_turn(response))
            conversation.append(
                {
                    "role": "user",
                    "content": (
                        f"You did not call the {actual_tool_name} tool. "
                        f"Please call it now with the required input."
                    ),
                }
            )
            continue

        try:
            parsed = schema.model_validate(tool_block.input)
        except ValidationError as exc:
            last_error = exc
            _log.warning(
                "structured.validation_failed",
                agent=agent,
                schema=schema.__name__,
                attempt=attempt,
                errors=str(exc),
            )
            if attempt >= total_attempts:
                raise StructuredOutputError(
                    f"Schema validation failed after {attempt} attempts",
                    attempts=attempt,
                    last_error=exc,
                ) from exc

            # Append the assistant's tool_use turn, then a tool_result with the
            # validation error. This is the conventional Anthropic shape for
            # giving feedback to a model after a failed tool call.
            conversation.append(_assistant_turn(response))
            conversation.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "is_error": True,
                            "content": _format_validation_error(exc),
                        }
                    ],
                }
            )
            continue

        _log.info(
            "structured.validated",
            agent=agent,
            schema=schema.__name__,
            attempts=attempt,
        )
        return parsed, response

    # Should be unreachable; the loop either returns or raises.
    raise StructuredOutputError(
        "structured.exhausted_unexpectedly",
        attempts=total_attempts,
        last_error=last_error,
    )


# --- Helpers ---


def _schema_to_tool(
    schema: type[BaseModel],
    *,
    tool_name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Generate an Anthropic tool definition from a Pydantic class."""
    name = tool_name or _to_snake_case(schema.__name__)
    docstring = schema.__doc__ or f"Return a {schema.__name__} object."
    desc = description or " ".join(docstring.split())
    return {
        "name": name,
        "description": desc,
        "input_schema": schema.model_json_schema(),
    }


def _find_tool_use_block(response: LLMResponse, tool_name: str) -> ToolUseBlock | None:
    """Extract the tool_use block matching the expected tool name."""
    for block in response.raw.content:
        if isinstance(block, ToolUseBlock) and block.name == tool_name:
            return block
    return None


def _assistant_turn(response: LLMResponse) -> dict[str, Any]:
    """Serialise the model's previous turn for inclusion in the next call.

    The Anthropic SDK's content blocks are Pydantic models; dumping them
    produces the dict shape the API expects when we send the conversation
    back in.
    """
    return {
        "role": "assistant",
        "content": [block.model_dump() for block in response.raw.content],
    }


def _to_snake_case(name: str) -> str:
    """CamelCase → snake_case for tool naming."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _format_validation_error(error: ValidationError) -> str:
    """Render a Pydantic ValidationError as a human-readable bullet list."""
    lines = ["The following fields failed validation:"]
    for err in error.errors():
        location = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"  - {location}: {err['msg']}")
    return "\n".join(lines)

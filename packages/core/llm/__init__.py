"""Sentinel LLM layer: Anthropic SDK wrapper + structured-output helper.

Every LLM call in Sentinel goes through this layer. The client owns the
cross-cutting concerns (retries, timeouts, cost, OTel spans, metrics);
agents stay focused on their prompts and Pydantic schemas.
"""

from packages.core.llm.client import LLMClient, LLMError, LLMResponse, LLMRetryPolicy
from packages.core.llm.structured import StructuredOutputError, complete_structured

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "LLMRetryPolicy",
    "StructuredOutputError",
    "complete_structured",
]

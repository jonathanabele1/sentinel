"""Per-model pricing for cost calculation.

Costs are dollars per 1M tokens, separated into input and output. Anthropic
publishes these; check https://www.anthropic.com/pricing if a model is
missing or pricing has changed.

Calculation: tokens / 1_000_000 * price_per_million_dollars * 100 = cents.
We round up the resulting cents because we don't want to under-report the
cost spent on a per-call basis (sub-cent calls still aggregate correctly
in the Counter).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Price per 1M tokens for one model, in dollars."""

    input_dollars_per_million: float
    output_dollars_per_million: float


# Subset of Anthropic's lineup likely to be used by Sentinel. Add new entries
# as we onboard models. UNKNOWN_PRICING is the fallback for models we haven't
# priced yet; cost calculations use it but log a warning so we notice.
PRICING: dict[str, ModelPricing] = {
    # Claude 4.x family. Update these when newer snapshots ship.
    "claude-sonnet-4-5": ModelPricing(3.0, 15.0),
    "claude-sonnet-4-5-20250929": ModelPricing(3.0, 15.0),
    "claude-opus-4-1": ModelPricing(15.0, 75.0),
    "claude-haiku-4-5": ModelPricing(1.0, 5.0),
    # Older snapshots, kept for replay-compatibility with historical runs.
    "claude-3-7-sonnet-20250219": ModelPricing(3.0, 15.0),
    "claude-3-5-sonnet-20241022": ModelPricing(3.0, 15.0),
}

UNKNOWN_PRICING = ModelPricing(0.0, 0.0)

# Prompt-caching multipliers on the base input rate. A cache read is much
# cheaper than fresh input; a cache write costs a bit more than fresh input.
# These are the standard Anthropic ratios for the default 5-minute cache.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


def cents_for(
    model: str,
    tokens_in: int,
    tokens_out: int,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> int:
    """Return the cost of a call in whole cents, rounded up.

    `tokens_in` is fresh (non-cached) input. `cache_read_tokens` and
    `cache_creation_tokens` are the prompt-caching portions, priced off the
    same input rate at the multipliers above. Defaulting both to 0 keeps the
    common (non-cached) call and existing callers unchanged.

    Returns 0 (with no error) for unknown models; the caller is expected to
    log a warning. We don't want a missing pricing entry to crash a review.
    """
    price = PRICING.get(model, UNKNOWN_PRICING)
    input_dollars = tokens_in / 1_000_000 * price.input_dollars_per_million
    cache_read_dollars = (
        cache_read_tokens / 1_000_000 * price.input_dollars_per_million * CACHE_READ_MULTIPLIER
    )
    cache_write_dollars = (
        cache_creation_tokens / 1_000_000 * price.input_dollars_per_million * CACHE_WRITE_MULTIPLIER
    )
    output_dollars = tokens_out / 1_000_000 * price.output_dollars_per_million
    return math.ceil(
        (input_dollars + cache_read_dollars + cache_write_dollars + output_dollars) * 100
    )


def has_pricing(model: str) -> bool:
    """Whether the given model has a known pricing entry."""
    return model in PRICING

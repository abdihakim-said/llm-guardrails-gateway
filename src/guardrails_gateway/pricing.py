"""Per-model token prices and cost calculation.

Prices are USD per million tokens. They change, so treat this table as
configuration: check the provider's pricing page and override it with
`PriceTable({...})` rather than trusting the defaults in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    # Prompt-cache multipliers relative to the input price.
    cache_read_multiplier: Decimal = Decimal("0.1")
    cache_write_multiplier: Decimal = Decimal("1.25")


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


# Anthropic first-party list prices (USD / MTok) as of mid-2026.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-opus-5": ModelPrice(Decimal("5"), Decimal("25")),
    "claude-opus-4-8": ModelPrice(Decimal("5"), Decimal("25")),
    "claude-sonnet-5": ModelPrice(Decimal("2"), Decimal("10")),
    "claude-haiku-4-5": ModelPrice(Decimal("1"), Decimal("5")),
    # Used by the offline fake provider in tests and the demo.
    "fake-model": ModelPrice(Decimal("3"), Decimal("15")),
}

_MTOK = Decimal(1_000_000)


class UnknownModelError(KeyError):
    pass


class PriceTable:
    def __init__(self, prices: dict[str, ModelPrice] | None = None):
        self._prices = dict(DEFAULT_PRICES if prices is None else prices)

    def price(self, model: str) -> ModelPrice:
        try:
            return self._prices[model]
        except KeyError:
            # Refuse to guess: an unpriced model would make budgets meaningless.
            raise UnknownModelError(f"no price configured for model {model!r}") from None

    def cost(self, model: str, usage: Usage) -> Decimal:
        p = self.price(model)
        total = (
            usage.input_tokens * p.input_per_mtok
            + usage.output_tokens * p.output_per_mtok
            + usage.cache_read_input_tokens * p.input_per_mtok * p.cache_read_multiplier
            + usage.cache_creation_input_tokens * p.input_per_mtok * p.cache_write_multiplier
        )
        return total / _MTOK

    def max_cost(self, model: str, input_tokens: int, max_output_tokens: int) -> Decimal:
        """Worst-case cost of a call, used to reserve budget before it runs."""
        return self.cost(model, Usage(input_tokens, max_output_tokens))

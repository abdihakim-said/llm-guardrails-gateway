"""OpenTelemetry spans and metrics for each model call, if OTel is installed.

Attribute names follow the OpenTelemetry GenAI semantic conventions
(gen_ai.*), plus a cost attribute. Without the `otel` extra, this is a no-op.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

try:
    from opentelemetry import metrics, trace

    _tracer = trace.get_tracer("guardrails_gateway")
    _meter = metrics.get_meter("guardrails_gateway")
    _tokens = _meter.create_counter("gen_ai.client.token.usage", unit="{token}")
    _cost = _meter.create_counter("llm.cost", unit="USD")
    _duration = _meter.create_histogram("gen_ai.client.operation.duration", unit="s")
    ENABLED = True
except ImportError:  # pragma: no cover - exercised only without the extra
    ENABLED = False


class _NullSpan:
    def set_attribute(self, *_):
        pass


@contextmanager
def model_call_span(provider: str, model: str, team: str):
    if not ENABLED:
        yield _NullSpan()
        return
    with _tracer.start_as_current_span(f"chat {model}") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.system", provider)
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("team", team)
        yield span


def record_usage(span, provider: str, model: str, team: str, input_tokens: int,
                 output_tokens: int, cost: Decimal, seconds: float) -> None:
    span.set_attribute("gen_ai.response.model", model)
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    span.set_attribute("llm.cost_usd", float(cost))
    if not ENABLED:
        return
    attrs = {"gen_ai.system": provider, "gen_ai.response.model": model, "team": team}
    _tokens.add(input_tokens, {**attrs, "gen_ai.token.type": "input"})
    _tokens.add(output_tokens, {**attrs, "gen_ai.token.type": "output"})
    _cost.add(float(cost), attrs)
    _duration.record(seconds, attrs)

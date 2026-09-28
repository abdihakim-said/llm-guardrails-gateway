"""Model providers behind one small interface.

`FakeProvider` runs offline (tests, demo). `AnthropicProvider` calls Claude
through the official SDK. Other providers implement the same `complete()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .pricing import Usage


@dataclass
class Completion:
    text: str
    model: str  # the model that actually served the request
    usage: Usage
    refused: bool = False


class Provider(Protocol):
    name: str

    def count_input_tokens(self, model: str, system: str, messages: list[dict]) -> int: ...

    def complete(self, model: str, system: str, messages: list[dict], max_tokens: int) -> Completion: ...


class FakeProvider:
    """Deterministic stand-in: ~4 characters per token, echoes the last message."""

    name = "fake"

    def __init__(self, output_tokens: int = 50):
        self.output_tokens = output_tokens
        self.calls: list[dict] = []

    def count_input_tokens(self, model, system, messages):
        chars = len(system) + sum(len(str(m["content"])) for m in messages)
        return max(1, chars // 4)

    def complete(self, model, system, messages, max_tokens):
        self.calls.append({"model": model, "system": system, "messages": messages})
        out = min(self.output_tokens, max_tokens)
        return Completion(
            text=f"echo: {messages[-1]['content']}",
            model=model,
            usage=Usage(self.count_input_tokens(model, system, messages), out),
        )


class AnthropicProvider:
    """Claude via the Anthropic Python SDK, with server-side refusal fallback."""

    name = "anthropic"

    def __init__(self, client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client

    def count_input_tokens(self, model, system, messages):
        kwargs = {"model": model, "messages": messages}
        if system:
            kwargs["system"] = system
        return self._client.messages.count_tokens(**kwargs).input_tokens

    def complete(self, model, system, messages, max_tokens):
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        response = self._client.beta.messages.create(
            **kwargs,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_input_tokens=u.cache_read_input_tokens or 0,
            cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
        )
        # Check stop_reason before reading content: a refusal has no answer.
        if response.stop_reason == "refusal":
            return Completion(text="", model=response.model, usage=usage, refused=True)
        text = "".join(b.text for b in response.content if b.type == "text")
        return Completion(text=text, model=response.model, usage=usage)

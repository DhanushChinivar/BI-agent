"""Anthropic SDK client with prompt caching support."""
from functools import lru_cache
from typing import AsyncIterator

import anthropic

from app.config.settings import get_settings


class EmptyCompletionError(RuntimeError):
    """The model returned no text. Carries `stop_reason` so callers can log why."""

    def __init__(self, stop_reason: str | None):
        self.stop_reason = stop_reason
        super().__init__(f"Model returned no text content (stop_reason={stop_reason})")


@lru_cache(maxsize=1)
def get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)


def _text_of(response: anthropic.types.Message) -> str:
    """Concatenate the text blocks of a response.

    `response.content[0].text` assumed both that content is non-empty and that
    its first block is text. Neither is guaranteed: a response can come back
    with an empty content list, and a `thinking` or `tool_use` block can precede
    the text — in which case the old code raised `AttributeError` or, worse,
    returned a non-answer. An empty result raises here rather than propagating
    "" into the pipeline, where an empty plan or answer fails much further from
    the cause.
    """
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    if not text:
        raise EmptyCompletionError(response.stop_reason)
    return text


async def chat(
    messages: list[dict],
    system: str,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
    cache_system: bool = True,
) -> str:
    """Single-turn chat completion. Caches system prompt by default."""
    client = get_client()
    settings = get_settings()

    system_param: list[anthropic.types.TextBlockParam] | str
    if cache_system:
        system_param = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_param = system

    response = await client.messages.create(
        model=model or settings.llm_model,
        max_tokens=max_tokens,
        system=system_param,
        messages=messages,
    )
    return _text_of(response)


async def stream(
    messages: list[dict],
    system: str,
    *,
    model: str | None = None,
    max_tokens: int = 2048,
    cache_system: bool = True,
) -> AsyncIterator[str]:
    """Streaming chat completion. Yields text deltas."""
    client = get_client()
    settings = get_settings()

    system_param: list[anthropic.types.TextBlockParam] | str
    if cache_system:
        system_param = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_param = system

    async with client.messages.stream(
        model=model or settings.llm_model,
        max_tokens=max_tokens,
        system=system_param,
        messages=messages,
    ) as s:
        async for text in s.text_stream:
            yield text

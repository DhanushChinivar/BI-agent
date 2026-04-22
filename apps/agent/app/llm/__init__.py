"""Anthropic SDK client with prompt caching support."""
from functools import lru_cache
from typing import AsyncIterator

import anthropic

from app.config.settings import get_settings


@lru_cache(maxsize=1)
def get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)


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
    return response.content[0].text  # type: ignore[union-attr]


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

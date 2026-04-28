"""Helpers for extracting content from LangGraph messages.

LangGraph converts dict messages to LangChain message objects (HumanMessage,
AIMessage, etc.) internally. Nodes must handle both forms.
"""
from typing import Any


def get_content(msg: Any) -> str:
    """Return the text content of a message regardless of its type."""
    if isinstance(msg, dict):
        return str(msg.get("content", ""))
    return str(getattr(msg, "content", ""))


def get_role(msg: Any) -> str:
    """Return the role of a message regardless of its type."""
    if isinstance(msg, dict):
        return msg.get("role", "")
    # LangChain message types: "human", "ai", "system", "tool"
    return getattr(msg, "type", "")


def last_human_message(messages: list[Any]) -> str:
    """Return the content of the most recent human message."""
    for msg in reversed(messages):
        if get_role(msg) in ("human", "user"):
            return get_content(msg)
    return get_content(messages[-1]) if messages else ""

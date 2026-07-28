"""Summarizer node: streams the final natural-language answer."""
import time

import structlog

from app.graph.message_utils import last_human_message
from app.graph.state import AgentState
from app.llm import stream

log = structlog.get_logger(__name__)

_SYSTEM = """You are the summarizer module of a Business Intelligence agent.
Translate structured analysis into a clear, concise business-friendly answer.

Guidelines:
- Open with a direct answer to the user's question (1–2 sentences)
- Support it with the most relevant metrics and insights (bullet points or short paragraphs)
- Flag any anomalies or caveats at the end
- Use plain language — no jargon, no JSON, no markdown headers
- Keep the total response under 300 words
- Keep the total response under 300 words"""


def build_prompt(question: str, analysis: dict, retrieved_data: list[dict]) -> str:
    """Build the summarizer prompt.

    Shared with the SSE path in `api/query.py`, which streams tokens itself
    rather than calling this node. Keeping one builder stops the two copies
    from drifting apart.
    """
    # One entry per resource read, so several sheets from one connector must
    # collapse to a single source name. `connector_error` is the retriever's
    # failure marker.
    sources: list[str] = []
    for item in retrieved_data:
        name = item.get("source")
        if name and not item.get("connector_error") and name not in sources:
            sources.append(name)

    sources_section = (
        f"\n\nActive data sources: {sources}" if sources else "\n\nActive data sources: none"
    )

    return (
        f"User question: {question}\n\n"
        f"Analysis results:\n"
        f"Insights: {analysis.get('insights', [])}\n"
        f"Metrics: {analysis.get('metrics', {})}\n"
        f"Trends: {analysis.get('trends', [])}\n"
        f"Anomalies: {analysis.get('anomalies', [])}"
        f"{sources_section}"
    )


async def summarizer_node(state: AgentState) -> dict:
    t0 = time.monotonic()
    bound = log.bind(
        node="summarizer",
        conversation_id=state.get("conversation_id"),
        user_id=state.get("user_id"),
    )

    messages = state.get("messages", [])
    user_question = last_human_message(messages) or "Summarize the analysis."
    analysis = state.get("analysis", {})

    prompt = build_prompt(user_question, analysis, state.get("retrieved_data", []))

    chunks: list[str] = []
    try:
        async for chunk in stream(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
            max_tokens=512,
        ):
            chunks.append(chunk)
    except Exception as exc:
        bound.error("streaming_failed", error=str(exc))
        chunks = [f"Summary unavailable. Insights: {analysis.get('insights', [])}"]

    bound.info("complete", duration_ms=round((time.monotonic() - t0) * 1000), tokens=len("".join(chunks)))
    return {"final_answer": "".join(chunks), "next_node": "end"}

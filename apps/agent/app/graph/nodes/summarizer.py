"""Summarizer node: streams the final natural-language answer."""
import logging

from app.graph.state import AgentState
from app.llm import stream

logger = logging.getLogger(__name__)

_SYSTEM = """You are the summarizer module of a Business Intelligence agent.
Translate structured analysis into a clear, concise business-friendly answer.

Guidelines:
- Open with a direct answer to the user's question (1–2 sentences)
- Support it with the most relevant metrics and insights (bullet points or short paragraphs)
- Flag any anomalies or caveats at the end
- Use plain language — no jargon, no JSON, no markdown headers
- Keep the total response under 300 words"""


async def summarizer_node(state: AgentState) -> dict:
    messages = state.get("messages", [])
    analysis = state.get("analysis", {})

    user_question = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "human"),
        "Summarize the analysis.",
    )

    prompt = (
        f"User question: {user_question}\n\n"
        f"Analysis results:\n"
        f"Insights: {analysis.get('insights', [])}\n"
        f"Metrics: {analysis.get('metrics', {})}\n"
        f"Trends: {analysis.get('trends', [])}\n"
        f"Anomalies: {analysis.get('anomalies', [])}"
    )

    chunks: list[str] = []
    try:
        async for chunk in stream(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
            max_tokens=512,
        ):
            chunks.append(chunk)
    except Exception as exc:
        logger.error("Summarizer streaming failed: %s", exc)
        chunks = [f"Summary unavailable. Insights: {analysis.get('insights', [])}"]

    return {"final_answer": "".join(chunks), "next_node": "end"}

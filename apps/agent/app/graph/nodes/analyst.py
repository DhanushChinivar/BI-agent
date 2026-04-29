"""Analyst node: computes metrics and performs LLM-powered analysis on retrieved data."""
import json
import logging

from app.graph.message_utils import last_human_message
from app.graph.state import AgentState
from app.llm import chat

logger = logging.getLogger(__name__)

_SYSTEM = """You are the analysis module of a Business Intelligence agent.
You receive retrieved data and a plan, then produce a structured analysis.

The data may be tabular (rows with numeric columns) OR text documents (rows with a "content" field).
Handle both cases:
- Tabular data: compute metrics, identify trends and anomalies
- Text/document data: extract key points, relevant facts, and direct answers to the user's question as insights; leave metrics/trends/anomalies empty if inapplicable

Respond with JSON only — no prose, no markdown fences:
{
  "insights": ["<insight 1>", "<insight 2>", ...],
  "metrics": {"<metric_name>": <value>, ...},
  "trends": ["<trend description>", ...],
  "anomalies": ["<anomaly description>", ...]
}

Rules:
- insights: 2–5 key findings directly answering the user's question (required — never empty)
- metrics: computed numbers for tabular data; empty object {} for pure text documents
- trends: directional patterns; empty list [] if not applicable
- anomalies: unexpected values; empty list [] if not applicable
- Be precise with numbers; derive them from the data provided
- For text documents, quote or paraphrase the most relevant passages as insights"""


def _build_analysis_prompt(plan: list[str], retrieved_data: list[dict], question: str) -> str:
    steps = [p for p in plan if not p.startswith(("connectors:", "question_type:"))]
    return (
        f"User question: {question}\n\n"
        f"Plan steps: {json.dumps(steps)}\n\n"
        f"Retrieved data:\n{json.dumps(retrieved_data, indent=2)}"
    )


async def analyst_node(state: AgentState) -> dict:
    messages = state.get("messages", [])
    plan = state.get("plan", [])
    retrieved_data = state.get("retrieved_data", [])

    user_question = last_human_message(messages) or "Analyze the data."

    prompt = _build_analysis_prompt(plan, retrieved_data, user_question)

    try:
        raw = await chat(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
            max_tokens=1024,
        )
        analysis = json.loads(raw)
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Analyst failed to parse LLM response: %s", exc)
        analysis = {"insights": ["Analysis unavailable"], "metrics": {}, "trends": [], "anomalies": []}

    return {"analysis": analysis, "next_node": "summarizer"}

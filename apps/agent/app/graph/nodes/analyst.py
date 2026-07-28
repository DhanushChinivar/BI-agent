"""Analyst node: computes metrics and performs LLM-powered analysis on retrieved data."""
import json
import time

import structlog

from app.graph.message_utils import last_human_message
from app.graph.state import AgentState
from app.llm import chat

log = structlog.get_logger(__name__)

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
- For text documents, quote or paraphrase the most relevant passages as insights
- If a source has a non-null "omitted_items" count, that many rows were dropped before you saw them.
  Any total, average, or count over that source is a partial figure — say so in the insight
  (e.g. "at least $X across the 60 rows shown; 240 more rows were not included")"""


def _build_analysis_prompt(plan: list[str], retrieved_data: list[dict], question: str) -> str:
    steps = [p for p in plan if not p.startswith(("connectors:", "question_type:"))]
    # Strip resource metadata — only send data rows to keep token count low.
    # `omitted_items` is carried through so totals over a trimmed dataset are
    # reported as partial rather than exact.
    compact_data = [
        {
            "source": r.get("source"),
            "data": r.get("data"),
            "error": r.get("error"),
            "omitted_items": r.get("omitted_items"),
        }
        for r in retrieved_data
    ]
    return (
        f"User question: {question}\n\n"
        f"Plan steps: {json.dumps(steps)}\n\n"
        f"Retrieved data:\n{json.dumps(compact_data, separators=(',', ':'))}"
    )


async def analyst_node(state: AgentState) -> dict:
    t0 = time.monotonic()
    bound = log.bind(
        node="analyst",
        conversation_id=state.get("conversation_id"),
        user_id=state.get("user_id"),
    )

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
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        analysis = json.loads(raw)
    except (json.JSONDecodeError, KeyError) as exc:
        bound.warning("parse_failed", error=str(exc))
        analysis = {"insights": ["Analysis unavailable"], "metrics": {}, "trends": [], "anomalies": []}

    bound.info("complete", duration_ms=round((time.monotonic() - t0) * 1000), insights=len(analysis.get("insights", [])))
    return {"analysis": analysis, "next_node": "summarizer"}

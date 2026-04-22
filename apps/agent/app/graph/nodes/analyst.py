"""Analyst node: computes metrics / performs analysis on retrieved data."""
from app.graph.state import AgentState


async def analyst_node(state: AgentState) -> dict:
    """Run calculations or LLM-powered analysis on retrieved_data."""
    # TODO: compute aggregations, detect trends, call LLM for qualitative analysis
    return {
        "analysis": {"summary": "stub analysis"},
        "next_node": "summarizer",
    }

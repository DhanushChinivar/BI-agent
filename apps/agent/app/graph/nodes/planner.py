"""Planner node: decomposes user question into sub-tasks."""
from app.graph.state import AgentState


async def planner_node(state: AgentState) -> dict:
    """Takes the latest user message, produces a plan."""
    # TODO: call LLM with planning prompt, parse into list of steps
    return {
        "plan": ["retrieve", "analyze", "summarize"],
        "next_node": "retriever",
    }

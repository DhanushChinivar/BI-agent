"""Summarizer node: streams the final natural-language answer."""
from app.graph.state import AgentState


async def summarizer_node(state: AgentState) -> dict:
    """Produce the final user-facing answer from state['analysis']."""
    # TODO: call LLM with streaming, craft final answer
    analysis = state.get("analysis", {})
    return {
        "final_answer": f"Stub answer. Analysis: {analysis}",
        "next_node": "end",
    }

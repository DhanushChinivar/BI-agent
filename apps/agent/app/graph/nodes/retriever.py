"""Retriever node: calls connectors to fetch data."""
from app.graph.state import AgentState


async def retriever_node(state: AgentState) -> dict:
    """Based on the plan, invoke the relevant connectors."""
    # TODO: pick connector based on plan, fetch data
    return {
        "retrieved_data": [],
        "next_node": "analyst",
    }

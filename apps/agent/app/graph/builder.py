"""Builds and compiles the LangGraph agent."""
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.action import action_node
from app.graph.nodes.analyst import analyst_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.retriever import retriever_node
from app.graph.nodes.summarizer import summarizer_node
from app.graph.state import AgentState


def _route_after_summarizer(state: AgentState) -> str:
    return "action" if state.get("action_required") else END


def build_graph():
    """Wire nodes into a LangGraph. Compile once at startup, reuse per request."""
    g = StateGraph(AgentState)

    g.add_node("planner", planner_node)
    g.add_node("retriever", retriever_node)
    g.add_node("analyst", analyst_node)
    g.add_node("summarizer", summarizer_node)
    g.add_node("action", action_node)

    g.add_edge(START, "planner")
    g.add_edge("planner", "retriever")
    g.add_edge("retriever", "analyst")
    g.add_edge("analyst", "summarizer")
    g.add_conditional_edges("summarizer", _route_after_summarizer, {"action": "action", END: END})
    g.add_edge("action", END)

    return g.compile()


# Module-level singleton so uvicorn workers share one compiled graph.
graph = build_graph()

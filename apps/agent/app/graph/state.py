"""Shared state passed between LangGraph nodes.

Keep this small and typed. Every node reads from and writes to this state.
If you need to add a field, ask: does every node need to know about it?
If no, it probably belongs in a node-local variable or a tool result.
"""
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # Conversation
    messages: Annotated[list[dict[str, Any]], add_messages]

    # User / session
    user_id: str
    conversation_id: str

    # Planner output
    plan: list[str]

    # Retrieval output
    retrieved_data: list[dict[str, Any]]
    question_type: str             # planner's classification; retriever and compute both read it

    # Compute output — SQL run over the untrimmed table, so a total is computed
    # rather than derived from the sampled rows. None when it did not engage.
    computation: dict[str, Any] | None

    # Analyst output
    analysis: dict[str, Any]

    # Final answer
    final_answer: str

    # Action / scheduling (Phase 3)
    action_required: bool
    action_type: str | None        # "schedule_report" | "data_alert"
    action_cron: str | None        # cron expression, e.g. "0 8 * * 1"
    action_question: str | None    # question to run on schedule
    schedule_result: dict[str, Any] | None

    # Control flow
    next_node: Literal["planner", "retriever", "compute", "analyst", "summarizer", "action", "end"]
    error: str | None

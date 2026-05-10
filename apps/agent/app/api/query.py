"""POST /v1/query and POST /v1/query/stream — main entry points."""
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.db.engine import get_session_factory
from app.db.history_crud import add_message, create_conversation, get_conversation, touch_conversation
from app.graph.builder import graph
from app.graph.nodes.analyst import analyst_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.retriever import retriever_node
from app.graph.nodes.summarizer import _SYSTEM as SUMMARIZER_SYSTEM
from app.graph.state import AgentState
from app.llm import stream as llm_stream
from app.middleware.rate_limit import get_user_id_for_limit, limiter
from app.schemas.query import QueryRequest, QueryResponse

router = APIRouter(prefix="/v1", tags=["query"])


async def _ensure_conversation(user_id: str, conversation_id: str, title: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        existing = await get_conversation(session, user_id, conversation_id)
        if existing is None:
            await create_conversation(session, user_id, conversation_id, title)


async def _persist_messages(conversation_id: str, user_msg: str, assistant_msg: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await add_message(session, conversation_id, "user", user_msg)
        await add_message(session, conversation_id, "assistant", assistant_msg)
        await touch_conversation(session, conversation_id)


@router.post("/query", response_model=QueryResponse)
@limiter.limit("60/minute")
async def query(req: QueryRequest, request: Request) -> QueryResponse:
    """Non-streaming endpoint — returns the complete answer in one response."""
    user_id = request.state.user_id
    conversation_id = req.conversation_id or str(uuid.uuid4())
    initial_state: AgentState = {
        "messages": [{"role": "human", "content": req.message}],
        "user_id": user_id,
        "conversation_id": conversation_id,
    }
    final_state = await graph.ainvoke(initial_state)
    answer = final_state.get("final_answer", "")

    title = req.message[:60]
    await _ensure_conversation(user_id, conversation_id, title)
    await _persist_messages(conversation_id, req.message, answer)

    return QueryResponse(final_answer=answer, conversation_id=conversation_id)


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data)}


async def _stream_pipeline(user_id: str, req: QueryRequest) -> AsyncIterator[dict]:
    conversation_id = req.conversation_id or str(uuid.uuid4())
    state: AgentState = {
        "messages": [{"role": "human", "content": req.message}],
        "user_id": user_id,
        "conversation_id": conversation_id,
    }

    # Create the conversation row on first message
    title = req.message[:60]
    await _ensure_conversation(user_id, conversation_id, title)

    yield _sse("stage", {"stage": "planning", "message": "Breaking down your question…"})
    state.update(await planner_node(state))

    yield _sse("stage", {"stage": "retrieving", "message": "Fetching data…"})
    state.update(await retriever_node(state))

    yield _sse("stage", {"stage": "analyzing", "message": "Analyzing results…"})
    state.update(await analyst_node(state))

    yield _sse("stage", {"stage": "summarizing", "message": "Writing your answer…"})

    analysis = state.get("analysis", {})
    retrieved_data = state.get("retrieved_data", [])
    sources = [item["source"] for item in retrieved_data if "error" not in item and "source" in item]
    sources_section = f"\n\nActive data sources: {sources}" if sources else "\n\nActive data sources: none"

    prompt = (
        f"User question: {req.message}\n\n"
        f"Analysis results:\n"
        f"Insights: {analysis.get('insights', [])}\n"
        f"Metrics: {analysis.get('metrics', {})}\n"
        f"Trends: {analysis.get('trends', [])}\n"
        f"Anomalies: {analysis.get('anomalies', [])}"
        f"{sources_section}"
    )

    full_reply: list[str] = []
    async for chunk in llm_stream(
        messages=[{"role": "user", "content": prompt}],
        system=SUMMARIZER_SYSTEM,
        max_tokens=512,
    ):
        full_reply.append(chunk)
        yield _sse("chunk", {"content": chunk})

    # Persist both turns now that streaming is complete
    await _persist_messages(conversation_id, req.message, "".join(full_reply))

    yield _sse("done", {"conversation_id": conversation_id})


@router.post("/query/stream")
@limiter.limit("10/minute", key_func=get_user_id_for_limit)
async def query_stream(req: QueryRequest, request: Request) -> EventSourceResponse:
    """Streaming endpoint — pushes SSE events as the pipeline progresses."""
    user_id = request.state.user_id
    return EventSourceResponse(_stream_pipeline(user_id, req))

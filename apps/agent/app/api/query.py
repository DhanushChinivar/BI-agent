"""POST /v1/query and POST /v1/query/stream — main entry points."""
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.graph.builder import graph
from app.graph.nodes.analyst import analyst_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.retriever import retriever_node
from app.graph.nodes.summarizer import _SYSTEM as SUMMARIZER_SYSTEM, _text_content_from_retrieved
from app.graph.state import AgentState
from app.llm import stream as llm_stream
from app.schemas.query import QueryRequest, QueryResponse

router = APIRouter(prefix="/v1", tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, request: Request) -> QueryResponse:
    """Non-streaming endpoint — returns the complete answer in one response."""
    user_id = request.state.user_id
    initial_state: AgentState = {
        "messages": [{"role": "human", "content": req.message}],
        "user_id": user_id,
        "conversation_id": req.conversation_id or str(uuid.uuid4()),
    }
    final_state = await graph.ainvoke(initial_state)
    return QueryResponse(
        final_answer=final_state.get("final_answer", ""),
        conversation_id=final_state.get("conversation_id"),
    )


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data)}


async def _stream_pipeline(user_id: str, req: QueryRequest) -> AsyncIterator[dict]:
    conversation_id = req.conversation_id or str(uuid.uuid4())
    state: AgentState = {
        "messages": [{"role": "human", "content": req.message}],
        "user_id": user_id,
        "conversation_id": conversation_id,
    }

    yield _sse("stage", {"stage": "planning", "message": "Breaking down your question…"})
    state.update(await planner_node(state))

    yield _sse("stage", {"stage": "retrieving", "message": "Fetching data…"})
    state.update(await retriever_node(state))

    yield _sse("stage", {"stage": "analyzing", "message": "Analyzing results…"})
    state.update(await analyst_node(state))

    yield _sse("stage", {"stage": "summarizing", "message": "Writing your answer…"})

    analysis = state.get("analysis", {})
    retrieved_data = state.get("retrieved_data", [])
    raw_text = _text_content_from_retrieved(retrieved_data)
    raw_section = (
        f"\n\nRaw document text (use this to answer if analysis is sparse):\n{raw_text}"
        if raw_text
        else ""
    )
    prompt = (
        f"User question: {req.message}\n\n"
        f"Analysis results:\n"
        f"Insights: {analysis.get('insights', [])}\n"
        f"Metrics: {analysis.get('metrics', {})}\n"
        f"Trends: {analysis.get('trends', [])}\n"
        f"Anomalies: {analysis.get('anomalies', [])}"
        f"{raw_section}"
    )

    async for chunk in llm_stream(
        messages=[{"role": "user", "content": prompt}],
        system=SUMMARIZER_SYSTEM,
        max_tokens=512,
    ):
        yield _sse("chunk", {"content": chunk})

    yield _sse("done", {"conversation_id": conversation_id})


@router.post("/query/stream")
async def query_stream(req: QueryRequest, request: Request) -> EventSourceResponse:
    """Streaming endpoint — pushes SSE events as the pipeline progresses."""
    user_id = request.state.user_id
    return EventSourceResponse(_stream_pipeline(user_id, req))

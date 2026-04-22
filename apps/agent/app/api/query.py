"""POST /v1/query and POST /v1/query/stream — main entry points."""
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.graph.builder import graph
from app.graph.nodes.analyst import analyst_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.retriever import retriever_node
from app.graph.state import AgentState
from app.llm import stream as llm_stream
from app.graph.nodes.summarizer import _SYSTEM as SUMMARIZER_SYSTEM
from app.schemas.query import QueryRequest, QueryResponse

router = APIRouter(prefix="/v1", tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    """Non-streaming endpoint — returns the complete answer in one response."""
    initial_state: AgentState = {
        "messages": [{"role": "human", "content": req.message}],
        "user_id": req.user_id or "anonymous",
        "conversation_id": req.conversation_id or str(uuid.uuid4()),
    }
    final_state = await graph.ainvoke(initial_state)
    return QueryResponse(
        final_answer=final_state.get("final_answer", ""),
        conversation_id=final_state.get("conversation_id"),
    )


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data)}


async def _stream_pipeline(req: QueryRequest) -> AsyncIterator[dict]:
    conversation_id = req.conversation_id or str(uuid.uuid4())
    state: AgentState = {
        "messages": [{"role": "human", "content": req.message}],
        "user_id": req.user_id or "anonymous",
        "conversation_id": conversation_id,
    }

    # Stage 1: planner
    yield _sse("stage", {"stage": "planning", "message": "Breaking down your question…"})
    state.update(await planner_node(state))

    # Stage 2: retriever
    yield _sse("stage", {"stage": "retrieving", "message": "Fetching data…"})
    state.update(await retriever_node(state))

    # Stage 3: analyst
    yield _sse("stage", {"stage": "analyzing", "message": "Analyzing results…"})
    state.update(await analyst_node(state))

    # Stage 4: summarizer — stream tokens
    yield _sse("stage", {"stage": "summarizing", "message": "Writing your answer…"})

    analysis = state.get("analysis", {})
    prompt = (
        f"User question: {req.message}\n\n"
        f"Analysis results:\n"
        f"Insights: {analysis.get('insights', [])}\n"
        f"Metrics: {analysis.get('metrics', {})}\n"
        f"Trends: {analysis.get('trends', [])}\n"
        f"Anomalies: {analysis.get('anomalies', [])}"
    )

    async for chunk in llm_stream(
        messages=[{"role": "user", "content": prompt}],
        system=SUMMARIZER_SYSTEM,
        max_tokens=512,
    ):
        yield _sse("chunk", {"content": chunk})

    yield _sse("done", {"conversation_id": conversation_id})


@router.post("/query/stream")
async def query_stream(req: QueryRequest) -> EventSourceResponse:
    """Streaming endpoint — pushes SSE events as the pipeline progresses."""
    return EventSourceResponse(_stream_pipeline(req))

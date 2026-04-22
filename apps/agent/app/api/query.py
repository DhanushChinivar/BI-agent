"""POST /v1/query — main entry point."""
from fastapi import APIRouter

from app.graph.builder import graph
from app.schemas.query import QueryRequest, QueryResponse

router = APIRouter(prefix="/v1", tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    """Non-streaming endpoint for now; SSE version comes in Phase 1.5."""
    initial_state = {
        "messages": [{"role": "user", "content": req.message}],
        "user_id": req.user_id or "anonymous",
        "conversation_id": req.conversation_id or "new",
    }
    final_state = await graph.ainvoke(initial_state)
    return QueryResponse(
        final_answer=final_state.get("final_answer", ""),
        conversation_id=final_state.get("conversation_id"),
    )

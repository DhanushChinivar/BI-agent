"""GET /v1/conversations — list, fetch messages, delete."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.history_crud import (
    delete_conversation,
    get_messages,
    get_conversation,
    list_conversations,
)

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("")
async def list_user_conversations(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_id = request.state.user_id
    convs = await list_conversations(session, user_id)
    return [
        {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
        }
        for c in convs
    ]


@router.get("/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_id = request.state.user_id
    conv = await get_conversation(session, user_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = await get_messages(session, conversation_id)
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
        }
        for m in msgs
    ]


@router.delete("/{conversation_id}", status_code=204)
async def remove_conversation(
    conversation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_id = request.state.user_id
    deleted = await delete_conversation(session, user_id, conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")

"""CRUD helpers for conversation history."""
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, Conversation


async def create_conversation(
    session: AsyncSession, user_id: str, conversation_id: str, title: str
) -> Conversation:
    conv = Conversation(
        id=conversation_id,
        user_id=user_id,
        title=title[:256],
    )
    session.add(conv)
    await session.commit()
    return conv


async def get_conversation(
    session: AsyncSession, user_id: str, conversation_id: str
) -> Conversation | None:
    return await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )


async def list_conversations(session: AsyncSession, user_id: str) -> list[Conversation]:
    result = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return list(result.all())


async def touch_conversation(session: AsyncSession, conversation_id: str) -> None:
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = datetime.now(UTC)
        await session.commit()


async def delete_conversation(
    session: AsyncSession, user_id: str, conversation_id: str
) -> bool:
    result = await session.execute(
        delete(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    await session.commit()
    return result.rowcount > 0


async def add_message(
    session: AsyncSession, conversation_id: str, role: str, content: str
) -> ChatMessage:
    msg = ChatMessage(conversation_id=conversation_id, role=role, content=content)
    session.add(msg)
    await session.commit()
    return msg


async def get_messages(session: AsyncSession, conversation_id: str) -> list[ChatMessage]:
    result = await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return list(result.all())


async def update_conversation_title(session: AsyncSession, conversation_id: str, title: str) -> None:
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.title = title[:256]
        await session.commit()


async def count_messages(session: AsyncSession, conversation_id: str) -> int:
    from sqlalchemy import func as sqlfunc
    result = await session.scalar(
        select(sqlfunc.count()).where(ChatMessage.conversation_id == conversation_id)
    )
    return result or 0

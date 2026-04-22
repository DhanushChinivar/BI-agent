"""Pydantic request/response schemas for the HTTP API."""
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    user_id: str | None = None
    conversation_id: str | None = None


class QueryResponse(BaseModel):
    final_answer: str
    conversation_id: str | None = None

"""POST /v1/webhooks/n8n — receives events from n8n and runs them through the pipeline."""
import hashlib
import hmac
import logging
import uuid

from fastapi import APIRouter, Header, HTTPException, Request

from app.config.settings import get_settings
from app.graph.builder import graph
from app.graph.state import AgentState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["n8n"])


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Validate HMAC-SHA256 signature in the format 'sha256=<hex>'."""
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/n8n")
async def n8n_webhook(
    request: Request,
    x_hub_signature_256: str = Header(alias="x-hub-signature-256", default=""),
) -> dict:
    """Inbound webhook from n8n. Runs the BI pipeline with the provided question."""
    settings = get_settings()
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    question = payload.get("question")
    if not question:
        raise HTTPException(status_code=400, detail="Missing 'question' field in payload")

    user_id = payload.get("user_id", "anonymous")
    conversation_id = payload.get("conversation_id") or str(uuid.uuid4())

    logger.info("n8n webhook received question for user=%s conversation=%s", user_id, conversation_id)

    initial_state: AgentState = {
        "messages": [{"role": "human", "content": question}],
        "user_id": user_id,
        "conversation_id": conversation_id,
    }
    final_state = await graph.ainvoke(initial_state)

    return {
        "conversation_id": conversation_id,
        "answer": final_state.get("final_answer", ""),
    }

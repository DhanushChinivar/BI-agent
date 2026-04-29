"""POST /v1/workflows/trigger — manually trigger an n8n workflow execution."""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["n8n"])


class WorkflowTriggerRequest(BaseModel):
    workflow_id: str
    data: dict = {}


class WorkflowTriggerResponse(BaseModel):
    execution_id: str | None = None
    status: str


@router.post("/workflows/trigger", response_model=WorkflowTriggerResponse)
async def trigger_workflow(body: WorkflowTriggerRequest, request: Request) -> WorkflowTriggerResponse:
    """Trigger an n8n workflow by ID. Called by the action node or directly by the frontend."""
    settings = get_settings()

    if not settings.n8n_api_key:
        raise HTTPException(status_code=503, detail="n8n integration not configured")

    headers = {"X-N8N-API-KEY": settings.n8n_api_key, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(base_url=settings.n8n_base_url, timeout=10) as client:
            resp = await client.post(
                f"/api/v1/workflows/{body.workflow_id}/run",
                headers=headers,
                json={
                    "data": {**body.data, "triggered_by": "bi-agent", "user_id": request.state.user_id},
                },
            )
            resp.raise_for_status()
            result = resp.json()
            return WorkflowTriggerResponse(
                execution_id=result.get("executionId"),
                status="triggered",
            )
    except httpx.HTTPStatusError as exc:
        logger.error("n8n trigger error %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(status_code=502, detail=f"n8n error: {exc.response.status_code}")
    except httpx.RequestError as exc:
        logger.error("n8n unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="n8n unreachable")


@router.get("/workflows")
async def list_workflows() -> dict:
    """List all workflows registered in n8n."""
    settings = get_settings()

    if not settings.n8n_api_key:
        raise HTTPException(status_code=503, detail="n8n integration not configured")

    headers = {"X-N8N-API-KEY": settings.n8n_api_key}

    try:
        async with httpx.AsyncClient(base_url=settings.n8n_base_url, timeout=10) as client:
            resp = await client.get("/api/v1/workflows", headers=headers)
            resp.raise_for_status()
            workflows = resp.json().get("data", [])
            return {
                "workflows": [
                    {"id": w["id"], "name": w["name"], "active": w.get("active", False)}
                    for w in workflows
                ]
            }
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="n8n unreachable")

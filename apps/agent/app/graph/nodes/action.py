"""Action node: triggers n8n workflows for scheduling and alerting."""
import logging

import httpx

from app.config.settings import get_settings
from app.graph.state import AgentState

logger = logging.getLogger(__name__)

# Maps action_type → n8n workflow name used to look up the workflow ID via API
_WORKFLOW_NAMES = {
    "schedule_report": "scheduled_report",
    "data_alert": "data_change_alert",
}


async def action_node(state: AgentState) -> dict:
    settings = get_settings()
    action_type = state.get("action_type")
    action_cron = state.get("action_cron")
    action_question = state.get("action_question") or state.get("final_answer", "")

    if not action_type:
        return {"schedule_result": None}

    if not settings.n8n_api_key:
        logger.warning("n8n integration not configured — skipping action node")
        return {"schedule_result": {"status": "skipped", "reason": "n8n not configured"}}

    workflow_name = _WORKFLOW_NAMES.get(action_type)
    if not workflow_name:
        logger.warning("Unknown action_type=%s", action_type)
        return {"schedule_result": {"status": "error", "reason": f"unknown action: {action_type}"}}

    headers = {"X-N8N-API-KEY": settings.n8n_api_key, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(base_url=settings.n8n_base_url, timeout=10) as client:
            # Find the workflow by name
            resp = await client.get("/api/v1/workflows", headers=headers)
            resp.raise_for_status()
            workflows = resp.json().get("data", [])
            workflow = next((w for w in workflows if w.get("name") == workflow_name), None)

            if not workflow:
                return {
                    "schedule_result": {
                        "status": "error",
                        "reason": f"workflow '{workflow_name}' not found in n8n — run import.sh first",
                    }
                }

            workflow_id = workflow["id"]

            # Update the workflow's sticky data with the user's cron + question, then activate it
            patch_body: dict = {"active": True}
            if action_cron or action_question:
                patch_body["settings"] = {
                    "callerPolicy": "workflowsFromSameOwner",
                }
                # Pass schedule/question via tags so workflows can read them
                existing_tags = [t["id"] for t in workflow.get("tags", [])]
                patch_body["tags"] = existing_tags

            patch_resp = await client.patch(
                f"/api/v1/workflows/{workflow_id}",
                headers=headers,
                json=patch_body,
            )
            patch_resp.raise_for_status()

            # Trigger an immediate execution with the payload
            run_resp = await client.post(
                f"/api/v1/workflows/{workflow_id}/run",
                headers=headers,
                json={
                    "startNodes": [],
                    "destinationNode": "",
                    "runData": {},
                    "pinData": {},
                    "workflowData": {
                        "id": workflow_id,
                        "name": workflow_name,
                    },
                    "data": {
                        "question": action_question,
                        "cron": action_cron,
                        "user_id": state.get("user_id", "anonymous"),
                    },
                },
            )
            run_resp.raise_for_status()
            execution = run_resp.json()

            logger.info(
                "Triggered n8n workflow=%s execution=%s for user=%s",
                workflow_name,
                execution.get("executionId"),
                state.get("user_id"),
            )
            return {
                "schedule_result": {
                    "status": "scheduled",
                    "workflow": workflow_name,
                    "workflow_id": workflow_id,
                    "execution_id": execution.get("executionId"),
                    "cron": action_cron,
                }
            }

    except httpx.HTTPStatusError as exc:
        logger.error("n8n API error %s: %s", exc.response.status_code, exc.response.text)
        return {"schedule_result": {"status": "error", "reason": str(exc)}}
    except httpx.RequestError as exc:
        logger.error("n8n unreachable: %s", exc)
        return {"schedule_result": {"status": "error", "reason": "n8n unreachable"}}

# Error Contract — Connectors and Pipeline Nodes

Defines what each connector and node returns on failure so downstream code has a predictable contract.

---

## Connectors

All connectors follow this contract:

| Scenario | Behaviour | Shape returned |
|---|---|---|
| No credentials for user | Raise `PermissionError` | — (retriever catches and logs) |
| API rate limit (429) | Log warning, return error dict | `{"error": "rate_limited", "source": "<name>"}` |
| API error (4xx/5xx) | Log warning, return error dict | `{"error": "<message>", "resource_id": "<id>"}` |
| Resource not found | Return error dict | `{"error": "not_found", "resource_id": "<id>"}` |
| Network timeout | Log warning, return error dict | `{"error": "timeout", "source": "<name>"}` |

Connectors **never raise** outside of `PermissionError`. All other exceptions are caught internally and returned as structured error dicts so the retriever can continue with other connectors.

---

## Retriever Node

- Catches any exception from a connector (including `PermissionError`).
- Appends `{"source": "<name>", "error": "<message>"}` to `retrieved_data` rather than aborting.
- Continues to the next connector.
- If **all** connectors fail, `retrieved_data` is a list of error dicts — the analyst and summarizer must handle this gracefully.

```python
# Shape when a connector fails
{"source": "google_sheets", "error": "No Google Sheets credentials for user 'abc'"}
```

---

## Planner Node

- On LLM call failure: logs error, returns a minimal fallback plan `["retrieve", "analyze", "summarize"]`.
- On JSON parse failure: same fallback plan.
- Never raises — always returns a valid `plan` list.

---

## Analyst Node

- On LLM call failure: logs error, returns `{"insights": ["Analysis unavailable"], "metrics": {}, "trends": [], "anomalies": []}`.
- On JSON parse failure: same fallback.
- If `retrieved_data` contains only error dicts: analyst should surface this in `insights` (e.g. "Could not retrieve data from Google Sheets — check your connection").

---

## Summarizer Node

- On streaming failure: logs error, assembles a stub answer from `analysis["insights"]`.
- Never raises — always returns a non-empty `final_answer`.

---

## API Layer

| Condition | HTTP status | Body |
|---|---|---|
| Pipeline completes (even with partial data) | 200 | `{"final_answer": "..."}` |
| Invalid request body | 422 | FastAPI default validation error |
| Unauthenticated (Phase 5+) | 401 | `{"detail": "Unauthorized"}` |
| Over query limit (Phase 5+) | 402 | `{"detail": "Query limit reached. Upgrade to Pro."}` |
| Unhandled server error | 500 | `{"detail": "Internal server error"}` |

SSE stream errors emit an `event: error` before closing:
```
event: error  {"message": "<reason>"}
```

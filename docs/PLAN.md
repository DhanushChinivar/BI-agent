# BI Agent — Full Plan of Action

> Plan Date: 2026-04-22  
> Last updated: 2026-07-27 (Phase 9 — security hardening; Phases 3/6/7 reconciled with shipped code)  
> Stack: Python · FastAPI · LangGraph · Anthropic Claude · Next.js · PostgreSQL · Redis · n8n · MCP

## Status at a glance

| Phase | Scope | Status |
|---|---|---|
| 1 | Core LangGraph pipeline | ✅ Complete |
| 2 | Real connectors + OAuth + encrypted credentials | ✅ Complete |
| 2.5 | Auth stub | ✅ Complete (superseded by Phase 5) |
| 3 | n8n automation workflows | ✅ Complete (n8n path chosen) |
| 4 | Next.js dashboard | ✅ Complete |
| 5 | Clerk auth + Stripe billing | ✅ Complete |
| 6 | Docker + deploy | 🟡 Docker complete · deploy not started |
| 7 | Polish (tests, evals, observability, docs) | ✅ Complete |
| 8 | MCP connector migration | ✅ Complete |
| 9 | Security hardening | ✅ Complete |
| 10 | RAG retrieval layer | ✅ Complete for text sources (Gmail, Notion) |
| 11 | CI + production deploy | ❌ Not started |

> **Honest framing:** the RAG claim is now true for unstructured sources.
> Gmail threads and Notion pages are chunked, embedded with `voyage-3-lite`, and
> stored in pgvector; `retriever_node` answers questions about them by vector
> similarity and returns citations. It is deliberately *not* true for
> spreadsheets: "what was Q4 revenue?" needs an exact sum over every row, so
> tabular sources stay on the provider-search → read → `compute_node` path. What
> is still missing is reranking, retrieval evals (recall@k), and rendering the
> citations in the UI.

---

## Phase 1 — Core LangGraph Pipeline ✅ COMPLETE

### Goal
End-to-end pipeline that takes a natural-language question and returns a streamed answer powered by Claude.

### Completed Tasks
- [x] LLM client (`app/llm/`) — Anthropic SDK wrapper with prompt caching and streaming
- [x] `planner_node` — Claude decomposes the question into a structured JSON plan
- [x] `retriever_node` — reads connector metadata from plan, fetches data
- [x] `analyst_node` — Claude produces structured insights/metrics/trends from retrieved data
- [x] `summarizer_node` — Claude streams a plain-language answer
- [x] Mock connector with synthetic Q4 sales, marketing, and roadmap data
- [x] `POST /v1/query` — non-streaming endpoint
- [x] `POST /v1/query/stream` — SSE endpoint with stage progress events + token chunks + `done`
- [x] `GET /health` — liveness probe

### SSE Event Shape
```
event: stage  {"stage": "planning",    "message": "Breaking down your question…"}
event: stage  {"stage": "retrieving",  "message": "Fetching data…"}
event: stage  {"stage": "analyzing",   "message": "Analyzing results…"}
event: stage  {"stage": "summarizing", "message": "Writing your answer…"}
event: chunk  {"content": "<token>"}
event: done   {"conversation_id": "<uuid>"}
```

---

## Phase 2 — Real Connectors ✅ COMPLETE

### Goal
Replace the mock connector with live integrations to Google Sheets and Gmail. Store OAuth tokens securely per user.

### Completed Tasks
- [x] Async SQLAlchemy engine + session factory (`app/db/engine.py`)
- [x] `UserConnectorCredential` model — Fernet-encrypted credentials at rest (`app/db/models.py`)
- [x] `get_credentials` / `upsert_credentials` CRUD (`app/db/crud.py`)
- [x] Google Sheets connector — lists spreadsheets via Drive API, reads rows with header mapping
- [x] Gmail connector — lists thread summaries, reads full threads, supports Gmail search queries
> **Note:** CSV/Excel/PDF upload connector was removed — not part of the RAG chatbot scope.
- [x] Notion connector — OAuth (basic-auth token exchange), searches pages/databases, reads blocks as text chunks
- [x] Shared `REGISTRY` dict in `app/connectors/__init__.py`
- [x] Retriever updated to pull from `REGISTRY` _(superseded in Phase 8 — retriever now calls connectors over MCP; `REGISTRY` backs the MCP server)_
- [x] Settings extended: `google_client_id`, `google_client_secret`, `google_redirect_uri`, `credential_encryption_key`
- [x] New dependencies: `google-api-python-client`, `google-auth-oauthlib`, `alembic`, `cryptography`

### Remaining Tasks
- [x] **Alembic setup** — `infra/db/migrations/` with async env.py + first migration for `user_connector_credentials`
- [x] **Split Google OAuth into two flows** — separate consent screens for Sheets/Drive and Gmail
  - `GET /v1/oauth/google-sheets/start?user_id=` — scopes: `spreadsheets.readonly`, `drive.readonly`
  - `GET /v1/oauth/google-sheets/callback` — exchange code, upsert credentials under key `"google_sheets"`
  - `GET /v1/oauth/gmail/start?user_id=` — scope: `gmail.readonly`
  - `GET /v1/oauth/gmail/callback` — exchange code, upsert credentials under key `"gmail"`
- [x] **Token refresh** — `app/connectors/google_auth.py` shared helper; refreshes and persists back to DB automatically
- [x] **`GET /v1/connectors/status?user_id=`** — returns connected/disconnected state per connector; `DELETE /v1/connectors/{name}` to disconnect
- [x] **Error contract** — `docs/errors.md` defines behaviour for all connectors and nodes
- [x] **Redis connector cache** — `app/cache.py` with 5-min TTL; wired into retriever node
- [x] **`.env.example`** — fully updated with all Phase 2 keys and inline documentation

> **Gmail scope warning:** Gmail `readonly` scope triggers Google's OAuth verification process for public apps. During development, keep the GCP project in **Testing mode** (max 100 users). Document this limitation prominently in the README. For a portfolio project, Testing mode is fine.

### Environment Variables Needed
| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | GCP OAuth 2.0 client ID |
| `GOOGLE_CLIENT_SECRET` | GCP OAuth 2.0 client secret |
| `GOOGLE_SHEETS_REDIRECT_URI` | Callback URL for Sheets flow |
| `GOOGLE_GMAIL_REDIRECT_URI` | Callback URL for Gmail flow |
| `CREDENTIAL_ENCRYPTION_KEY` | 32-char key for Fernet encryption |
| `DATABASE_URL` | `postgresql+asyncpg://biagent:biagent@localhost/biagent` |
| `REDIS_URL` | `redis://localhost:6379` |

---

## Phase 2.5 — Minimal Auth Stub ✅ COMPLETE

### Goal
Eliminate the `"anonymous"` user_id hardcoded throughout the pipeline **before** the frontend is built, so Phase 4 and 5 have a clean foundation. This is ~2 hours of work that avoids a grep-and-replace across the whole codebase later.

### Why not just wait for Phase 5?
Every file written between now and Phase 5 that touches `user_id` defaults to `"anonymous"`. Adding the stub now means Phase 5 only needs to swap the verification logic — not hunt down every callsite.

### Tasks
- [x] **FastAPI middleware** `app/middleware/auth.py` — reads `X-User-Id` header, trusts it blindly in dev, writes `request.state.user_id`; returns `anonymous` if header is absent
- [x] **Wire middleware** into `app/main.py`
- [x] **Update API endpoints** — `query.py` and `connectors.py` read `user_id` from `request.state.user_id`
- [x] **Swap point documented** in `app/middleware/auth.py` with inline Phase 5 instructions

> **Security note for Phase 5:** When Clerk is added, pass the **Clerk JWT** via `Authorization: Bearer <token>` — never trust a client-supplied `X-User-Id` header in production. The middleware's job in Phase 5 is to *verify* the JWT and *extract* `user_id` from its claims, not to accept it from the request.

---

## Phase 3 — n8n Automation Workflows ✅ COMPLETE

### Goal
Let users schedule recurring reports and data-change alerts. The agent can also decide during a conversation to trigger a workflow.

> **Action node decision:** The LangGraph `action_node` (for scheduling workflows) is deliberately deferred to this phase. Phase 2 is about *data access*; Phase 3 is about *agents acting*. This keeps the graph simpler until user identity is real.

### n8n vs apscheduler decision
Before starting, choose an execution backend and write an ADR (`docs/adr/0002-scheduling-backend.md`):

| Option | Pros | Cons |
|---|---|---|
| **n8n self-hosted** | Visual editor, rich integrations | Needs always-on server (~$5–10/mo on Railway paid tier; Railway free tier sleeps) |
| **n8n Cloud** | Zero ops, always-on | $20/mo |
| **apscheduler in-process** | No extra service, one less deploy | No visual editor, less flexible |

**Decision:** n8n self-hosted was chosen over `apscheduler` — the visual workflow
editor is part of the demo story. Recorded in `docs/adr/0002-scheduling-backend.md`.
The apscheduler path below is retained as the documented alternative, not as pending work.

### Completed Tasks (n8n path)
- [x] **Settings** — `WEBHOOK_SECRET` in `app/config/settings.py`. The agent no longer calls n8n's API, so it needs no `N8N_API_KEY`; the import script reads one from the shell
- [x] **Inbound webhook** — `POST /v1/webhooks/n8n` (`app/api/n8n_webhooks.py`) — HMAC-verified, routes event to pipeline
- [x] **Ticker endpoint** — `POST /v1/schedules/run-due` (`app/api/schedules.py`) — HMAC-verified, claims due rows `FOR UPDATE SKIP LOCKED` and runs each through the pipeline. Replaced `POST /v1/workflows/trigger`, which called an n8n endpoint that does not exist in the public API
- [x] **LangGraph action node** — `app/graph/nodes/action.py`; planner emits an action step when the question implies scheduling. Writes a `scheduled_reports` row (migration `0004`) and reports the stored `next_run_at`
- [x] **n8n workflow JSON** — `infra/n8n/workflows/schedule_ticker.json`, the single source of truth. The two earlier workflows were deleted: each read its question and schedule from instance-level `$env`/`$vars`, giving a deployment one global schedule, and the `apps/n8n/` mirror had silently diverged from `infra/n8n/`
- [x] **Import script** — `apps/agent/scripts/import_workflows.sh` + `make import-workflows`, which now also activates each workflow (an imported-but-inactive schedule trigger never fires)
- [x] **Schedule confirmation UI** — chat surfaces the workflow the agent created

### Partly adopted: the apscheduler path's data model
n8n stayed as the executor, but the state model from the apscheduler option was taken:
`POST/GET/DELETE /v1/schedules` and a `scheduled_reports` table (`user_id`, `cron`,
`question`, `next_run_at`, `last_status`) now exist. What n8n contributes is the tick and
the email delivery — not the schedule itself. See `docs/adr/0002-scheduling-backend.md`.

---

## Phase 4 — Next.js Dashboard ✅ COMPLETE

### Goal
A chat UI that streams answers in real time, shows pipeline progress, and lets users connect their data sources.

### Scaffold
```bash
cd apps/web
npx create-next-app . --typescript --tailwind --app --src-dir
```

### Tasks

#### Chat UI (`/`)
- [x] `<ChatInput>` — textarea + submit, keyboard shortcuts, abort on new message
- [x] `<StageIndicator>` — animated progress bar through pipeline stages
- [x] `<MessageBubble>` — streaming token rendering with loading dots
- [x] Optimistic UI + auto-scroll to bottom
- [x] `lib/useAgentStream.ts` — `useAgentStream()` hook returning `{ messages, stage, streaming, send, reset }`
- [x] Suggested question buttons on empty state

#### Connector Onboarding (`/connect`)
- [x] Fetches `/v1/connectors/status` on load
- [x] Separate Connect buttons for Google Sheets, Gmail, Notion
- [x] Success banner on redirect back with `?connected=<name>`
- [x] Disconnect button per connector

#### Conversation History (added post-scaffold)
- [x] `conversations` + `messages` tables — migration `0003_conversations.py`, models in `app/db/models.py`
- [x] `app/db/history_crud.py` — create/list/fetch/delete, all scoped by `user_id`
- [x] `GET /v1/conversations`, `GET /v1/conversations/{id}/messages`, `DELETE /v1/conversations/{id}` (`app/api/conversations.py`)
- [x] Sidebar in `/chat` — conversation list, resume, delete, "New conversation"
- [x] Auto-generated conversation titles; multi-turn context passed into the graph

#### Infrastructure
- [x] `AGENT_URL` env var in `.env.local`
- [x] BFF catch-all proxy at `app/api/agent/[...path]/route.ts` — proxies to FastAPI, SSE streaming preserved; Phase 5 JWT injection point marked
- [x] Landing page at `/`; chat moved to `/chat`

---

## Phase 5 — Auth + Stripe ✅ COMPLETE

### Goal
Real user identity so credentials are isolated per account, plus a paywall for pro features.

### Auth (Clerk — recommended for Next.js)
- [x] Clerk installed in `apps/web`, layout wrapped with `<ClerkProvider>`
- [x] Sign-in / sign-up pages at `/sign-in/[[...sign-in]]` and `/sign-up/[[...sign-up]]`
- [x] Next.js `src/proxy.ts` (Next 16's rename of `middleware.ts`) protects all routes; public matchers are `/`, `/sign-in(.*)`, `/sign-up(.*)`, `/api/billing/checkout(.*)`, `/api/agent/v1/oauth/(.*)`
- [x] BFF proxy injects `Authorization: Bearer <clerk-jwt>` via `auth().getToken()` server-side
- [x] FastAPI `app/middleware/auth.py` verifies JWT via Clerk's JWKS, extracts `user_id` from claims; returns 401 in production if missing/invalid; falls back to `X-User-Id` header in dev

### Stripe
- [x] `POST /v1/stripe/webhook` — handles `customer.subscription.created`, `updated`, `deleted`
- [x] `user_plan` table + migration `0002_user_plans.py` — `user_id`, `plan`, `queries_today`, `reset_at`, Stripe IDs
- [x] `app/db/plan_crud.py` — `check_and_increment` (3/day free limit), `set_plan`, `get_or_create_plan`
- [x] `GatingMiddleware` — checks plan before pipeline endpoints, returns 402 with upgrade message
- [x] `GET /v1/plan/status` — returns plan, queries_today, stripe_customer_id
- [x] `POST /api/billing/checkout` — creates Stripe Checkout session for Pro plan
- [x] `POST /api/billing/portal` — creates Stripe Customer Portal session
- [x] `/settings` page — shows plan, usage, Upgrade/Manage button, account info via `<UserButton>`

---

## Phase 6 — Docker ✅ COMPLETE · Deploy ❌ NOT STARTED

### Goal
One-command local stack and a production-ready deployment.

### Docker ✅
- [x] `infra/docker/agent.Dockerfile` — multi-stage: `builder` installs deps with `uv`, `runner` copies venv
- [x] `infra/docker/web.Dockerfile` — `next build` with `output: standalone`, copy `.next/standalone`
- [x] `docker-compose.yml`: `web`, `agent`, `mcp-server`, `postgres`, `redis`, `n8n`
- [x] `docker-compose.infra.yml`: postgres + redis + n8n only, for hot-reload local dev
- [x] `Makefile`: `make dev`, `make infra`, `make dev-agent`, `make dev-web`, `make mcp-server`, `make down`, `make migrate`, `make logs`, `make ps`

> **Migration gotcha (already handled):** `make migrate` invokes `python -m alembic`
> because the venv is built at `/build/.venv` and copied to `/app/.venv`, leaving
> console scripts with a stale absolute shebang.

### Deploy (Railway — recommended for MVP) ❌ NOT STARTED
- [ ] Connect GitHub repo to Railway
- [ ] Create services: `agent`, `web`, `postgres`, `redis`
- [ ] Set all env vars via Railway dashboard
- [ ] Agent entrypoint runs `alembic upgrade head` before `uvicorn`
- [ ] Custom domain via Railway + Cloudflare

> **n8n on Railway:** Railway's free tier sleeps — n8n cron triggers will miss. Use Railway's paid tier ($5–10/mo), n8n Cloud ($20/mo), or switch to `apscheduler` (no extra service needed).

### Alternative: Fly.io
```bash
flyctl launch --dockerfile infra/docker/agent.Dockerfile
flyctl postgres create
flyctl secrets set ANTHROPIC_API_KEY=...
flyctl deploy
```

---

## Phase 7 — Polish ✅ COMPLETE

### Goal
Production-quality observability, tests, and documentation.

### Evals
- [x] `tests/evals/golden_pairs.json` — question → expected-insight pairs
- [x] `tests/evals/test_pipeline.py` — runs the full pipeline against golden pairs, asserts key metrics appear in the answer
- [ ] Track eval pass rate over time (log to LangSmith project) — *not wired; runs are ad-hoc*

### Unit Tests
- [x] `tests/unit/test_planner.py` — mocks `llm.chat`, asserts plan structure
- [x] `tests/unit/test_analyst.py` — mocks `llm.chat`, asserts insight keys present
- [x] `tests/unit/test_retriever.py` — patches `mcp_client.*`, asserts `retrieved_data` shape
- [x] `tests/unit/test_summarizer.py` — mocks `llm.stream`, asserts `final_answer` non-empty
- [x] `tests/unit/test_graph_smoke.py` — end-to-end graph compile + run

### Observability
- [x] `structlog` across all nodes with `conversation_id`, `user_id`, `node`, `duration_ms` (`app/observability/`; JSON renderer in production, console in dev)
- [x] LangSmith settings (`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`)
- [x] `/metrics` endpoint via `prometheus-fastapi-instrumentator`

### Rate Limiting
- [x] `slowapi` middleware — 60 req/min per IP
- [ ] Separate concurrency cap for `/v1/query/stream` (10 concurrent streams per user) — *not implemented; per-user quota is enforced by `GatingMiddleware` instead*

### Documentation
- [x] `docs/diagrams/architecture.md` — Mermaid diagram of the full system
- [x] `docs/errors.md` — error contract for all connectors and nodes
- [x] `README.md` — quickstart, architecture overview, connector setup, Gmail Testing-mode limitation
- [x] ADRs `0001`–`0005`

---

## Phase 8 — MCP Connector Migration ✅ COMPLETE

> Added: 2026-06-16

### Goal
Expose connectors over the **Model Context Protocol** instead of calling connector
classes directly, so tools are standardized, discoverable, and reusable by any MCP
client. OAuth is unchanged — it remains the authorization layer supplying per-user
tokens; MCP is the transport/tool-discovery layer on top. Only `user_id` crosses the
MCP boundary, never a raw token (connectors resolve credentials from the DB themselves).

### Completed Tasks
- [x] FastMCP server (`app/mcp_server/server.py`) — auto-publishes `<connector>_list_resources` / `_read` / `_search` for every entry in `REGISTRY` (12 tools)
- [x] MCP client (`app/mcp_client.py`) — streamable-http session + structured-result unwrapping
- [x] `retriever_node` refactored to call MCP tools; caching + relevance filtering unchanged
- [x] `CONNECTOR_NAMES` exported from `app/connectors/__init__.py`; `REGISTRY` retained to back the server
- [x] Settings: `MCP_SERVER_URL` / `MCP_SERVER_HOST` / `MCP_SERVER_PORT`
- [x] New dependency: `mcp` (FastMCP / MCP Python SDK)
- [x] `mcp-server` service added to `docker-compose.yml`; `make mcp-server` target for local runs
- [x] `tests/unit/test_retriever.py` patches `mcp_client.*` instead of `REGISTRY`
- [x] Verified end-to-end in Docker: agent → `mcp-server` over the container network, tool calls logged as `CallToolRequest`

---

## Phase 9 — Security Hardening ✅ COMPLETE

> Added: 2026-07-27 · commits `1eb45bb`, `4146a2c`

### Goal
Close the gaps that Phase 8's new trust boundary opened, and stop insecure
development defaults from silently reaching production.

### Completed Tasks
- [x] **MCP confused deputy** — the MCP tools trust their `user_id` argument, so the server must only be reachable by the agent. Added a shared-secret gate: the client sends `X-Service-Secret` (`app/mcp_client.py`), the server verifies it with `hmac.compare_digest` (`app/mcp_server/server.py`). Empty secret = unauthenticated, dev only.
- [x] **Identity spoofing** — `GET /v1/connectors/status`, `DELETE /v1/connectors/{name}`, and every OAuth `/start` route now read `request.state.user_id` (verified from the Clerk JWT) instead of a caller-controlled `?user_id=` query param. Previously a caller could read or disconnect another user's sources, or bind their own OAuth grant to a victim's account.
- [x] **Insecure production defaults** — `Settings._require_secure_secrets_in_production` fails startup when `APP_ENV=production` and `CREDENTIAL_ENCRYPTION_KEY`, `WEBHOOK_SECRET`, or `MCP_SERVICE_SECRET` is missing or still the committed dev value. Without it, a forgetful deploy would encrypt every user's refresh token with a public key and accept forged webhooks.
- [x] **MCP client transport fix** — `streamable_http_client` takes no `headers` kwarg; auth now rides on a caller-supplied `httpx` client via `create_mcp_http_client` (the `streamablehttp_client` that accepts headers is deprecated).
- [x] **Error surfacing** — connector failures propagate as `connector_error` entries in `retrieved_data` and reach the user instead of being swallowed.

### New Settings
| Variable | Purpose |
|---|---|
| `MCP_SERVICE_SECRET` | Shared secret between agent and MCP server. **Required in production.** |

---

## Phase 10 — RAG Retrieval Layer ✅ COMPLETE (text sources)

### What it replaced
`retriever_node` called `<connector>_list_resources`, `_read` every resource, then kept
rows whose string form shared the most word-tokens with the question. That is lexical
truncation: it did not scale past a few resources, missed any match that shared no
words with the question, and silently dropped data.

### Completed Tasks
- [x] **Vector store** — `pgvector` in the existing Postgres (no new service);
      `document_chunks` with an **HNSW** index on `vector_cosine_ops`, scoped by
      `user_id`. HNSW rather than IVFFlat because IVFFlat needs a populated table to
      build meaningful lists, and this one is created empty (migration `0005`)
- [x] **Embeddings** — `voyage-3-lite` (512 dims), batched at ingest, never per query.
      `input_type` is `document` on ingest and `query` at search: Voyage's models are
      asymmetric and using one type for both measurably degrades recall. The model id
      is stored per chunk, so a model change invalidates the vectors even when the
      content has not changed
- [x] **Chunking** (`app/rag/chunking.py`) — Gmail splits per message with quoted reply
      history stripped; Notion packs paragraphs. Every chunk is prefixed with its
      source's title and sender, because a retrieved fragment is read alone
- [x] **Ingestion** — backfill on OAuth callback (backgrounded, so the user is not
      held on the redirect), incremental resync on the ticker
- [x] **Incremental sync** — `indexed_resources.revision` tracks Gmail's `historyId`
      and Notion's `last_edited_time`; unchanged resources are skipped
- [x] **Structured vs. text split** — decided and enforced: `TEXT_CONNECTORS` is
      `{gmail, notion}`, and Sheets never reaches the index
- [x] **Citations** — every vector entry carries `{connector, resource_id, title}`
- [x] **Index lifecycle** — dropped on disconnect, alongside the Redis cache

### Remaining
- [x] **Reranking** — `voyage rerank-2-lite` over the shortlist. pgvector returns
      `k * 4` candidates by cosine distance, the cross-encoder rescores them by
      reading the question and passage *together*, and `_MIN_RELEVANCE` cuts the
      tail. Falls back to the vector ordering when unconfigured or unreachable
- [ ] **Retrieval evals** — recall@k against a labelled set, separate from answer evals
- [ ] **Citations in the UI** — the data reaches the analyst; nothing renders it as a link
- [ ] **Original tasks not yet done**
- [ ] **Ingestion job** — pull connector resources on a schedule and on connect; normalize per source (sheet rows → records, Gmail threads → messages, Notion blocks → sections)
- [ ] **Chunking** — size/overlap per content type; preserve `source`, `resource_id`, and row/message offset as metadata
- [ ] **Vector store** — `pgvector` in the existing Postgres (no new service); `document_chunks` table with an ivfflat/hnsw index, scoped by `user_id`
- [ ] **Embeddings** — batch on ingest, not per query; store the model id so re-embedding is detectable
- [ ] **Hybrid retrieval** — vector similarity + the existing keyword scoring, then rerank; replaces `_filter_data`
- [ ] **Citations** — every insight carries `source` + `resource_id` + offset; the UI renders them as links. Highest-credibility win for a BI tool
- [ ] **Structured vs. text split** — decide explicitly: spreadsheets want SQL/pandas aggregation ("what was Q4 revenue?"), not nearest-neighbour lookup. Route tabular questions to computation and text questions to vector search
- [ ] **Incremental sync** — track a per-resource revision so a query does not refetch everything (currently blocked by there being no ingest step at all)
- [ ] **Retrieval evals** — recall@k against a labelled set, separate from the existing end-to-end answer evals

### Sequencing note
Incremental sync (Layer 2) and ingestion (Layer 3) are the same piece of work —
build the ingest pipeline once and both fall out of it.

---

## Phase 11 — CI + Production Deploy ❌ NOT STARTED

### CI
- [ ] `.github/workflows/ci.yml` — `ruff check`, `ruff format --check`, `pytest` on the agent; `tsc --noEmit` + `next build` on the web app
- [ ] Run on push and PR; required before merge to `main`
- [ ] Cache `uv` and `npm` layers

> There is currently no `.github/` directory — nothing lints or tests on push.

### Deploy
- [ ] Pick a target and record it in `docs/adr/0004-deploy-target.md` (the ADR exists; the deploy does not)
- [ ] Agent entrypoint runs `alembic upgrade head` before `uvicorn`
- [ ] All secrets set via the platform dashboard; verify `APP_ENV=production` triggers the Phase 9 secret validation
- [ ] `mcp-server` deployed on a private network — it must not be publicly reachable even with `MCP_SERVICE_SECRET` set
- [ ] Custom domain + TLS

---

## Other Remaining Work

Smaller items that do not warrant their own phase:

| Item | Why it matters | Effort |
|---|---|---|
| **OAuth state store is in-process** (`_pending` dict, `app/api/oauth.py`) | Breaks on restart and with more than one worker — every in-flight connect fails with "Invalid or expired OAuth state". Move to Redis. | ~30 min |
| **Clerk user-deletion webhook** | No path deletes a user's credentials, conversations, or plan row when the account goes away. | Small |
| **Connector reconnect UX** | A revoked token logs `connector_failed` and degrades the answer; the UI never tells the user to reconnect. | Small |
| **Connector pagination/quota** | Gmail mailboxes with hundreds of threads are read in full on every query. Largely subsumed by Phase 10's ingest step. | Medium |
| **Charts in the UI** | `analyst_node` returns metrics and trends that render as prose only. | Medium |
| **Frontend tests** | No test coverage in `apps/web`. | Medium |
| **Org/workspace multi-tenancy** | Connectors are per-user; teams cannot share a data source. Product decision, not a bug. | Large |

---

## Recommended Sequencing

Phases 1–9 are complete. Remaining order:

```
Phase 11 CI (do first — an afternoon, and it protects everything after)
  → OAuth state → Redis (30 min, blocks any multi-worker deploy)
  → Phase 10 RAG  (ingest → chunk → pgvector → hybrid retrieval → citations)
  → Phase 11 Deploy
  → Charts, frontend tests, reconnect UX
```

---

## Key Files Reference

| File | Purpose |
|---|---|
| `apps/agent/app/llm/__init__.py` | Anthropic SDK client, `chat()`, `stream()` |
| `apps/agent/app/graph/builder.py` | LangGraph compilation |
| `apps/agent/app/graph/state.py` | `AgentState` TypedDict |
| `apps/agent/app/graph/nodes/planner.py` | Decomposes question → plan |
| `apps/agent/app/graph/nodes/retriever.py` | Provider search via MCP tools, then ranks and trims |
| `apps/agent/app/graph/nodes/compute.py` | Model-written SQL over the untrimmed table (DuckDB, sandboxed) |
| `apps/agent/app/graph/nodes/analyst.py` | LLM-powered analysis |
| `apps/agent/app/graph/nodes/summarizer.py` | Streams final answer |
| `apps/agent/app/graph/nodes/action.py` | Triggers n8n workflows |
| `apps/agent/app/mcp_server/server.py` | FastMCP server exposing connectors as MCP tools; `X-Service-Secret` gate |
| `apps/agent/app/mcp_client.py` | Agent-side MCP client used by the retriever |
| `apps/agent/app/connectors/__init__.py` | Connector registry (`REGISTRY`) backing the MCP server |
| `apps/agent/app/db/models.py` | Encrypted credentials, plans, conversations, messages |
| `apps/agent/app/db/history_crud.py` | Conversation/message persistence, scoped by `user_id` |
| `apps/agent/app/api/query.py` | REST + SSE endpoints |
| `apps/agent/app/api/oauth.py` | OAuth start/callback for Sheets, Gmail, Notion |
| `apps/agent/app/api/conversations.py` | Conversation list/fetch/delete |
| `apps/agent/app/middleware/auth.py` | Clerk JWT verification → `request.state.user_id` |
| `apps/agent/app/middleware/gating.py` | Free-plan quota enforcement (402 on limit) |
| `apps/agent/app/config/settings.py` | Settings + production secret validation (Phase 9) |
| `apps/web/src/proxy.ts` | Clerk route protection (Next 16 middleware) |
| `apps/web/src/app/api/agent/[...path]/route.ts` | BFF proxy — injects the Clerk JWT |
| `infra/docker/docker-compose.yml` | Full stack: web, agent, mcp-server, postgres, redis, n8n |
| `infra/docker/docker-compose.infra.yml` | Infra only, for hot-reload local dev |
| `docs/errors.md` | Connector/node error contract |
| `docs/adr/` | Architecture decision records |

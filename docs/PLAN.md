# BI Agent — Full Plan of Action

> Generated: 2026-04-22  
> Last updated: 2026-04-22 (revised per architectural review)  
> Stack: Python · FastAPI · LangGraph · Anthropic Claude · Next.js · PostgreSQL · Redis · n8n

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
Replace the mock connector with live integrations to Google Sheets, Notion, and Gmail. Store OAuth tokens securely per user.

### Completed Tasks
- [x] Async SQLAlchemy engine + session factory (`app/db/engine.py`)
- [x] `UserConnectorCredential` model — Fernet-encrypted credentials at rest (`app/db/models.py`)
- [x] `get_credentials` / `upsert_credentials` CRUD (`app/db/crud.py`)
- [x] Google Sheets connector — lists spreadsheets via Drive API, reads rows with header mapping
- [x] Notion connector — lists/reads pages, extracts plain text from blocks, falls back to shared integration key
- [x] Gmail connector — lists thread summaries, reads full threads, supports Gmail search queries
- [x] Shared `REGISTRY` dict in `app/connectors/__init__.py`
- [x] Retriever updated to pull from `REGISTRY`
- [x] Settings extended: `google_client_id`, `google_client_secret`, `google_redirect_uri`, `notion_api_key`, `credential_encryption_key`
- [x] New dependencies: `google-api-python-client`, `google-auth-oauthlib`, `notion-client`, `alembic`, `cryptography`

### Remaining Tasks
- [x] **Alembic setup** — `infra/db/migrations/` with async env.py + first migration for `user_connector_credentials`
- [x] **Split Google OAuth into two flows** — separate consent screens for Sheets/Drive and Gmail
  - `GET /v1/oauth/google-sheets/start?user_id=` — scopes: `spreadsheets.readonly`, `drive.readonly`
  - `GET /v1/oauth/google-sheets/callback` — exchange code, upsert credentials under key `"google_sheets"`
  - `GET /v1/oauth/gmail/start?user_id=` — scope: `gmail.readonly`
  - `GET /v1/oauth/gmail/callback` — exchange code, upsert credentials under key `"gmail"`
- [x] **Notion OAuth** — `GET /v1/oauth/notion/start` + `GET /v1/oauth/notion/callback`
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
| `NOTION_API_KEY` | Notion internal integration token (shared fallback) |
| `CREDENTIAL_ENCRYPTION_KEY` | 32-char key for Fernet encryption |
| `DATABASE_URL` | `postgresql+asyncpg://biagent:biagent@localhost/biagent` |
| `REDIS_URL` | `redis://localhost:6379` |

---

## Phase 2.5 — Minimal Auth Stub

### Goal
Eliminate the `"anonymous"` user_id hardcoded throughout the pipeline **before** the frontend is built, so Phase 4 and 5 have a clean foundation. This is ~2 hours of work that avoids a grep-and-replace across the whole codebase later.

### Why not just wait for Phase 5?
Every file written between now and Phase 5 that touches `user_id` defaults to `"anonymous"`. Adding the stub now means Phase 5 only needs to swap the verification logic — not hunt down every callsite.

### Tasks
- [ ] **FastAPI middleware** `app/middleware/auth.py` — reads `X-User-Id` header, trusts it blindly in dev, writes `request.state.user_id`; returns `anonymous` if header is absent (not 401 — that's Phase 5's job)
- [ ] **Wire middleware** into `app/main.py`
- [ ] **Update all nodes** (`planner`, `retriever`, `analyst`, `summarizer`) to read `user_id` from `state["user_id"]` — already done; confirm no hardcoded fallbacks remain
- [ ] **Update API endpoints** to populate `state["user_id"]` from `request.state.user_id` instead of `req.user_id or "anonymous"`
- [ ] **Document the swap point** in `app/middleware/auth.py` with a comment: `# Phase 5: replace trust-header logic with Clerk JWT verification`

> **Security note for Phase 5:** When Clerk is added, pass the **Clerk JWT** via `Authorization: Bearer <token>` — never trust a client-supplied `X-User-Id` header in production. The middleware's job in Phase 5 is to *verify* the JWT and *extract* `user_id` from its claims, not to accept it from the request.

---

## Phase 3 — n8n Automation Workflows

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

**Recommendation:** Use `apscheduler` for MVP (saves ops cost, simpler deploy). Use n8n if the visual workflow editor is part of the demo story.

### Tasks (n8n path)
- [ ] **Settings** — add `N8N_BASE_URL`, `N8N_API_KEY`, `WEBHOOK_SECRET`
- [ ] **Inbound webhook** — `POST /v1/webhooks/n8n` — validates HMAC signature (`WEBHOOK_SECRET`), routes event to pipeline
- [ ] **Outbound trigger** — `POST /v1/workflows/trigger` — agent calls n8n REST API to activate a workflow by ID
- [ ] **LangGraph action node** — `action_node` added to graph; planner includes `"action"` step when question implies scheduling; node calls `/v1/workflows/trigger`
- [ ] **n8n workflow JSONs** in `apps/n8n/`:
  - `scheduled_report.json` — cron → `POST /v1/query` → email result
  - `data_change_alert.json` — Sheets trigger → `POST /v1/query` → Slack/email alert
- [ ] **Import script** — `apps/n8n/import.sh` POSTs workflow JSONs to n8n API on first run

### Tasks (apscheduler path)
- [ ] Add `apscheduler>=3.10` to `pyproject.toml`
- [ ] `app/scheduler.py` — `AsyncIOScheduler` started in FastAPI lifespan
- [ ] `POST /v1/schedules` — create a recurring query job (cron expression + question + delivery target)
- [ ] `GET /v1/schedules` — list user's scheduled jobs
- [ ] `DELETE /v1/schedules/{id}` — remove a job
- [ ] `schedules` table in DB — `id`, `user_id`, `cron`, `question`, `delivery` (email/webhook)

---

## Phase 4 — Next.js Dashboard

### Goal
A chat UI that streams answers in real time, shows pipeline progress, and lets users connect their data sources.

### Scaffold
```bash
cd apps/web
npx create-next-app . --typescript --tailwind --app --src-dir
```

### Tasks

#### Chat UI (`/`)
- [ ] `<ChatInput>` — textarea + submit, sends `POST /v1/query/stream` with `Authorization` header
- [ ] `<StageIndicator>` — progress bar cycling through planning/retrieving/analyzing/summarizing on `stage` events
- [ ] `<MessageBubble>` — renders streamed text by appending `chunk` event content
- [ ] `<ConversationSidebar>` — lists past conversations, loads history on click
- [ ] Abort controller — cancel in-flight SSE when user sends a new message
- [ ] Optimistic UI — show user message immediately before response arrives
- [ ] `lib/sse.ts` — reusable hook `useAgentStream(question)` returning `{ stage, answer, done }`

#### Connector Onboarding (`/connect`)
- [ ] Fetch `/v1/connectors/status` on page load to show connected/disconnected state per connector
- [ ] Separate "Connect Google Sheets" and "Connect Gmail" buttons (two distinct OAuth flows)
- [ ] "Connect Notion" button
- [ ] Post-OAuth success redirect back to `/connect` with status refresh
- [ ] "Disconnect" button per connector (DELETE `/v1/connectors/{name}?user_id=`)

#### Settings (`/settings`)
- [ ] Display plan (free/pro) and queries used today
- [ ] Stripe Customer Portal link for plan management

#### Infrastructure
- [ ] `NEXT_PUBLIC_AGENT_URL` env var
- [ ] BFF API routes at `app/api/agent/[...path]/route.ts` — proxy to FastAPI, inject `Authorization: Bearer <clerk-jwt>` header server-side (keeps JWT off the client for non-SSE requests)

---

## Phase 5 — Auth + Stripe

### Goal
Real user identity so credentials are isolated per account, plus a paywall for pro features.

### Auth (Clerk — recommended for Next.js)
- [ ] Install Clerk in `apps/web`, wrap layout with `<ClerkProvider>`
- [ ] Sign-in / sign-up pages at `/sign-in` and `/sign-up`
- [ ] Next.js middleware to protect all routes except landing page
- [ ] BFF routes inject `Authorization: Bearer <clerk-jwt>` on every FastAPI request
- [ ] **Replace auth stub in `app/middleware/auth.py`** — verify Clerk JWT using Clerk's JWKS endpoint; extract `user_id` from verified claims; return 401 if token is missing or invalid
  - Use `Authorization: Bearer <token>` — **never trust a client-set `X-User-Id` header** in production
- [ ] Remove `"anonymous"` fallback from all pipeline code

### Stripe
- [ ] Create products: **Free** (mock connector only, **3 queries/day**) and **Pro** ($X/mo, all connectors, unlimited)
  - Free tier is 3/day, not 10 — at ~10k tokens/query, 10/day × 100 free users = 30M tokens/month, real cost
- [ ] `POST /v1/stripe/webhook` — handle `customer.subscription.created`, `updated`, `deleted`
- [ ] `user_plan` table in DB — `user_id`, `plan` (`free`|`pro`), `queries_today`, `reset_at`
- [ ] Gating middleware — check plan before invoking pipeline, return 402 if over limit
- [ ] Stripe Customer Portal link in `/settings` for self-serve plan management

---

## Phase 6 — Docker + Deploy

### Goal
One-command local stack and a production-ready deployment.

### Docker
- [ ] `infra/docker/agent.Dockerfile` — multi-stage: `builder` installs deps with `uv`, `runner` copies venv
- [ ] `infra/docker/web.Dockerfile` — `next build` with `output: standalone`, copy `.next/standalone`
- [ ] Update `docker-compose.yml`: add `agent` and `web` services alongside postgres + redis
- [ ] `Makefile` at repo root: `make up`, `make down`, `make migrate`, `make logs`

### Deploy (Railway — recommended for MVP)
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

## Phase 7 — Polish

### Goal
Production-quality observability, tests, and documentation.

### Evals
- [ ] `tests/evals/golden_pairs.json` — 20+ question → expected-insight pairs
- [ ] `tests/evals/test_pipeline.py` — run full pipeline against golden pairs, assert key metrics appear in answer
- [ ] Track eval pass rate over time (log to LangSmith project)

### Unit Tests
- [ ] `tests/unit/test_planner.py` — mock `llm.chat`, assert plan structure
- [ ] `tests/unit/test_analyst.py` — mock `llm.chat`, assert insight keys present
- [ ] `tests/unit/test_retriever.py` — mock connector, assert `retrieved_data` shape
- [ ] `tests/unit/test_summarizer.py` — mock `llm.stream`, assert `final_answer` non-empty

### Observability
- [ ] Wire `structlog` across all nodes with `conversation_id`, `user_id`, `stage`, `duration_ms`
- [ ] LangSmith tracing enabled via `LANGCHAIN_TRACING_V2=true` + `LANGSMITH_API_KEY`
- [ ] `/metrics` endpoint (Prometheus-compatible) for query count, latency p50/p95

### Rate Limiting
- [ ] Add `slowapi` middleware: 60 req/min per IP on all endpoints
- [ ] Separate limit for `/v1/query/stream`: 10 concurrent streams per user

### Documentation
- [ ] `docs/diagrams/architecture.md` — Mermaid diagram of full system
- [ ] `docs/errors.md` — error contract for all connectors and nodes (what each returns on failure)
- [ ] Final `README.md` — quickstart, architecture overview, connector setup guides, deploy instructions, Gmail Testing mode limitation
- [ ] ADRs: `0002-scheduling-backend.md`, `0003-auth-provider.md`, `0004-deploy-target.md`, `0005-stripe-vs-manual-billing.md`

---

## Recommended Sequencing

```
Phase 1 ✅
  → Phase 2  (finish OAuth, migrations, Redis cache, /connectors/status)
  → Phase 2.5 (auth stub — eliminates "anonymous" before frontend is written)
  → Phase 4  (frontend, unblocked by Phase 2 OAuth + connector status endpoint)
  → Phase 5  (replace auth stub with Clerk + add Stripe)
  → Phase 3  (n8n/apscheduler — needs real user identity to be useful)
  → Phase 6  (deploy)
  → Phase 7  (polish)
```

---

## Key Files Reference

| File | Purpose |
|---|---|
| `apps/agent/app/llm/__init__.py` | Anthropic SDK client, `chat()`, `stream()` |
| `apps/agent/app/graph/builder.py` | LangGraph compilation |
| `apps/agent/app/graph/state.py` | `AgentState` TypedDict |
| `apps/agent/app/graph/nodes/planner.py` | Decomposes question → plan |
| `apps/agent/app/graph/nodes/retriever.py` | Fetches data via connectors |
| `apps/agent/app/graph/nodes/analyst.py` | LLM-powered analysis |
| `apps/agent/app/graph/nodes/summarizer.py` | Streams final answer |
| `apps/agent/app/connectors/__init__.py` | Connector registry |
| `apps/agent/app/db/models.py` | Encrypted credential storage |
| `apps/agent/app/api/query.py` | REST + SSE endpoints |
| `infra/docker/docker-compose.yml` | Local Postgres + Redis |
| `docs/errors.md` | Connector/node error contract |
| `docs/adr/` | Architecture decision records |

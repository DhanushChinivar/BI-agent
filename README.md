# AI Business Intelligence Agent

A full-stack, multi-agent BI platform. Connect Google Sheets, Gmail, and Notion, ask natural-language questions, and get streamed insights — with scheduled reports and data alerts via n8n.

## Architecture

See [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) for the full Mermaid diagram.

```
Browser → Next.js (Clerk auth) → FastAPI BFF proxy
                                      ↓
                             LangGraph pipeline
              planner → retriever → compute → analyst → summarizer → [action_node]
                            ↓ (MCP client)    ↓                            ↓
                     FastMCP connector server DuckDB                      n8n
                            ↓
                   Google Sheets / Gmail / Notion
```

Connectors are exposed over the **Model Context Protocol (MCP)**: a FastMCP server
publishes each connector's `list_resources` / `read` / `search` as MCP tools, and the
retriever consumes them as an MCP client. OAuth remains the authorization layer that
supplies per-user tokens — MCP is the transport/tool-discovery layer on top.

**Two retrieval paths, chosen by source type.** Gmail threads and Notion pages are
chunked, embedded with `voyage-3-lite`, and stored in pgvector. A question shortlists
candidates by cosine distance, then a **cross-encoder reranks them** — measurement showed
distance alone cannot tell a genuine match from a topically adjacent miss, so the
reranker is what lets the agent say "nothing in your data answers this". Answers come
back with citations. Spreadsheets are deliberately
*not* embedded — "what was Q4 revenue?" needs an exact sum over every row, and
nearest-neighbour lookup over embedded rows answers a different question convincingly.

Totals are therefore **computed, not inferred**. On aggregation, trend, and comparison
questions `compute_node` loads the untrimmed table into an in-memory DuckDB, asks Claude
for a single `SELECT`, and executes it — so a sum covers every row rather than the sample
the analyst can fit in a prompt. Other question types skip the node entirely.

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 16, Tailwind CSS, Clerk |
| Backend | FastAPI, LangGraph, Anthropic Claude |
| Connectors | MCP (FastMCP server + client) over streamable-http |
| Compute | DuckDB (in-memory, external access disabled) for exact aggregates |
| Retrieval | Voyage `voyage-3-lite` embeddings + pgvector (HNSW, cosine), reranked with `rerank-2-lite` |
| Database | PostgreSQL 16 + pgvector (credentials, plans, history, schedules, chunks) |
| Cache | Redis 7 (connector data, 5-min TTL) |
| Automation | n8n (scheduled reports, data alerts) |
| Billing | Stripe (free: 3 queries/day, pro: unlimited) |
| Auth | Clerk (JWT, managed JWKS) |

## Quick start

**Prerequisites:** Docker Desktop, an [Anthropic API key](https://console.anthropic.com),
and a [Clerk](https://clerk.com) application (free tier is fine). Everything else is
optional and the app degrades without it.

### 1. Clone and create both env files

Two are needed — a missing `apps/web/.env.local` fails the build, not just a feature.

```bash
git clone https://github.com/DhanushChinivar/BI-agent.git
cd BI-agent

cp apps/agent/.env.example apps/agent/.env
cp apps/web/.env.example   apps/web/.env.local
```

### 2. Fill in the two required keys

| File | Variable | Where to get it |
|---|---|---|
| `apps/agent/.env` | `ANTHROPIC_API_KEY` | console.anthropic.com |
| `apps/web/.env.local` | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk dashboard → API Keys |
| `apps/web/.env.local` | `CLERK_SECRET_KEY` | same page |

> `NEXT_PUBLIC_*` is inlined into the browser bundle by `next build`, so it must be
> present *before* the image is built. `make dev` reads `apps/web/.env.local` and passes
> them as build args; the build fails with a named error rather than producing an image
> whose UI silently hangs.

### 3. Start it

```bash
make dev
```

Migrations run automatically as the agent boots (`alembic upgrade head`), so there is no
separate step. First run pulls images and builds — allow a few minutes.

Open **http://localhost:3000**, sign up, and you are in.

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| Agent API docs | http://localhost:8000/docs |
| n8n | http://localhost:5678 — creates an owner account on first visit |
| Prometheus metrics | http://localhost:8000/metrics |

## Other make commands

```bash
make down       # stop all services
make logs       # tail logs from all services
make migrate    # run DB migrations inside the agent container
make build      # rebuild images after code changes
make ps         # show running containers
```

## Connector setup

> Connectors are served as MCP tools by the `mcp-server` container (started automatically
> by `make dev`). OAuth is unchanged — the steps below still grant per-user tokens; the
> retriever just reaches the connectors through MCP rather than calling them directly.

### Google Sheets & Gmail
1. Create a project at [console.cloud.google.com](https://console.cloud.google.com)
2. Enable **Google Sheets API**, **Google Drive API**, and **Gmail API**
3. Create OAuth 2.0 credentials → download client ID + secret
4. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` in `apps/agent/.env`
5. Visit `http://localhost:8000/v1/oauth/google-sheets/start?user_id=<your-id>` to connect

> **Gmail note:** The `gmail.readonly` scope requires Google OAuth verification for public apps. Keep your GCP project in **Testing mode** (max 100 users) for local/portfolio use.

### Notion
1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration**
2. Copy the **Client ID** and **Client Secret**
3. Set `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET` in `apps/agent/.env`
4. Visit `http://localhost:8000/v1/oauth/notion/start?user_id=<your-id>` to connect

### Semantic search over Gmail and Notion

Retrieval over text sources needs an embedding key. Anthropic sells no embedding model,
so this uses [Voyage](https://voyageai.com), its recommended partner:

```bash
# apps/agent/.env
VOYAGE_API_KEY=pa-...
```

Without it the project still runs — `enabled()` is false, indexing is skipped, and the
retriever falls back to each provider's own search API. Degraded, not broken.

Connecting Gmail or Notion kicks off a backfill in the background, and the `schedule_ticker`
workflow re-syncs every 5 minutes, skipping resources whose revision has not changed.
Check progress with `GET /v1/index/status`, or force a pass with `POST /v1/index/sync`.

### Scheduled reports

Schedules live in Postgres (`scheduled_reports`), not in n8n. n8n runs exactly one
workflow — `schedule_ticker` — which every 5 minutes calls `POST /v1/schedules/run-due`,
signed with `WEBHOOK_SECRET`; the agent claims whatever is due, runs it, and returns the
answers for n8n to email.

Setting it up is four steps, and each one fails silently if skipped — the workflow stays
green on every tick that has nothing to send, so a broken step only shows up the first
time a report is actually due.

**1.** Add the delivery addresses to `apps/agent/.env` (the Makefile exports them into
the n8n container; that file is gitignored, so a personal address stays out of the repo):

```bash
REPORT_FROM_EMAIL=you@example.com
REPORT_TO_EMAIL=you@example.com
```

**2.** Open http://localhost:5678, create the owner account, then **Credentials → New →
SMTP** and fill in your mail server. For Gmail use an
[app password](https://myaccount.google.com/apppasswords), not your account password.

**3.** Import and activate the workflow:

```bash
N8N_API_KEY=<key from n8n Settings → API> make import-workflows
```

**4.** In the n8n editor, open `schedule_ticker` → the **Send Report Email** node → set
its credential to the SMTP account you just made. Credentials are specific to an n8n
instance and are never carried in the workflow file, so this link cannot be imported.

Then ask the agent *"email me this every Monday"*, or manage schedules directly via
`GET/POST /v1/schedules` and `DELETE /v1/schedules/{id}`.

To check it worked, make a schedule due and watch it run:

```bash
docker compose -f infra/docker/docker-compose.yml exec -T postgres \
  psql -U biagent -d biagent -c \
  "update scheduled_reports set next_run_at = now() - interval '1 minute';"
```

Within five minutes `last_status` should read `ok` and `next_run_at` should have advanced.

## Production configuration

Setting `APP_ENV=production` turns on a startup check that refuses to boot on
insecure defaults. Three secrets must be set to strong, unique values:

| Variable | Why |
|---|---|
| `CREDENTIAL_ENCRYPTION_KEY` | Encrypts every user's OAuth refresh token at rest. The committed dev default is public. |
| `WEBHOOK_SECRET` | HMAC key for inbound n8n webhooks. Without it, anyone can forge pipeline triggers. |
| `MCP_SERVICE_SECRET` | Shared secret between the agent and the MCP server. |

> **MCP server placement:** the MCP tools trust the `user_id` they are called with —
> authorization happens at the agent's Clerk-JWT boundary, not inside the tools.
> Deploy `mcp-server` on a private network so it is unreachable from the internet.
> The shared secret is defence in depth, not a substitute for network isolation.

## Running tests

```bash
cd apps/agent

# Unit tests (no API key needed — LLM is mocked)
uv run pytest tests/unit/ -v

# Pipeline evals (real LLM calls — requires ANTHROPIC_API_KEY)
uv run pytest tests/evals/ -v --timeout=120
```

## Docs

- [`docs/DATAFLOW.md`](docs/DATAFLOW.md) — Step-by-step trace of every operation: auth, OAuth, query pipeline, billing, scheduling
- [`docs/PLAN.md`](docs/PLAN.md) — Phase-by-phase plan, current status, and remaining work
- [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) — Mermaid architecture diagram, trust boundaries, known gaps
- [`docs/adr/`](docs/adr/) — Architecture Decision Records
- [`docs/errors.md`](docs/errors.md) — Connector and node error contract
- [`apps/agent/.env.example`](apps/agent/.env.example) — All environment variables with inline docs

## Status

Phases 1–10 are complete (pipeline, connectors, OAuth, dashboard, auth, billing,
n8n automation, Docker, MCP migration, security hardening, RAG retrieval for text
sources). Remaining: retrieval evals (recall@k), rendering citations in the UI, CI, and
deployment. See [`docs/PLAN.md`](docs/PLAN.md) for the breakdown.

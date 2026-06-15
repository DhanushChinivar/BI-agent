# AI Business Intelligence Agent

A full-stack, multi-agent BI platform. Connect Google Sheets, Gmail, and Notion, ask natural-language questions, and get streamed insights — with scheduled reports and data alerts via n8n.

## Architecture

See [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) for the full Mermaid diagram.

```
Browser → Next.js (Clerk auth) → FastAPI BFF proxy
                                      ↓
                             LangGraph pipeline
                      planner → retriever → analyst → summarizer → [action_node]
                                   ↓ (MCP client)                        ↓
                            FastMCP connector server                    n8n
                                   ↓
                          Google Sheets / Gmail / Notion
```

Connectors are exposed over the **Model Context Protocol (MCP)**: a FastMCP server
publishes each connector's `list_resources` / `read` / `search` as MCP tools, and the
retriever consumes them as an MCP client. OAuth remains the authorization layer that
supplies per-user tokens — MCP is the transport/tool-discovery layer on top.

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 16, Tailwind CSS, Clerk |
| Backend | FastAPI, LangGraph, Anthropic Claude |
| Connectors | MCP (FastMCP server + client) over streamable-http |
| Database | PostgreSQL 16 (credentials + plans) |
| Cache | Redis 7 (connector data, 5-min TTL) |
| Automation | n8n (scheduled reports, data alerts) |
| Billing | Stripe (free: 3 queries/day, pro: unlimited) |
| Auth | Clerk (JWT, managed JWKS) |

## Quick start

**Prerequisites:** Docker Desktop, an Anthropic API key.

```bash
git clone https://github.com/DhanushChinivar/BI-agent.git
cd BI-agent

# Fill in your API key
cp apps/agent/.env.example apps/agent/.env
# Edit apps/agent/.env — set ANTHROPIC_API_KEY

# Start everything
make dev
```

Open **http://localhost:3000** in your browser.

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| Agent API docs | http://localhost:8000/docs |
| n8n | http://localhost:5678 (admin / admin) |
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

### n8n workflows
After `make dev`, import the bundled workflows:
```bash
N8N_BASE_URL=http://localhost:5678 N8N_API_KEY=<your-key> ./apps/n8n/import.sh
```
This imports `scheduled_report` (cron → query → email) and `data_change_alert` (webhook → query → email).

## Running tests

```bash
cd apps/agent

# Unit tests (no API key needed — LLM is mocked)
uv run pytest tests/unit/ -v

# Pipeline evals (real LLM calls — requires ANTHROPIC_API_KEY)
uv run pytest tests/evals/ -v --timeout=120
```

## Docs

- [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) — Mermaid architecture diagram
- [`docs/adr/`](docs/adr/) — Architecture Decision Records
- [`docs/errors.md`](docs/errors.md) — Connector and node error contract
- [`apps/agent/.env.example`](apps/agent/.env.example) — All environment variables with inline docs

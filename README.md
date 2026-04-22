# AI Business Intelligence Agent

Multi-agent BI platform: connect Sheets/Notion/Gmail, ask natural-language questions,
get insights, reports, and automated workflows.

## Repo layout

```
apps/
  agent/        Python + FastAPI + LangGraph (the brain)
  web/          Next.js app (UI + BFF)                      [Phase 4]
  n8n/          n8n workflows as JSON                        [Phase 3]
infra/
  docker/       Dockerfiles + compose
  db/           Migrations
docs/
  adr/          Architecture Decision Records
  diagrams/     Architecture diagram(s)
```

## Quick start (agent only, Phase 1)

```bash
cd apps/agent
uv sync                      # install deps
cp .env.example .env         # fill in secrets
uv run uvicorn app.main:app --reload
```

Then: `curl -X POST http://localhost:8000/v1/query -d '{"message":"hello"}'`

## Phases

1. Core LangGraph multi-agent pipeline  ← **current**
2. MCP integrations (Sheets, Notion, Gmail)
3. n8n automation workflows
4. Next.js dashboard
5. Auth + Stripe
6. Docker + deploy
7. Polish, README, architecture diagram

See `docs/` for architecture and ADRs.

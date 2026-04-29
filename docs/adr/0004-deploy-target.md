# ADR 0004 — Deploy Target: Docker Compose (local)

**Date:** 2026-04-29  
**Status:** Accepted

## Context

The project needed a deployment strategy. Options ranged from fully managed cloud platforms to local Docker Compose.

## Options

| Option | Cost | Complexity |
|---|---|---|
| **Railway** | ~$5–15/month | Low — connect GitHub, set env vars |
| **Render + Supabase + Upstash** | Free tier (with limitations) | Medium — separate services |
| **Docker Compose (local)** | Free | Low — one command |
| **Fly.io** | Free allowance for small VMs | Medium |

## Decision

**Docker Compose (local)** — for a portfolio/personal project where external access is not required, running the full stack locally via `make dev` is free, fast, and requires no cloud accounts beyond what the APIs already need.

## Consequences

- `make dev` spins up all 5 services: postgres, redis, agent, web, n8n
- `make down` tears everything down cleanly
- Migrating to Railway or Fly.io later only requires adding a `railway.json` / `fly.toml` — the Dockerfiles are already production-ready
- No ongoing cost

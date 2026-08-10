# ADR 0002 — Scheduling Backend: n8n vs apscheduler

**Date:** 2026-04-29  
**Status:** Accepted

## Context

Phase 3 requires the ability to schedule recurring BI reports and trigger alerts when data changes. Two options were evaluated.

## Options

| Option | Pros | Cons |
|---|---|---|
| **n8n (self-hosted)** | Visual workflow editor, rich integrations, webhook triggers, separate from app process | Needs always-on service, extra Docker container |
| **apscheduler in-process** | No extra service, simpler deploy, one less container | No visual editor, less flexible integrations |

## Decision

**n8n** — chosen because the visual workflow editor is part of the demo story and n8n provides native email/Slack/webhook integrations without writing extra code. For a portfolio project it also makes the automation capabilities visible and impressive.

n8n runs as a Docker container alongside the agent. The workflow JSON is version-controlled in `infra/n8n/workflows/` and imported via `apps/agent/scripts/import_workflows.sh`.

## Consequences

- The stack has one additional service (n8n container)
- Workflows are defined as JSON and imported idempotently on setup
- The inbound `POST /v1/webhooks/n8n` endpoint receives n8n callbacks and runs the pipeline

## Amendment (2026-08-10) — n8n executes, Postgres remembers

The original decision left *where schedules live* implicit, and the implementation put them in n8n. That does not work, for a reason that is structural rather than a bug:

**n8n has no per-user state.** Both workflows read their question and cron from instance-level `$env`/`$vars`, so a deployment had one global schedule shared by every user. `action_node` tried to paper over this by PATCHing the workflow per request, but there was nowhere to put a per-user cron — it wrote only `{"active": true}` and a copy of the workflow's own tags, then called `POST /api/v1/workflows/{id}/run`, which is not part of n8n's public API. It returned `{"status": "scheduled"}` regardless, so the UI confirmed schedules that were never created.

Two ways out were considered:

| Option | Why not / why |
|---|---|
| **A workflow per schedule**, created through n8n's API with the cron baked into the node | Genuinely works, but the schedule list becomes unreadable without calling n8n, every schedule is lost if the n8n volume is recreated, there is nowhere to record whether the last run succeeded, and it cannot be tested without a live n8n |
| **Postgres owns schedules; n8n owns the tick** ✅ | A `scheduled_reports` table, a `/v1/schedules` CRUD API, and one `schedule_ticker` workflow calling `POST /v1/schedules/run-due` every 5 minutes. Testable with no n8n running, survives an n8n reinstall, and gives the UI something to list |

The second was taken. n8n keeps the roles it was actually chosen for — the visual editor, the cron tick, and email delivery — and stops being a database.

Claiming is `SELECT ... FOR UPDATE SKIP LOCKED` with `next_run_at` advanced **before** the pipeline runs, so a slow run overlapping the next tick, or a second agent replica, cannot both send the same report. The trade is that a run lost to a crash mid-flight is skipped rather than retried; for a recurring report, a missed edition beats a duplicate one.

This also means the agent no longer calls n8n's API at all — traffic is inbound only, verified by `WEBHOOK_SECRET`. `POST /v1/workflows/trigger` was deleted with it.

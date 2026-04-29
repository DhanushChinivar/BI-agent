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

n8n runs as a Docker container alongside the agent. Workflow JSONs are version-controlled in `apps/n8n/workflows/` and imported via `apps/n8n/import.sh`.

## Consequences

- The stack has one additional service (n8n container)
- Workflows are defined as JSON and imported idempotently on setup
- The `action_node` in LangGraph calls the n8n REST API to activate workflows
- The inbound `POST /v1/webhooks/n8n` endpoint receives n8n callbacks and runs the pipeline

# ADR-0001: Split agent service from Next.js BFF

**Status:** Accepted
**Date:** 2026-04-22

## Context

The BI agent needs to run LangGraph pipelines, call LLM providers, and integrate with MCP servers. The user-facing app needs auth, Stripe, and a polished UI. Both could in principle live in one Next.js codebase using the Vercel AI SDK.

## Decision

Split into two services:
- `apps/agent` — Python (FastAPI + LangGraph), the intelligence plane.
- `apps/web` — Next.js, the presentation plane / BFF.

They communicate over versioned HTTP (`POST /v1/query`, SSE for streaming).

## Consequences

**Positive**
- Python has the strongest agent/LLM tooling (LangGraph, LangChain, MCP SDKs).
- Services scale independently — agent workloads are bursty and CPU/network-heavy; the web app is latency-sensitive and mostly static.
- Clear seams make testing and swapping implementations easier.
- Mirrors how real teams actually deploy this — useful signal for hiring managers.

**Negative**
- Extra network hop between UI and agent.
- Two deploy targets, two runtimes, two dependency graphs.
- Need a versioned API contract from day one.

## Alternatives considered

- **All-in-one Next.js with AI SDK.** Simpler to ship, but locks us out of the Python agent ecosystem and conflates concerns.
- **tRPC between Next.js and a Python agent.** Would require a codegen step or giving up type-sharing. Plain REST + OpenAPI is good enough and more portable.

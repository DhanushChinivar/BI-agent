# ADR 0003 — Auth Provider: Clerk vs Auth.js vs Custom JWT

**Date:** 2026-04-29  
**Status:** Accepted

## Context

The application needs user authentication for the Next.js frontend and JWT verification in the FastAPI backend.

## Options

| Option | Pros | Cons |
|---|---|---|
| **Clerk** | Best-in-class Next.js integration, managed JWKS, pre-built UI components | SaaS dependency, free tier limits |
| **Auth.js (NextAuth)** | Open source, self-hostable | More boilerplate, no managed JWKS |
| **Custom JWT** | Full control | Significant implementation effort, security risk |

## Decision

**Clerk** — chosen for its first-class Next.js App Router support and managed JWKS endpoint. The FastAPI middleware fetches the JWKS once and verifies JWTs locally without a network round-trip per request.

## Consequences

- `CLERK_FRONTEND_API` env var required for JWKS URL construction
- FastAPI `AuthMiddleware` caches the JWKS via `@lru_cache` (restart service to rotate keys)
- In development, the middleware falls back to trusting the `X-User-Id` header
- In production (`APP_ENV=production`), missing or invalid JWTs return 401

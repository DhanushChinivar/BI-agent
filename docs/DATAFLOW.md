# Data & API Flows

> Last updated: 2026-08-10. Traced against the code, not the design docs — where
> the two disagree, this file follows the code. Line references are links.
>
> **Rendered version with full swimlane diagrams:**
> https://claude.ai/code/artifact/bfb2addc-2aad-47d3-9b6b-c3f746979a57

A step-by-step trace of every operation the system performs: what calls what, what
crosses which trust boundary, where data is stored, and how each step fails.

| # | Operation | Entry point |
|---|---|---|
| 0 | [The middleware chain](#0-the-middleware-chain-every-request) | every request |
| 1 | [Sign up / sign in](#1-sign-up--sign-in) | `/sign-up`, `/sign-in` |
| 2 | [Connect a data source](#2-connect-a-data-source-oauth) | `/connect` |
| 3 | [Ask a question (streaming)](#3-ask-a-question-streaming--the-main-flow) | `POST /v1/query/stream` |
| 4 | [Ask a question (non-streaming)](#4-ask-a-question-non-streaming) | `POST /v1/query` |
| 5 | [Conversation history](#5-conversation-history) | `GET /v1/conversations` |
| 6 | [Connector status / disconnect](#6-connector-status--disconnect) | `GET /v1/connectors/status` |
| 7 | [Upgrade to Pro](#7-upgrade-to-pro-stripe) | `/settings` |
| 8 | [Schedule a report](#8-schedule-a-report-agent--n8n) | `action_node` |
| 9 | [A scheduled report fires](#9-a-scheduled-report-fires-n8n--agent) | `POST /v1/webhooks/n8n` |
| 10 | [Health & metrics](#10-health--metrics) | `/health`, `/metrics` |

**Read [§11 Known gaps](#11-known-gaps) before deploying** — the production-401 problem
that used to head this section is fixed; what remains is listed there.

---

## 0. The middleware chain (every request)

Every request to the agent passes through four middlewares before reaching a route.
Order matters and is **the reverse of the registration order** in
[main.py:41-51](../apps/agent/app/main.py#L41-L51) — Starlette makes the last-added the
outermost.

```
request
  → CORSMiddleware          allow_origins from CORS_ORIGINS
  → AuthMiddleware          verify Clerk JWT → request.state.user_id
  → GatingMiddleware        plan quota check (POST /v1/query* only)
  → SlowAPIMiddleware       60 req/min per IP
  → route handler
```

**AuthMiddleware** ([auth.py:44-62](../apps/agent/app/middleware/auth.py#L44-L62)):

1. If the path is exempt (`_is_exempt`) → sets `user_id = "system"` and passes straight
   through. Exempt: `/health`, `/metrics`, `/v1/stripe/webhook`, `/v1/webhooks/n8n`, and
   `/v1/oauth/*/callback`. Each of those authenticates by its own mechanism — OAuth
   `state`, Stripe signature, HMAC — and never carries a JWT. **`/v1/oauth/*/start` is
   deliberately not exempt**: it binds the flow to an identity.
2. Reads `Authorization: Bearer <token>`.
3. If present → fetches Clerk's JWKS (cached via `@lru_cache`, so **key rotation needs a
   service restart**), matches `kid`, verifies RS256, sets `request.state.user_id` from
   the `sub` claim. Invalid token → **401**.
4. If absent and `APP_ENV=production` → **401**.
5. If absent otherwise → dev fallback: trusts the `X-User-Id` header, else `anonymous`.

> The JWT signature is verified, but **no `audience` or `issuer` claim is checked**
> ([auth.py](../apps/agent/app/middleware/auth.py)) — any token minted by the configured
> Clerk instance is accepted regardless of what it was issued for.

> Every downstream handler reads `request.state.user_id` and **never** a `user_id` query
> param. That was the Phase 9 identity-spoofing fix — a query param would let any caller
> act as any user.

**GatingMiddleware** ([gating.py:12-24](../apps/agent/app/middleware/gating.py#L12-L24))
only acts on `POST /v1/query` and `POST /v1/query/stream`. It calls `check_and_increment`,
which resets the daily counter on a date change, allows Pro unconditionally, and returns
**402** for Free once `queries_today >= 3`.

> ⚠️ **The counter increments before the pipeline runs.** A query that fails at the
> connector, the LLM, or a timeout still consumes one of the three free daily queries.

---

## 1. Sign up / sign in

```
Browser → /sign-up → Clerk hosted component → Clerk session cookie → redirect to /chat
```

1. [proxy.ts:3-9](../apps/web/src/proxy.ts#L3-L9) (Next 16's renamed `middleware.ts`)
   protects every route except `/`, `/sign-in(.*)`, `/sign-up(.*)`,
   `/api/billing/checkout(.*)`, and `/api/agent/v1/oauth/(.*)`.
2. `<SignUp>` / `<SignIn>` render Clerk's hosted flow. Credentials never touch this app.
3. Clerk sets its session cookie; `auth.protect()` lets the request through.
4. **No user row is created in Postgres at this point.** `user_plans` is created lazily on
   first use by `get_or_create_plan`; `user_connector_credentials` on first OAuth connect.

**Where identity comes from afterwards:** the browser never holds an agent token. Every
agent call goes through the BFF, which mints the JWT server-side —
[route.ts:19-31](../apps/web/src/app/api/agent/[...path]/route.ts#L19-L31).

---

## 2. Connect a data source (OAuth)

Google Sheets, Gmail, and Notion each have their own start/callback pair. Sheets shown;
the others are identical in shape.

```
 Browser          Next BFF          Agent            Postgres        Google
    │                 │               │                  │              │
 1  ├── GET /start ──▶│               │                  │              │
 2  │                 ├─ + Bearer ───▶│                  │              │
 3  │                 │               ├─ user_id from verified JWT      │
 4  │                 │               ├─ _pending[state] = {user, flow} │   in-memory!
 5  │◀──────────── 302 accounts.google.com ─────────────────────────────┤
 6  ├── consent screen ─────────────────────────────────────────────────▶│
 7  │◀── 302 /callback?code&state ──────────────────────────────────────┤
 8  ├── callback (NO Authorization header) ────────────▶│              │   401 in prod
 9  │                 │               ├─ _pending.pop(state)  400 if unknown
10  │                 │               ├── exchange code for tokens ────▶│
11  │                 │               ├─ upsert ────────▶│              │   Fernet-encrypted
12  │◀───────── 302 /connect?connected=google_sheets ────┤              │
```

**Step detail:**

1. `/start` binds the flow to the **verified** `user_id`, never a query param
   ([oauth.py:52-65](../apps/agent/app/api/oauth.py#L52-L65)).
2. `state` is a 16-byte `secrets.token_urlsafe`, stored with the `Flow` object so the PKCE
   verifier survives the round trip.
3. The callback pops `state`; an unknown or expired value → **400 "Invalid or expired
   OAuth state"**.
4. Tokens are written via `upsert_credentials` → `set_credentials`, which Fernet-encrypts
   the JSON blob ([models.py:35-38](../apps/agent/app/db/models.py#L35-L38)). The key is
   derived from `CREDENTIAL_ENCRYPTION_KEY`, truncated/padded to 32 bytes.

**Scopes:** Sheets requests `spreadsheets.readonly` + `drive.readonly`; Gmail requests
`gmail.readonly` — a **restricted** scope, which caps a Testing-mode project at 100 users.

> ⚠️ `_pending` is a module-level dict ([oauth.py:31](../apps/agent/app/api/oauth.py#L31)).
> It does not survive a restart and breaks with more than one worker: the callback can land
> on a process that never saw the `state`. Needs Redis before any multi-instance deploy.

**Token refresh** happens lazily at read time, not on a schedule
([google_auth.py:15-33](../apps/agent/app/connectors/google_auth.py#L15-L33)): if
`creds.expired` and a refresh token exists, it refreshes and writes the new access token
back to Postgres.

---

## 3. Ask a question (streaming) — the main flow

**This is the path the UI actually uses.** Everything below happens inside one SSE
connection.

```
 Browser        BFF          Agent        mcp-server    PG/Redis     Claude/Google
    │            │             │               │            │             │
 1  ├─ POST ────▶│             │               │            │             │
 2  │            ├─ +Bearer ──▶│               │            │             │
 3  │            │             ├─ Auth → Gating (quota++) → SlowAPI       │
 4  │            │             ├─ last 10 msgs ────────────▶│             │
 5  │◀════ stage {planning} ═══┤               │            │             │
 6  │            │             ├── planner chat() ────────────────────────▶│  LLM 1
 7  │            │             │◀─ {steps, connectors, question_type} ─────┤
 8  │◀════ stage {retrieving} ═┤               │            │             │
 9  │            │             ├─ list_resources ─▶│  X-Service-Secret    │
10  │            │             │               ├─ decrypt token ─▶│       │
11  │            │             │               ├── Drive API ─────────────▶│
12  │            │             ├─ _select_resources → at most 3           │
    │            │             │               │            │             │
    │       ┌─── LOOP per selected resource ───────────────────────────┐  │
13  │       │    │             ├─ cache_get ───────────────▶│         │  │
14  │       │    │             ├┄ on miss: _read ─▶│ ── Sheets API ───┼──▶│
15  │       │    │             ├┄ cache_set (TTL 300s) ────▶│         │  │
16  │       │    │             ├─ _trim → sets omitted_items if cut   │  │
    │       └─────────────────────────────────────────────────────────┘  │
    │            │             │               │            │             │
17  │◀════ warning {connector} ┤   per failed connector      │             │
18  │◀════ stage {analyzing} ══┤               │            │             │
19  │            │             ├── analyst chat() ─────────────────────────▶│  LLM 2 ← the answer
20  │◀════ stage {summarizing} ┤               │            │             │
21  │            │             ├── summarizer stream() ────────────────────▶│  LLM 3
    │       ┌─── LOOP per token ──────────────────────────────────────┐  │
22  │◀════ chunk {content} ════┤◀──────────────────────────────────────┼──┤
    │       └─────────────────────────────────────────────────────────┘  │
23  │            │             ├── title chat() ───────────────────────────▶│  LLM 4 (first turn)
24  │            │             ├─ persist Q&A ─────────────▶│             │
25  │◀════ done {conversation_id} ─┤            │            │             │

  ──▶ call      ◀── return      ═══▶ SSE to browser      ┄┄▶ conditional
```

### Step 1 — Browser to agent

[useAgentStream.ts:39-42](../apps/web/src/lib/useAgentStream.ts#L39-L42) POSTs
`{message, conversation_id}` to `/api/agent/v1/query/stream`. The BFF forwards it with the
Clerk JWT and forces `Content-Type: text/event-stream`, `X-Accel-Buffering: no` on the way
back so nothing buffers the stream.

### Step 2 — Conversation setup

[query.py:120-132](../apps/agent/app/api/query.py#L120-L132):

- New conversation → `uuid4()`. Existing → `_assert_conversation_owned` runs **first** and
  returns **404** unless the id belongs to the verified user; only then are the last **10**
  messages loaded as context. `_load_history` re-checks ownership and returns `[]` on a
  miss, so a foreign thread's messages are never even fetched.
- The check lives in the route, not in `_stream_pipeline` — raising inside an
  `EventSourceResponse` generator breaks the stream instead of returning a clean 404.
- `_ensure_conversation` creates the row with a placeholder title (the question, truncated
  to 60 chars).

### Step 3 — `planner_node` (Claude call #1)

Sends the question plus history to Claude and expects JSON back:

```json
{"steps": [...], "connectors": ["google_sheets"],
 "question_type": "aggregation", "action": null, "action_cron": null}
```

Flattened into the plan list as `connectors:google_sheets`, `question_type:aggregation`.
If `action` is set, `action_required` is flagged for step 7.

**Claude has seen no data yet** — connector choice is inferred from the question wording.

### Step 4 — `retriever_node` (the data step)

[retriever.py:111-165](../apps/agent/app/graph/nodes/retriever.py#L111-L165):

1. `_parse_plan_meta` extracts connectors **and** `question_type`. Unknown connector names
   are dropped; an empty list falls back to `mock`.
2. `<connector>_list_resources` over MCP → the agent opens a streamable-http session to
   `mcp-server`, authenticated with `X-Service-Secret`.
3. **`_select_resources` picks at most 3** by title-keyword match against the question. ≤3
   available → all are read. No title matches → first 3, and `resources_narrowed` is logged.
4. Per resource: Redis first (`connector:<name>:<user_id>:<resource_id>`, 5-min TTL), else
   `<connector>_read` → the connector decrypts the user's token and calls the SaaS API.
5. **`_trim` caps the payload** — reaching *into* the dict (`rows` for Sheets, `messages`
   for Gmail), because every connector returns a dict wrapper.
   - `question_type ∈ {aggregation, trend, comparison}` → keep **document order**, head-truncate.
     Relevance-reordering rows would corrupt a sum.
   - otherwise → keep the 60 highest keyword-scoring rows.
   - Anything dropped sets `omitted_items` on the entry.

**Failure:** any exception per connector appends
`{"source", "error", "connector_error": True}` and the loop continues — one dead connector
does not kill the query. The SSE layer emits `event: warning` for each
([query.py:141-144](../apps/agent/app/api/query.py#L141-L144)).

### Step 5 — `analyst_node` (Claude call #2)

Strips resource metadata, JSON-dumps `{source, data, error, omitted_items}` per entry into
one prompt, and asks for `{insights, metrics, trends, anomalies}`. `omitted_items` is
carried so a total over a trimmed dataset is reported as partial rather than exact.

**This is where the answer is actually computed.** There is no SQL or pandas — Claude reads
the JSON and does the arithmetic in-context.

Unparseable JSON → `{"insights": ["Analysis unavailable"], ...}`; the pipeline continues.

### Step 6 — Summarizer (Claude call #3, streamed)

The SSE path streams tokens itself rather than calling `summarizer_node`, but both build
the prompt with the shared
[`build_prompt()`](../apps/agent/app/graph/nodes/summarizer.py#L24-L52) so they cannot drift.

**The prompt contains only the analyst's JSON plus the list of source names — not the
rows.** The summarizer therefore cannot verify a number or cite a cell.

Each delta is emitted as `event: chunk {"content": "..."}`. Stream failure falls back to a
plain rendering of the insights.

### Step 7 — Persistence and action

1. `_maybe_update_title` — on the **first** exchange only, a fourth Claude call generates a
   4–7 word title (`count_messages == 0` is checked *before* the messages are written).
2. `_persist_messages` writes the user and assistant messages and touches `updated_at`.
3. If `action_required` → `action_node` runs (see §8) and emits `event: schedule`.
4. `event: done {"conversation_id"}` closes the stream.

### SSE event reference

| Event | Payload | When |
|---|---|---|
| `stage` | `{stage, message}` | Entering planning / retrieving / analyzing / summarizing |
| `warning` | `{connector, message}` | A connector failed, or n8n scheduling failed |
| `chunk` | `{content}` | Each token of the answer |
| `schedule` | `{status, workflow, cron}` | A workflow was activated |
| `done` | `{conversation_id}` | End of stream |

### Cost and latency profile

Four Claude calls per question, all on `LLM_MODEL` (default `claude-opus-4-7`, $5/$25 per
MTok) — including the ~20-token title call. Time to first token spans the planner call plus
all connector round-trips plus the analyst call; nothing streams until step 6.

---

## 4. Ask a question (non-streaming)

`POST /v1/query` ([query.py:88-112](../apps/agent/app/api/query.py#L88-L112)) is the only
caller of the **compiled LangGraph**:

```
START → planner → retriever → analyst → summarizer ─┬→ END
                                                     └→ action → END  (action_required)
```

Same nodes, same order, one JSON response instead of a stream. Note that the conditional
edge in [builder.py:12-13](../apps/agent/app/graph/builder.py#L12-L13) governs **only** this
path — the SSE path calls the node functions directly and handles the action branch inline.
The frontend never calls this endpoint; n8n does (§9).

---

## 5. Conversation history

All three routes scope every query by the verified `user_id`, so one user cannot read
another's threads ([conversations.py](../apps/agent/app/api/conversations.py)).

| Route | Flow | Miss |
|---|---|---|
| `GET /v1/conversations` | `list_conversations(user_id)` → id, title, timestamps | `[]` |
| `GET /v1/conversations/{id}/messages` | `get_conversation(user_id, id)` **then** `get_messages` | **404** |
| `DELETE /v1/conversations/{id}` | `delete_conversation(user_id, id)` | **404** |

The sidebar in [chat/page.tsx](../apps/web/src/app/chat/page.tsx) refreshes the list
whenever a stream completes, so an auto-generated title appears after the first answer.

---

## 6. Connector status / disconnect

`GET /v1/connectors/status` reads `user_connector_credentials` for the verified user and
returns `{connector, connected, last_updated}` for every entry in `REGISTRY` except `mock`.
Only the row's **existence** is checked — a revoked-upstream token still reports
`connected: true` until a query actually fails.

`DELETE /v1/connectors/{name}` deletes the row. The Fernet blob goes with it; Redis entries
expire on their own TTL.

---

## 7. Upgrade to Pro (Stripe)

```
/settings → POST /api/billing/checkout → Stripe Checkout → Stripe webhook → user_plans.plan = "pro"
```

1. [checkout/route.ts](../apps/web/src/app/api/billing/checkout/route.ts) creates a
   subscription Checkout session **in Next.js, not the agent**, and attaches
   `subscription_data.metadata.user_id` — the only link back to the Clerk user.
2. The user pays on Stripe's domain and returns to `/settings?upgraded=true`.
3. Stripe POSTs `/v1/stripe/webhook`. The signature is verified with
   `STRIPE_WEBHOOK_SECRET`; a bad signature → 400.
4. `customer.subscription.created|updated` → `set_plan(plan="pro" if status == "active")`.
   `deleted` → back to `free`. **A subscription without `user_id` metadata is logged and
   ignored** — the plan silently stays Free.
5. `GET /v1/plan/status` backs the `/settings` page.

---

## 8. Schedule a report (agent → n8n)

Triggered when the planner sets `action` on a question like *"email me this every Monday"*.

1. `action_node` maps `action_type` → workflow name (`schedule_report` → `scheduled_report`,
   `data_alert` → `data_change_alert`).
2. `GET /api/v1/workflows` on n8n (header `X-N8N-API-KEY`), matched **by name**. Not found →
   `{"status": "error", "reason": "... run import.sh first"}`.
3. The workflow is patched with the cron/question and set `active: true`.
4. The SSE layer emits `event: schedule` on success, `event: warning` on failure.

No API key configured → `{"status": "skipped"}`; the answer is still returned.

---

## 9. A scheduled report fires (n8n → agent)

1. n8n's cron trigger fires and POSTs to `/v1/webhooks/n8n`.
2. The body is HMAC-SHA256 verified against `WEBHOOK_SECRET`, compared with
   `hmac.compare_digest`, expecting `x-hub-signature-256: sha256=<hex>`. Mismatch → **401**.
3. `question` is required (**400** if absent); `user_id` defaults to `anonymous`, and
   `conversation_id` to a fresh UUID.
4. **`graph.ainvoke()`** runs the full pipeline — the non-streaming path, so the answer is
   returned in one response for n8n to email.

> This route reaches the pipeline **without passing GatingMiddleware's quota check**
> (it gates only `/v1/query*`), so scheduled runs do not consume the daily allowance.
> Whether that is intended is worth deciding explicitly.

---

## 10. Health & metrics

| Route | Serves |
|---|---|
| `GET /health` | `{"status": "ok"}` liveness probe |
| `GET /metrics` | Prometheus, via `prometheus-fastapi-instrumentator`, excluded from OpenAPI |

Structured logs come from `structlog` — JSON in production, coloured console in dev — with
`node`, `conversation_id`, `user_id`, and `duration_ms` bound per pipeline node.

---

## 11. Known gaps

### Fixed in `49a5cd9`

`AuthMiddleware` had no path exemptions, so every caller that authenticates by its own
mechanism returned 401 under `APP_ENV=production`: `/health`, `/metrics`,
`/v1/oauth/*/callback`, `/v1/stripe/webhook`, `/v1/webhooks/n8n`. Now covered by
`_is_exempt` (§0) and pinned by `tests/unit/test_auth.py`, which asserts both halves —
exempt routes reach their handler, everything else still 401s.

Two things had kept it hidden, and both are worth remembering as a testing lesson: the dev
branch falls through to `anonymous`, so nothing failed locally; and the n8n route returns
**401 in both environments** — for HMAC in dev, for auth in production — so an integration
test asserting `401` on an unsigned request passed either way. The test now asserts on the
**response body** (`"Invalid webhook signature"` vs `"Unauthorized"`), which is the only
thing that distinguishes the two.

Also fixed in the same commit: `_load_history` loaded any conversation by id with no
ownership check (§3), and the Stripe webhook caught `stripe.errors.SignatureVerificationError`
— a name that does not exist in the pinned release, so a forged signature returned **500**
rather than 400.

### Still open

| Gap | Where | Impact |
|---|---|---|
| `action_node` never writes the cron or question into the workflow, but returns `{"status": "scheduled"}` | [action.py](../apps/agent/app/graph/nodes/action.py) | "Email me this every Monday" reports success and schedules nothing. The n8n endpoints it calls (`PATCH /workflows/{id}`, `POST /workflows/{id}/run`) also need verifying against the public API |
| OAuth `state` lives in a module-level dict | [oauth.py](../apps/agent/app/api/oauth.py) | Lost on restart; breaks with >1 worker. Blocks any multi-instance deploy |
| No `audience` / `issuer` claim check on the JWT | [auth.py](../apps/agent/app/middleware/auth.py) | Any token from the configured Clerk instance is accepted |
| Notion's `content` is never trimmed — `_PAYLOAD_CAPS` covers `rows` and `messages` only | [retriever.py](../apps/agent/app/graph/nodes/retriever.py) | One long page enters the analyst prompt whole |
| `cache_invalidate` is never called | [cache.py](../apps/agent/app/cache.py) | Disconnecting a connector leaves data readable until the 5-min TTL expires. Also uses `KEYS`, which blocks Redis |
| `_jwks()` uses sync `httpx.get` inside async middleware | [auth.py](../apps/agent/app/middleware/auth.py) | Blocks the event loop on the first request after boot |
| Quota increments before the pipeline runs | [gating.py](../apps/agent/app/middleware/gating.py) | A query that fails at the connector or LLM still burns one of three free daily queries |
| `/v1/webhooks/n8n` bypasses `GatingMiddleware` entirely | [gating.py](../apps/agent/app/middleware/gating.py) | Scheduled runs do not consume quota. Whether that is intended is still undecided |
| Retrieval is lexical title matching, not RAG | [retriever.py](../apps/agent/app/graph/nodes/retriever.py) | See [PLAN.md](PLAN.md) Phase 10. The summarizer sees only the analyst's JSON, so it cannot cite |

# Agent Service

FastAPI + LangGraph. The brain of the BI agent.

## Run

```bash
uv sync
cp .env.example .env    # add ANTHROPIC_API_KEY
uv run uvicorn app.main:app --reload
```

Smoke test:
```bash
curl -X POST http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
```

## Layout

```
app/
  main.py              FastAPI entry
  api/                 HTTP routes (health, query)
  graph/
    state.py           AgentState TypedDict — the shared blob
    builder.py         wires nodes into a compiled graph
    nodes/             one file per node (planner, retriever, analyst, summarizer)
  connectors/          Connector protocol + provider adapters (back the MCP tools)
  mcp_server/          FastMCP server exposing each connector as MCP tools
  mcp_client.py        agent-side MCP client the retriever uses to call tools
  tools/               tool definitions exposed to the LLM
  llm/                 LLM provider abstraction
  db/                  SQLAlchemy models + repositories
  schemas/             Pydantic request/response shapes
  config/              settings loader
  observability/       tracing, logging, metrics
tests/
  unit/                fast, isolated
  integration/         against real-ish deps (testcontainers, etc.)
  evals/               golden-question regressions
```

## Dev commands

```bash
uv run pytest                      # run tests
uv run ruff check .                # lint
uv run ruff format .               # format
uv run mypy app                    # type-check
```

## Adding a node

1. Create `app/graph/nodes/your_node.py` with an async function that takes `AgentState` and returns a dict.
2. Register it in `app/graph/builder.py` with `g.add_node(...)` and wire edges.
3. Add a unit test in `tests/unit/`.

## Connectors over MCP

Connectors are exposed to the agent over the **Model Context Protocol (MCP)**, not
called directly. A FastMCP server (`app/mcp_server/server.py`) wraps each connector
and publishes three tools per source — `<connector>_list_resources`,
`<connector>_read`, `<connector>_search`. The retriever node is an MCP client
(`app/mcp_client.py`) that connects over streamable-http and invokes those tools.

```
retriever node ──MCP client──▶ FastMCP server ──▶ connector classes ──▶ OAuth tokens (DB)
```

OAuth is unchanged: it remains the **authorization** layer that supplies per-user
tokens. MCP is the **transport + tool-discovery** layer on top. The connector
classes still resolve each user's credentials from the DB, so only `user_id`
crosses the MCP boundary — never a raw token.

Run the MCP server alongside the agent:

```bash
make mcp-server          # serves on MCP_SERVER_PORT (default 8001)
```

### Adding a connector

1. Create `app/connectors/yourprovider.py` with a class conforming to the `Connector` protocol.
2. Register it in `app/connectors/__init__.py` `REGISTRY` — the MCP server auto-publishes its tools and `CONNECTOR_NAMES` picks it up.
3. Add the connector name to the planner's available-connectors list.
4. In tests, patch `app.graph.nodes.retriever.mcp_client.*` to avoid needing a running server.

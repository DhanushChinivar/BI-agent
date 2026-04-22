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
  connectors/          Connector protocol + provider adapters (Phase 2)
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

## Adding a connector

1. Create `app/connectors/yourprovider.py` with a class that conforms to `Connector` protocol.
2. Register it in a future `connectors/registry.py` so the retriever can discover it.
3. Mock it in tests by passing any object that implements the protocol.

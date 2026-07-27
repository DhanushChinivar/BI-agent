COMPOSE       = docker compose -f infra/docker/docker-compose.yml
COMPOSE_INFRA = docker compose -f infra/docker/docker-compose.infra.yml

.PHONY: dev dev-web dev-agent mcp-server infra infra-down down build migrate import-workflows logs ps

# ── Local development (hot reload) ───────────────────────────────────────────

infra: ## Start postgres, redis, n8n in Docker (needed for local dev)
	$(COMPOSE_INFRA) up -d
	@echo ""
	@echo "Infrastructure running:"
	@echo "  Postgres → localhost:5432"
	@echo "  Redis    → localhost:6379"
	@echo "  n8n      → http://localhost:5678  (admin / admin)"
	@echo ""
	@echo "Now run in separate terminals:"
	@echo "  make mcp-server"
	@echo "  make dev-agent"
	@echo "  make dev-web"

dev-agent: ## Run FastAPI agent locally with hot reload
	cd apps/agent && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web: ## Run Next.js locally with hot reload
	cd apps/web && npm run dev

mcp-server: ## Run the FastMCP connector server (agent connects to it as an MCP client)
	cd apps/agent && uv run python -m app.mcp_server.server

infra-down: ## Stop infrastructure containers
	$(COMPOSE_INFRA) down

# ── Docker (production-like) ──────────────────────────────────────────────────

dev: ## Build images and start full Docker stack
	$(COMPOSE) up --build -d
	@echo ""
	@echo "Stack is running:"
	@echo "  Web   → http://localhost:3000"
	@echo "  Agent → http://localhost:8000/docs"
	@echo "  n8n   → http://localhost:5678  (admin / admin)"

down: ## Stop and remove all containers
	$(COMPOSE) down

build: ## Rebuild Docker images without starting
	$(COMPOSE) build

migrate: ## Run Alembic migrations inside the agent container
	# Invoke via `python -m` — the venv is built at /build/.venv and copied to
	# /app/.venv, so console scripts keep a stale absolute shebang.
	$(COMPOSE) exec agent /app/.venv/bin/python -m alembic upgrade head

import-workflows: ## Import n8n workflow definitions (requires N8N_API_KEY env var)
	./apps/agent/scripts/import_workflows.sh http://localhost:5678 $(N8N_API_KEY)

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

ps: ## Show running containers
	$(COMPOSE) ps

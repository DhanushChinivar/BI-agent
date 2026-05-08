COMPOSE       = docker compose -f infra/docker/docker-compose.yml
COMPOSE_INFRA = docker compose -f infra/docker/docker-compose.infra.yml

.PHONY: dev dev-web dev-agent infra infra-down down build migrate logs ps

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
	@echo "  make dev-agent"
	@echo "  make dev-web"

dev-agent: ## Run FastAPI agent locally with hot reload
	cd apps/agent && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web: ## Run Next.js locally with hot reload
	cd apps/web && npm run dev

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
	$(COMPOSE) exec agent alembic upgrade head

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

ps: ## Show running containers
	$(COMPOSE) ps

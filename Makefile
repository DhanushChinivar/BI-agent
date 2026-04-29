COMPOSE = docker compose -f infra/docker/docker-compose.yml

.PHONY: dev down build migrate logs ps

dev: ## Build images and start all services
	$(COMPOSE) up --build -d
	@echo ""
	@echo "Stack is running:"
	@echo "  Web   → http://localhost:3000"
	@echo "  Agent → http://localhost:8000/docs"
	@echo "  n8n   → http://localhost:5678  (admin / admin)"

down: ## Stop and remove containers
	$(COMPOSE) down

build: ## Rebuild images without starting
	$(COMPOSE) build

migrate: ## Run Alembic migrations inside the agent container
	$(COMPOSE) exec agent alembic upgrade head

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

ps: ## Show running containers
	$(COMPOSE) ps

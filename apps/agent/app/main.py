"""FastAPI application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import billing, connectors, health, oauth, query, stripe_webhooks
from app.config.settings import get_settings
from app.middleware.auth import AuthMiddleware
from app.middleware.gating import GatingMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: warm up resources (DB pool, LLM client) here later
    yield
    # Shutdown: close pools, flush logs


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="BI Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(GatingMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(query.router)
    app.include_router(oauth.router)
    app.include_router(connectors.router)
    app.include_router(stripe_webhooks.router)
    app.include_router(billing.router)
    return app


app = create_app()

"""FastAPI application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api import billing, connectors, health, n8n_webhooks, oauth, query, stripe_webhooks, upload, workflows
from app.config.settings import get_settings
from app.middleware.auth import AuthMiddleware
from app.middleware.gating import GatingMiddleware
from app.middleware.rate_limit import limiter
from app.observability import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="BI Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=429),
    )
    app.add_middleware(SlowAPIMiddleware)

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
    app.include_router(upload.router)
    app.include_router(n8n_webhooks.router)
    app.include_router(workflows.router)

    # Prometheus metrics at /metrics
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    return app


app = create_app()

"""Application configuration loaded from environment variables."""
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Committed defaults that are safe for local dev but must never reach production.
_INSECURE_CREDENTIAL_KEY = "bi-agent-default-dev-key-000000"
_INSECURE_WEBHOOK_SECRET = "change-me-webhook-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    anthropic_api_key: str = Field(default="")
    llm_model: str = Field(default="claude-opus-4-7")

    # Database
    database_url: str = Field(default="postgresql+asyncpg://localhost/biagent")

    # Server
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    cors_origins: str = Field(default="http://localhost:3000")

    # Phase 2: connector credentials
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_sheets_redirect_uri: str = Field(default="http://localhost:8000/v1/oauth/google-sheets/callback")
    google_gmail_redirect_uri: str = Field(default="http://localhost:8000/v1/oauth/gmail/callback")
    notion_client_id: str = Field(default="")
    notion_client_secret: str = Field(default="")
    notion_redirect_uri: str = Field(default="http://localhost:8000/v1/oauth/notion/callback")
    credential_encryption_key: str = Field(default=_INSECURE_CREDENTIAL_KEY)
    frontend_url: str = Field(default="http://localhost:3000")
    redis_url: str = Field(default="redis://localhost:6379")

    # Phase 5: auth + billing
    clerk_frontend_api: str = Field(default="")   # e.g. clerk.your-domain.com
    stripe_secret_key: str = Field(default="")
    stripe_webhook_secret: str = Field(default="")
    stripe_pro_price_id: str = Field(default="")

    # Observability
    langsmith_api_key: str = Field(default="")
    langsmith_project: str = Field(default="bi-agent-dev")

    # n8n automation
    n8n_base_url: str = Field(default="http://localhost:5678")
    n8n_api_key: str = Field(default="")
    webhook_secret: str = Field(default=_INSECURE_WEBHOOK_SECRET)

    # MCP: connectors are exposed as tools by a FastMCP server that the
    # agent consumes as an MCP client. OAuth still provides the per-user tokens.
    mcp_server_url: str = Field(default="http://localhost:8001/mcp")
    mcp_server_host: str = Field(default="0.0.0.0")
    mcp_server_port: int = Field(default=8001)
    # Shared secret the agent (MCP client) sends and the MCP server verifies.
    # The MCP tools trust their user_id argument, so the server must only be
    # reachable by the agent. Empty = unauthenticated (dev only).
    mcp_service_secret: str = Field(default="")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _require_secure_secrets_in_production(self) -> "Settings":
        """Fail fast rather than silently ship insecure defaults to production.

        A deploy that forgets to set these would otherwise encrypt every user's
        OAuth refresh token with a public key, accept forged webhooks, or expose
        an unauthenticated connector server.
        """
        if self.app_env != "production":
            return self

        problems: list[str] = []
        if self.credential_encryption_key in ("", _INSECURE_CREDENTIAL_KEY):
            problems.append("CREDENTIAL_ENCRYPTION_KEY")
        if self.webhook_secret in ("", _INSECURE_WEBHOOK_SECRET):
            problems.append("WEBHOOK_SECRET")
        if not self.mcp_service_secret:
            problems.append("MCP_SERVICE_SECRET")

        if problems:
            raise ValueError(
                "Insecure or missing secrets for APP_ENV=production: "
                + ", ".join(problems)
                + ". Set each to a strong, unique value."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

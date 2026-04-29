"""Application configuration loaded from environment variables."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    notion_api_key: str = Field(default="")
    notion_oauth_client_id: str = Field(default="")
    notion_oauth_client_secret: str = Field(default="")
    notion_redirect_uri: str = Field(default="http://localhost:8000/v1/oauth/notion/callback")
    credential_encryption_key: str = Field(default="bi-agent-default-dev-key-000000")
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
    webhook_secret: str = Field(default="change-me-webhook-secret")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

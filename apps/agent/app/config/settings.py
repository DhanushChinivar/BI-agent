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
    google_redirect_uri: str = Field(default="http://localhost:8000/v1/oauth/google/callback")
    notion_api_key: str = Field(default="")
    credential_encryption_key: str = Field(default="bi-agent-default-dev-key-000000")

    # Observability
    langsmith_api_key: str = Field(default="")
    langsmith_project: str = Field(default="bi-agent-dev")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

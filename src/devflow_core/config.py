"""Application settings loaded from the environment and local dotenv file."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from devflow_core.mcp_config import parse_mcp_config


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    APP_NAME: str = "DevFlow AI"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Literal["development", "local", "test", "staging", "production"] = "development"

    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    UI_HOST: str = "127.0.0.1"
    UI_PORT: int = 8501
    ALLOWED_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:8501"])
    DEVFLOW_API_BASE_URL: str = "http://localhost:8000"

    DATABASE_URL: str = "sqlite+aiosqlite:///./devflow.db"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["text", "json"] = "text"

    CHATGPT_API_KEY: SecretStr | None = None
    CODEX_API_KEY: SecretStr | None = None
    ANTHROPIC_API_KEY: SecretStr | None = None
    GITHUB_TOKEN: SecretStr | None = None
    FIGMA_TOKEN: SecretStr | None = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_TIMEOUT_SECONDS: int = Field(default=30, gt=0)
    GITHUB_API_BASE_URL: str = "https://api.github.com"
    LOCAL_OPERATOR_ID: str = Field(default="local_operator", min_length=1)
    APPROVAL_TTL_SECONDS: int = Field(default=3600, gt=0)
    APPROVAL_AUTHORIZATION_VALIDITY_SECONDS: int = Field(default=3600, gt=0)
    RUN_CONCURRENCY: int = Field(default=1, ge=1, le=1)
    STEP_LEASE_TTL_SECONDS: int = Field(default=60, gt=0)
    STEP_HEARTBEAT_INTERVAL_SECONDS: int = Field(default=15, gt=0)
    APPROVED_WORKING_DIRECTORIES: list[str] = Field(default_factory=list)
    EXECUTOR_ENV_ALLOWLIST: list[str] = Field(default_factory=list)
    GITHUB_CONFIRMATION_TTL_SECONDS: int = Field(default=600, gt=0)
    FIGMA_API_BASE_URL: str = "https://api.figma.com"
    MCP_SERVERS_CONFIG: str = ""

    @field_validator("MCP_SERVERS_CONFIG")
    @classmethod
    def validate_mcp_config(cls, value: str) -> str:
        try:
            parse_mcp_config(value)
        except ValueError:
            raise ValueError("Invalid MCP server configuration") from None
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

"""Runtime settings loaded at the application composition boundary."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_KP_", env_file=".env", extra="ignore")

    db_path: Path = Field(default=Path("data/ai_kp.sqlite3"))
    llm_base_url: str = Field(default="http://localhost:11434/v1")
    llm_api_key: str = Field(default="local")
    llm_model: str = Field(default="qwen3:8b")
    rulebook_index_root: Path = Field(default=Path("data/rag/rulesets"))
    rulebook_embedding_dimensions: int = Field(default=384, ge=64, le=2048)
    map_asset_root: Path = Field(default=Path("data/map-assets"))
    module_asset_root: Path = Field(default=Path("data/module-assets"))
    backup_root: Path = Field(default=Path("data/backups"))
    backup_max_files: int = Field(default=20_000, ge=1, le=100_000)
    backup_max_uncompressed_bytes: int = Field(
        default=20 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
    )
    image_base_url: str | None = None
    image_api_key: str = Field(default="")
    image_model: str | None = None
    image_timeout_seconds: float = Field(default=300, ge=10, le=1800)
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173"
    )
    local_admin_enabled: bool = True
    admin_token: str | None = None
    deployment_mode: str = Field(default="local", pattern="^(local|lan)$")
    trusted_hosts: str = Field(default="localhost,127.0.0.1,testserver")
    sensitive_rate_limit_requests: int = Field(default=20, ge=1, le=1000)
    sensitive_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    @model_validator(mode="after")
    def validate_lan_security(self) -> "Settings":
        if self.deployment_mode == "lan" and not self.admin_token:
            raise ValueError("AI_KP_ADMIN_TOKEN is required in LAN deployment mode")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

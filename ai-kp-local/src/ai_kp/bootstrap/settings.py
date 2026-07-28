"""Runtime settings loaded at the application composition boundary."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    image_base_url: str | None = None
    image_api_key: str = Field(default="")
    image_model: str | None = None
    image_timeout_seconds: float = Field(default=300, ge=10, le=1800)
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173"
    )
    local_admin_enabled: bool = True
    admin_token: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

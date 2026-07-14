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
    cors_origins: str = Field(default="http://localhost:5173")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


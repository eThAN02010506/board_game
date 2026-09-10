"""Runtime settings loaded at the application composition boundary."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_KP_", env_file=".env", extra="ignore")

    db_path: Path = Field(default=Path("data/ai_kp.sqlite3"))
    llm_base_url: str = Field(default="http://localhost:11434/v1")
    llm_api_key: str = Field(default="local")
    llm_model: str = Field(default="qwen3:8b")
    director_help_max_concurrency: int = Field(default=1, ge=1, le=32)
    rulebook_index_root: Path = Field(default=Path("data/rag/rulesets"))
    rulebook_embedding_dimensions: int = Field(default=384, ge=64, le=2048)
    map_asset_root: Path = Field(default=Path("data/map-assets"))
    module_asset_root: Path = Field(default=Path("data/module-assets"))
    module_parse_timeout_seconds: float = Field(default=3600, ge=5, le=3600)
    module_parse_memory_limit_mib: int = Field(default=1024, ge=256, le=8192)
    module_parse_cpu_seconds: int = Field(default=3600, ge=1, le=3600)
    module_document_parse_timeout_seconds: float = Field(
        default=10_800,
        ge=60,
        le=21_600,
    )
    module_document_parse_cpu_seconds: int = Field(
        default=10_800,
        ge=60,
        le=21_600,
    )
    module_document_parser: Literal["native_first", "mineru", "builtin"] = "native_first"
    mineru_command: str = Field(default="mineru", min_length=1, max_length=500)
    mineru_backend: Literal["pipeline", "vlm-engine", "hybrid-engine"] = "pipeline"
    mineru_model_source: Literal["modelscope", "huggingface"] = "modelscope"
    legacy_doc_converter_command: str = Field(
        default="soffice",
        min_length=1,
        max_length=500,
    )
    legacy_doc_converter_timeout_seconds: float = Field(default=60, ge=1, le=600)
    tesseract_command: str = Field(default="tesseract", min_length=1, max_length=500)
    tesseract_timeout_seconds: float = Field(default=60, ge=5, le=600)
    backup_root: Path = Field(default=Path("data/backups"))
    backup_max_files: int = Field(default=20_000, ge=1, le=100_000)
    backup_max_uncompressed_bytes: int = Field(
        default=20 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
    )
    sqlite_synchronous: Literal["FULL", "NORMAL"] = "FULL"
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
    json_body_max_bytes: int = Field(
        default=1024 * 1024,
        ge=64 * 1024,
        le=16 * 1024 * 1024,
    )

    @model_validator(mode="after")
    def validate_lan_security(self) -> "Settings":
        if self.deployment_mode == "lan" and not self.admin_token:
            raise ValueError("AI_KP_ADMIN_TOKEN is required in LAN deployment mode")
        if self.deployment_mode == "lan":
            # A reverse proxy commonly reaches the backend through loopback. LAN
            # mode therefore never treats a loopback socket as administrator.
            self.local_admin_enabled = False
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

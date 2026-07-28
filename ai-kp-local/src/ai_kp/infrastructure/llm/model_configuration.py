"""Persisted model configuration validation, discovery, and settings projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ai_kp.bootstrap.settings import Settings


def normalize_openai_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("模型地址必须是有效的 http:// 或 https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("模型地址不能内嵌用户名或密码，请使用 API Key 字段")
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


def validate_local_model_path(value: str) -> dict[str, Any]:
    if not value.strip():
        raise ValueError("本地模型目录不能为空")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("本地模型路径不存在或不是目录")
    missing = [
        filename
        for filename in ("config.json", "tokenizer_config.json")
        if not (path / filename).is_file()
    ]
    weights = sorted(path.glob("*.safetensors"))
    if missing:
        raise ValueError(f"模型目录缺少必要文件：{', '.join(missing)}")
    if not weights:
        raise ValueError("模型目录内没有找到 .safetensors 权重")
    return {
        "resolved_path": str(path),
        "model_name": path.name,
        "weight_files": len(weights),
        "size_bytes": sum(item.stat().st_size for item in weights),
    }


def apply_model_configuration(settings: Settings, configuration: dict | None) -> Settings:
    if not configuration:
        return settings
    if configuration["provider_type"] == "local_mlx":
        base_url = f"http://127.0.0.1:{configuration['local_port']}/v1"
        api_key = "local"
    else:
        base_url = normalize_openai_base_url(str(configuration["base_url"]))
        api_key = str(configuration.get("api_key") or "local")
    return settings.model_copy(
        update={
            "llm_base_url": base_url,
            "llm_api_key": api_key,
            "llm_model": str(configuration["model"]),
        }
    )


def public_model_configuration(
    settings: Settings,
    configuration: dict | None,
) -> dict[str, Any]:
    if configuration is None:
        return {
            "provider_type": "openai_compatible",
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "local_model_path": None,
            "local_port": 8011,
            "api_key_configured": bool(settings.llm_api_key),
            "persisted": False,
        }
    return {
        "provider_type": configuration["provider_type"],
        "base_url": configuration["base_url"],
        "model": configuration["model"],
        "local_model_path": configuration["local_model_path"],
        "local_port": configuration["local_port"],
        "api_key_configured": bool(configuration.get("api_key")),
        "persisted": True,
        "updated_at": configuration["updated_at"],
    }


async def discover_openai_models(
    base_url: str,
    api_key: str,
    *,
    timeout_seconds: float = 10,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    normalized = normalize_openai_base_url(base_url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        if client is None:
            async with httpx.AsyncClient() as temporary_client:
                response = await temporary_client.get(
                    f"{normalized}/models",
                    headers=headers,
                    timeout=timeout_seconds,
                )
        else:
            response = await client.get(
                f"{normalized}/models",
                headers=headers,
                timeout=timeout_seconds,
            )
    except httpx.RequestError as exc:
        raise RuntimeError(f"无法连接模型服务 {normalized}：{exc}") from exc
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text.replace("\n", " ")[:500]
        raise RuntimeError(
            f"模型服务返回 HTTP {response.status_code}：{detail}"
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("模型服务返回了无效 JSON") from exc
    models = payload.get("data", []) if isinstance(payload, dict) else []
    return [
        str(item["id"])
        for item in models
        if isinstance(item, dict) and item.get("id")
    ]

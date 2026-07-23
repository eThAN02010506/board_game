from fastapi import APIRouter, Depends, Request

from ai_kp.api.authz import require_local_admin
from ai_kp.api.dependencies import get_app_settings, get_repo
from ai_kp.api.schemas import ModelConfigurationUpdate
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.llm.local_runtime import LocalModelRuntime
from ai_kp.infrastructure.llm.model_configuration import (
    apply_model_configuration,
    discover_openai_models,
    normalize_openai_base_url,
    public_model_configuration,
    validate_local_model_path,
)


router = APIRouter()


def _runtime(request: Request) -> LocalModelRuntime:
    return request.app.state.local_model_runtime


def _effective_api_key(
    payload: ModelConfigurationUpdate,
    existing: dict | None,
    settings: Settings,
) -> str:
    if payload.api_key is not None:
        return payload.api_key.strip()
    if existing:
        if existing["provider_type"] == "openai_compatible":
            return str(existing.get("api_key") or "")
        return ""
    return settings.llm_api_key


@router.get("/model-settings")
def get_model_settings(
    request: Request,
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
    repo: Repository = Depends(get_repo),
) -> dict:
    result = public_model_configuration(settings, repo.get_model_configuration())
    result["runtime"] = _runtime(request).status()
    return result


@router.put("/model-settings")
def update_model_settings(
    payload: ModelConfigurationUpdate,
    request: Request,
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
    repo: Repository = Depends(get_repo),
) -> dict:
    existing = repo.get_model_configuration()
    if payload.provider_type == "local_mlx":
        model_info = validate_local_model_path(payload.local_model_path or "")
        base_url = None
        api_key = None
        local_model_path = str(model_info["resolved_path"])
        model = payload.model.strip() or str(model_info["model_name"])
    else:
        base_url = normalize_openai_base_url(payload.base_url or "")
        api_key = _effective_api_key(payload, existing, settings)
        local_model_path = None
        model = payload.model.strip()
        if not model:
            raise ValueError("请选择或填写模型 ID")
    saved = repo.save_model_configuration(
        provider_type=payload.provider_type,
        base_url=base_url,
        api_key=api_key,
        model=model,
        local_model_path=local_model_path,
        local_port=payload.local_port,
    )
    request.app.state.settings = apply_model_configuration(settings, saved)
    result = public_model_configuration(request.app.state.settings, saved)
    result["runtime"] = _runtime(request).status()
    return result


@router.post("/model-settings/discover")
async def discover_models(
    payload: ModelConfigurationUpdate,
    request: Request,
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
    repo: Repository = Depends(get_repo),
) -> dict:
    existing = repo.get_model_configuration()
    if payload.provider_type == "local_mlx":
        model_info = validate_local_model_path(payload.local_model_path or "")
        runtime = _runtime(request).status()
        models: list[str] = []
        if runtime["state"] == "running" and runtime["port"] == payload.local_port:
            models = await discover_openai_models(
                f"http://127.0.0.1:{payload.local_port}/v1",
                "local",
                timeout_seconds=2,
            )
        return {"models": models, "model_path": model_info, "runtime": runtime}
    models = await discover_openai_models(
        payload.base_url or "",
        _effective_api_key(payload, existing, settings),
    )
    return {
        "models": models,
        "normalized_base_url": normalize_openai_base_url(payload.base_url or ""),
        "runtime": _runtime(request).status(),
    }


@router.post("/model-runtime/start")
def start_local_model(
    request: Request,
    _admin: None = Depends(require_local_admin),
    repo: Repository = Depends(get_repo),
) -> dict:
    configuration = repo.get_model_configuration()
    if not configuration or configuration["provider_type"] != "local_mlx":
        raise ValueError("请先保存本地 MLX 模型配置")
    return _runtime(request).start(
        str(configuration["local_model_path"]),
        int(configuration["local_port"]),
    )


@router.get("/model-runtime")
def get_local_model_runtime(
    request: Request,
    _admin: None = Depends(require_local_admin),
) -> dict:
    return _runtime(request).status()


@router.post("/model-runtime/stop")
def stop_local_model(
    request: Request,
    _admin: None = Depends(require_local_admin),
) -> dict:
    return _runtime(request).stop()

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ai_kp.api.errors import register_error_handlers
from ai_kp.api.routers import (
    backups,
    campaigns,
    checks,
    debug,
    facts,
    investigators,
    maps,
    models,
    module_graph,
    module_runs,
    modules,
    realtime,
    rulebooks,
    sessions,
    system,
    turns,
    world,
)
from ai_kp.api.routers.debug import DebugTelemetry, DebugTelemetryMiddleware
from ai_kp.api.security import (
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
    SensitiveOperationRateLimitMiddleware,
)
from ai_kp.bootstrap.settings import Settings, get_settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.images.model_configuration import (
    apply_image_model_configuration,
)
from ai_kp.infrastructure.llm.local_runtime import LocalModelRuntime
from ai_kp.infrastructure.llm.model_configuration import apply_model_configuration
from ai_kp.infrastructure.modules.document_sandbox import DocumentParsePolicy
from ai_kp.infrastructure.modules.import_worker import ModuleImportWorker


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Own shared HTTP resources, the import worker, and local model process."""

    app.state.module_import_worker.start()
    try:
        async with httpx.AsyncClient() as http_client:
            app.state.http_client = http_client
            yield
    finally:
        await asyncio.to_thread(app.state.module_import_worker.stop)
        app.state.local_model_runtime.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    initialization_connection = connect(
        resolved_settings.db_path,
        synchronous=resolved_settings.sqlite_synchronous,
    )
    try:
        init_db(initialization_connection)
        repository = Repository(initialization_connection)
        repository.recover_interrupted_rule_extractions()
        repository.recover_interrupted_rule_ingestion_runs()
        repository.recover_interrupted_module_knowledge_extractions()
        persisted_model_configuration = repository.get_model_configuration()
        persisted_image_model_configuration = (
            repository.get_image_model_configuration()
        )
        initialization_connection.commit()
    finally:
        initialization_connection.close()
    resolved_settings = apply_model_configuration(
        resolved_settings, persisted_model_configuration
    )
    resolved_settings = apply_image_model_configuration(
        resolved_settings,
        persisted_image_model_configuration,
    )

    app = FastAPI(title="AI KP Local", version="0.1.0", lifespan=_lifespan)
    app.state.settings = resolved_settings
    app.state.local_model_runtime = LocalModelRuntime(
        resolved_settings.db_path.parent / "model-runtime.log"
    )
    app.state.module_import_worker = ModuleImportWorker(
        resolved_settings.db_path,
        resolved_settings.module_asset_root,
        synchronous=resolved_settings.sqlite_synchronous,
        parse_policy=DocumentParsePolicy(
            timeout_seconds=resolved_settings.module_parse_timeout_seconds,
            memory_limit_mib=resolved_settings.module_parse_memory_limit_mib,
            cpu_seconds=resolved_settings.module_parse_cpu_seconds,
            legacy_doc_converter_command=(
                resolved_settings.legacy_doc_converter_command
            ),
            legacy_doc_converter_timeout_seconds=(
                resolved_settings.legacy_doc_converter_timeout_seconds
            ),
        ),
    )
    app.state.debug_telemetry = DebugTelemetry()
    if resolved_settings.deployment_mode == "lan":
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=resolved_settings.trusted_host_list,
        )
        app.add_middleware(
            SensitiveOperationRateLimitMiddleware,
            requests=resolved_settings.sensitive_rate_limit_requests,
            window_seconds=resolved_settings.sensitive_rate_limit_window_seconds,
        )
    app.add_middleware(DebugTelemetryMiddleware, telemetry=app.state.debug_telemetry)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_json_bytes=resolved_settings.json_body_max_bytes,
    )
    # Add last so security headers wrap CORS, host rejection, rate limits, and route errors.
    app.add_middleware(SecurityHeadersMiddleware)
    register_error_handlers(app)

    domain_routers = (
        debug.router,
        backups.router,
        system.router,
        realtime.router,
        campaigns.router,
        checks.router,
        facts.router,
        investigators.router,
        models.router,
        rulebooks.router,
        sessions.router,
        world.router,
        maps.router,
        modules.router,
        module_graph.router,
        module_runs.router,
        turns.router,
    )
    app.state.domain_routers = domain_routers
    for domain_router in domain_routers:
        app.include_router(domain_router)
    return app


app = create_app()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute, APIRouter, APIWebSocketRoute

from ai_kp.api.errors import register_error_handlers
from ai_kp.api.routers import campaigns, maps, realtime, sessions, system, turns, world
from ai_kp.core.config import Settings, get_settings
from ai_kp.core.db import connect, init_db


def _mount_flat_router(app: FastAPI, router: APIRouter) -> None:
    """Clone a domain router onto the app while retaining a flat public route table."""

    for route in router.routes:
        if isinstance(route, APIRoute):
            app.router.add_api_route(
                route.path,
                route.endpoint,
                response_model=route.response_model,
                status_code=route.status_code,
                tags=route.tags,
                dependencies=route.dependencies,
                summary=route.summary,
                description=route.description,
                response_description=route.response_description,
                responses=route.responses,
                deprecated=route.deprecated,
                methods=route.methods,
                operation_id=route.operation_id,
                response_model_include=route.response_model_include,
                response_model_exclude=route.response_model_exclude,
                response_model_by_alias=route.response_model_by_alias,
                response_model_exclude_unset=route.response_model_exclude_unset,
                response_model_exclude_defaults=route.response_model_exclude_defaults,
                response_model_exclude_none=route.response_model_exclude_none,
                include_in_schema=route.include_in_schema,
                response_class=route.response_class,
                name=route.name,
                route_class_override=type(route),
                callbacks=route.callbacks,
                openapi_extra=route.openapi_extra,
                generate_unique_id_function=route.generate_unique_id_function,
                strict_content_type=route.strict_content_type,
            )
        elif isinstance(route, APIWebSocketRoute):
            app.router.add_api_websocket_route(
                route.path,
                route.endpoint,
                name=route.name,
                dependencies=route.dependencies,
            )
        else:
            raise TypeError(f"Unsupported domain route type: {type(route).__name__}")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    initialization_connection = connect(resolved_settings.db_path)
    try:
        init_db(initialization_connection)
    finally:
        initialization_connection.close()

    app = FastAPI(title="AI KP Local", version="0.1.0")
    app.state.settings = resolved_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)

    # These routers use no mount-time prefix, tags, dependencies, or response
    # overrides. Clone them so introspection stays flat and app-level dependency
    # overrides continue to work exactly as they did in the former monolith.
    for domain_router in (
        system.router,
        realtime.router,
        campaigns.router,
        sessions.router,
        world.router,
        maps.router,
        turns.router,
    ):
        _mount_flat_router(app, domain_router)
    return app


app = create_app()

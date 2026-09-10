from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import time
from collections import Counter, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ai_kp.api.authz import require_local_admin
from ai_kp.api.debug_dashboard import DEBUG_DASHBOARD_HTML
from ai_kp.api.dependencies import get_app_settings, get_repo
from ai_kp.api.uploads import read_limited_body, safe_upload_filename
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.migrations import LATEST_SCHEMA_VERSION
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.llm.local_runtime import LocalModelRuntime
from ai_kp.infrastructure.llm.model_configuration import (
    discover_openai_models,
    public_model_configuration,
)
from ai_kp.infrastructure.realtime.websocket import handle_realtime_websocket
from ai_kp.observability import operational_telemetry
from ai_kp.rulesets.coc7.character.xlsx_import import (
    MAX_XLSX_BYTES,
    import_coc_character_xlsx,
)

router = APIRouter(tags=["debug"])

_SENSITIVE_MARKERS = ("token", "hash", "api_key", "secret", "password")
_LARGE_TEXT_MARKERS = ("_json", "content", "raw_text", "extracted_text")


class DebugTelemetry:
    """Small in-memory request ledger for local diagnostics."""

    def __init__(self, max_requests: int = 250):
        self.started_at = datetime.now(UTC)
        self.started_monotonic = time.monotonic()
        self.total_requests = 0
        self.failed_requests = 0
        self.recent: deque[dict[str, Any]] = deque(maxlen=max_requests)

    def record(
        self,
        *,
        method: str,
        path: str,
        status: int,
        duration_ms: float,
        client: str | None,
    ) -> None:
        self.total_requests += 1
        if status >= 400:
            self.failed_requests += 1
        self.recent.appendleft(
            {
                "at": datetime.now(UTC).isoformat(),
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": round(duration_ms, 2),
                "client": client,
            }
        )

    def summary(self) -> dict[str, Any]:
        statuses = Counter(str(item["status"])[0] + "xx" for item in self.recent)
        durations = [float(item["duration_ms"]) for item in self.recent]
        return {
            "started_at": self.started_at.isoformat(),
            "uptime_seconds": round(time.monotonic() - self.started_monotonic, 2),
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "recent_status_groups": dict(statuses),
            "recent_average_ms": round(sum(durations) / len(durations), 2)
            if durations
            else 0,
            "retained_requests": len(self.recent),
        }


class DebugTelemetryMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, telemetry: DebugTelemetry):
        super().__init__(app)
        self.telemetry = telemetry

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-AI-KP-Process-Time-Ms"] = (
                f"{(time.perf_counter() - started) * 1000:.2f}"
            )
            return response
        finally:
            self.telemetry.record(
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=(time.perf_counter() - started) * 1000,
                client=request.client.host if request.client else None,
            )
            operational_telemetry.record_request(
                (time.perf_counter() - started) * 1000
            )


def _telemetry(request: Request) -> DebugTelemetry:
    return request.app.state.debug_telemetry


def _runtime(request: Request) -> LocalModelRuntime:
    return request.app.state.local_model_runtime


def _table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _database_inventory(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    result = []
    for table in _table_names(connection):
        quoted = _quote_identifier(table)
        count = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
        columns = [
            {
                "name": str(row["name"]),
                "type": str(row["type"]),
                "nullable": not bool(row["notnull"]),
                "primary_key": bool(row["pk"]),
            }
            for row in connection.execute(f"PRAGMA table_info({quoted})").fetchall()
        ]
        result.append({"name": table, "rows": count, "columns": columns})
    return result


def _redact_value(column: str, value: Any) -> Any:
    lowered = column.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "<redacted>" if value not in (None, "") else value
    if isinstance(value, bytes):
        return f"<binary:{len(value)} bytes>"
    if isinstance(value, str) and (
        len(value) > 700 or any(marker in lowered for marker in _LARGE_TEXT_MARKERS)
    ):
        return value[:240] + f" … <{len(value)} chars>"
    return value


def _route_inventory(request: Request) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for path, path_item in request.app.openapi().get("paths", {}).items():
        for method, operation in path_item.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            routes.append(
                {
                    "path": path,
                    "methods": [method.upper()],
                    "name": operation.get("operationId"),
                    "summary": operation.get("summary"),
                    "tags": operation.get("tags", []),
                    "response_model": None,
                }
            )
    for domain_router in request.app.state.domain_routers:
        for route in domain_router.routes:
            if isinstance(route, APIRoute) and not route.include_in_schema:
                routes.append(
                    {
                        "path": route.path,
                        "methods": sorted(
                            method
                            for method in route.methods
                            if method not in {"HEAD", "OPTIONS"}
                        ),
                        "name": route.name,
                        "summary": route.summary,
                        "tags": route.tags,
                        "response_model": getattr(route.response_model, "__name__", None),
                    }
                )
            elif isinstance(route, APIWebSocketRoute):
                routes.append(
                    {
                        "path": route.path,
                        "methods": ["WS"],
                        "name": route.name,
                        "summary": "WebSocket endpoint",
                        "tags": [],
                        "response_model": None,
                    }
                )
    return sorted(routes, key=lambda item: (item["path"], item["methods"]))


def _filesystem_snapshot(settings: Settings) -> dict[str, Any]:
    db_path = settings.db_path.resolve()
    try:
        stat = db_path.stat()
        db_size = stat.st_size
        modified_at = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
    except FileNotFoundError:
        db_size = 0
        modified_at = None
    try:
        disk = shutil.disk_usage(db_path.parent)
        disk_snapshot = {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        }
    except OSError:
        disk_snapshot = None
    return {
        "database_path": str(db_path),
        "database_size_bytes": db_size,
        "database_modified_at": modified_at,
        "rulebook_index_root": str(settings.rulebook_index_root.resolve()),
        "disk": disk_snapshot,
    }


def _tail_file(path: Path, lines: int) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return list(deque((line.rstrip("\n") for line in handle), maxlen=lines))
    except FileNotFoundError:
        return []


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def debug_dashboard(_admin: None = Depends(require_local_admin)) -> HTMLResponse:
    return HTMLResponse(
        DEBUG_DASHBOARD_HTML,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


@router.get("/debug/diagnostics")
def diagnostics(
    request: Request,
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
    repo: Repository = Depends(get_repo),
) -> dict[str, Any]:
    stored_configuration = repo.get_model_configuration()
    database = _database_inventory(repo.connection)
    migration_rows = repo.connection.execute(
        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {
        "service": {
            "name": request.app.title,
            "version": request.app.version,
            "status": "ok",
            "time_utc": datetime.now(UTC).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "pid": os.getpid(),
            "cpu_count": os.cpu_count(),
        },
        "telemetry": _telemetry(request).summary(),
        "operational_telemetry": operational_telemetry.snapshot(),
        "settings": {
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "llm_api_key_configured": bool(settings.llm_api_key),
            "cors_origins": settings.cors_origin_list,
            "local_admin_enabled": settings.local_admin_enabled,
            "admin_token_configured": bool(settings.admin_token),
            "rulebook_embedding_dimensions": settings.rulebook_embedding_dimensions,
        },
        "filesystem": _filesystem_snapshot(settings),
        "database": {
            "sqlite_version": sqlite3.sqlite_version,
            "journal_mode": repo.connection.execute("PRAGMA journal_mode").fetchone()[0],
            "foreign_keys": bool(repo.connection.execute("PRAGMA foreign_keys").fetchone()[0]),
            "latest_supported_schema": LATEST_SCHEMA_VERSION,
            "applied_migrations": [dict(row) for row in migration_rows],
            "tables": database,
            "total_rows": sum(int(table["rows"]) for table in database),
        },
        "model": {
            "configuration": public_model_configuration(settings, stored_configuration),
            "runtime": _runtime(request).status(),
        },
        "routes": _route_inventory(request),
    }


@router.get("/debug/runtime-identity")
def runtime_identity(
    request: Request,
    _admin: None = Depends(require_local_admin),
) -> dict[str, Any]:
    """Project the narrow identities needed to prove a real process restart."""

    return {
        "process_instance_id": str(request.app.state.process_instance_id),
        "os_pid": os.getpid(),
        "persistent_store_id": str(request.app.state.persistent_store_id),
        "schema_version": LATEST_SCHEMA_VERSION,
        "status": "ready",
    }


@router.get("/debug/requests")
def recent_requests(
    request: Request,
    limit: int = Query(default=80, ge=1, le=250),
    _admin: None = Depends(require_local_admin),
) -> dict[str, Any]:
    telemetry = _telemetry(request)
    return {
        "summary": telemetry.summary(),
        "requests": list(telemetry.recent)[:limit],
    }


@router.get("/debug/observability")
def observability(
    _admin: None = Depends(require_local_admin),
) -> dict[str, Any]:
    return operational_telemetry.snapshot()


@router.get("/debug/database/tables/{table_name}")
def database_table(
    table_name: str,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    _admin: None = Depends(require_local_admin),
    repo: Repository = Depends(get_repo),
) -> dict[str, Any]:
    available = _table_names(repo.connection)
    if table_name not in available:
        raise KeyError(f"Debug table not found: {table_name}")
    quoted = _quote_identifier(table_name)
    columns = [dict(row) for row in repo.connection.execute(f"PRAGMA table_info({quoted})")]
    rows = repo.connection.execute(
        f"SELECT * FROM {quoted} LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {
        "table": table_name,
        "limit": limit,
        "offset": offset,
        "columns": columns,
        "rows": [
            {key: _redact_value(key, value) for key, value in dict(row).items()}
            for row in rows
        ],
    }


@router.get("/debug/database/check")
def database_check(
    _admin: None = Depends(require_local_admin),
    repo: Repository = Depends(get_repo),
) -> dict[str, Any]:
    started = time.perf_counter()
    quick_check = [str(row[0]) for row in repo.connection.execute("PRAGMA quick_check")]
    from ai_kp.infrastructure.database.integrity import fts_integrity_issues

    fts_issues = fts_integrity_issues(repo.connection)
    foreign_key_issues = [
        dict(row) for row in repo.connection.execute("PRAGMA foreign_key_check").fetchall()
    ]
    return {
        "ok": quick_check == ["ok"] and not foreign_key_issues and not fts_issues,
        "quick_check": quick_check,
        "foreign_key_issues": foreign_key_issues,
        "fts_issues": fts_issues,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@router.get("/debug/model/probe")
async def model_probe(
    request: Request,
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        models = await discover_openai_models(
            settings.llm_base_url,
            settings.llm_api_key,
            timeout_seconds=4,
            client=getattr(request.app.state, "http_client", None),
        )
        return {
            "ok": True,
            "base_url": settings.llm_base_url,
            "configured_model": settings.llm_model,
            "models": models,
            "configured_model_visible": settings.llm_model in models,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "runtime": _runtime(request).status(),
        }
    # A diagnostic endpoint must report failures from any subsystem instead of
    # turning the entire debug console request into another opaque 500 response.
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "base_url": settings.llm_base_url,
            "configured_model": settings.llm_model,
            "error": str(exc)[:1000],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "runtime": _runtime(request).status(),
        }


@router.get("/debug/logs")
def debug_logs(
    request: Request,
    lines: int = Query(default=120, ge=1, le=1000),
    _admin: None = Depends(require_local_admin),
) -> dict[str, Any]:
    runtime = _runtime(request)
    return {
        "source": "local-model-runtime",
        "path": str(runtime.log_path),
        "runtime": runtime.status(),
        "lines": _tail_file(runtime.log_path, lines),
    }


@router.post("/debug/xlsx/preview")
async def debug_xlsx_preview(
    request: Request,
    x_file_name: str | None = Header(default=None),
    _admin: None = Depends(require_local_admin),
) -> dict[str, Any]:
    data = await read_limited_body(
        request,
        max_bytes=MAX_XLSX_BYTES,
        label="调查员 XLSX",
    )
    filename = safe_upload_filename(x_file_name, default="character.xlsx")
    return await run_in_threadpool(import_coc_character_xlsx, data, filename)


@router.websocket("/debug/ws")
async def debug_realtime_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    local_debug_origins = {
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8002",
        "http://localhost:8000",
        "http://localhost:8002",
    }
    await handle_realtime_websocket(
        websocket,
        db_path=settings.db_path,
        allowed_origins=(*settings.cors_origin_list, *local_debug_origins),
    )


__all__ = ["DebugTelemetry", "DebugTelemetryMiddleware", "router"]

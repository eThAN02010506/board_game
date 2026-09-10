import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ai_kp.application.errors import (
    AccessDeniedError,
    ConflictError,
    DirectorHelpAuditUnavailableError,
    DirectorHelpClientDisconnectedError,
    InvalidInputError,
    ResourceNotFoundError,
    UpstreamInvalidResponseError,
    UpstreamServiceError,
)
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.infrastructure.backups import BackupNotFoundError, BackupVerificationError
from ai_kp.infrastructure.llm.director_help_call_gate import (
    DirectorHelpCapacityExceededError,
    DirectorHelpRunInProgressError,
)


def _error(status_code: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "code": code},
    )


async def not_found_handler(_request: Request, exc: Exception) -> JSONResponse:
    return _error(404, str(exc), "not_found")


async def conflict_handler(_request: Request, exc: Exception) -> JSONResponse:
    return _error(409, str(exc), "conflict")


async def forbidden_handler(_request: Request, exc: Exception) -> JSONResponse:
    return _error(403, str(exc), "forbidden")


async def invalid_input_handler(_request: Request, exc: Exception) -> JSONResponse:
    return _error(422, str(exc), "invalid_input")


async def upstream_handler(_request: Request, exc: Exception) -> JSONResponse:
    return _error(502, str(exc), "upstream_service_error")


async def upstream_invalid_response_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error(502, str(exc), "upstream_invalid_response")


async def director_help_in_progress_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error(409, str(exc), "director_help_in_progress")


async def director_help_capacity_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error(429, str(exc), "director_help_capacity_exceeded")


async def client_disconnected_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error(499, str(exc), "client_disconnected")


async def director_help_audit_unavailable_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return _error(503, str(exc), "director_help_audit_unavailable")


async def integrity_conflict_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return _error(409, "Conflicting resource state", "integrity_conflict")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ResourceNotFoundError, not_found_handler)
    app.add_exception_handler(ConflictError, conflict_handler)
    app.add_exception_handler(CampaignAiCallCancelledError, conflict_handler)
    app.add_exception_handler(AccessDeniedError, forbidden_handler)
    app.add_exception_handler(InvalidInputError, invalid_input_handler)
    app.add_exception_handler(UpstreamServiceError, upstream_handler)
    app.add_exception_handler(
        UpstreamInvalidResponseError,
        upstream_invalid_response_handler,
    )
    app.add_exception_handler(
        DirectorHelpRunInProgressError,
        director_help_in_progress_handler,
    )
    app.add_exception_handler(
        DirectorHelpCapacityExceededError,
        director_help_capacity_handler,
    )
    app.add_exception_handler(
        DirectorHelpClientDisconnectedError,
        client_disconnected_handler,
    )
    app.add_exception_handler(
        DirectorHelpAuditUnavailableError,
        director_help_audit_unavailable_handler,
    )
    app.add_exception_handler(BackupVerificationError, invalid_input_handler)
    app.add_exception_handler(BackupNotFoundError, not_found_handler)
    # Compatibility handlers remain until older repositories use typed errors.
    app.add_exception_handler(KeyError, not_found_handler)
    app.add_exception_handler(ValueError, conflict_handler)
    app.add_exception_handler(PermissionError, forbidden_handler)
    app.add_exception_handler(sqlite3.IntegrityError, integrity_conflict_handler)

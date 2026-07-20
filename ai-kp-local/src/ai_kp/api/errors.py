import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


async def not_found_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


async def conflict_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def integrity_conflict_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "Conflicting resource state"})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(KeyError, not_found_handler)
    app.add_exception_handler(ValueError, conflict_handler)
    app.add_exception_handler(sqlite3.IntegrityError, integrity_conflict_handler)

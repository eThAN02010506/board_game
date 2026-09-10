"""Short, independent transactions for Need Help audit lifecycle events."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generic, ParamSpec, TypeVar, cast

from ai_kp.application.director_help_audit_service import (
    DirectorHelpAuditAttempt,
    DirectorHelpAuditService,
    DirectorHelpTerminalOutcome,
)
from ai_kp.application.errors import DirectorHelpAuditUnavailableError
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect

_AUDIT_UNAVAILABLE_MESSAGE = "Need Help is unavailable because its audit record could not be stored"
P = ParamSpec("P")
T = TypeVar("T")


@dataclass(frozen=True)
class DirectorHelpAuditIoResult(Generic[T]):
    value: T | None = None
    error: BaseException | None = None
    cancellation: asyncio.CancelledError | None = None


async def settle_director_help_audit_task(
    task: asyncio.Task[T],
) -> DirectorHelpAuditIoResult[T]:
    """Drain one strong-referenced worker despite repeated caller cancellation."""

    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.cancelled():
                break
            if cancellation is None:
                cancellation = exc
        except Exception:  # noqa: BLE001
            # Worker failures are returned below as data so callers can settle
            # a frozen terminal proposal before choosing HTTP error precedence.
            break
    try:
        value = task.result()
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
        # The operation boundary deliberately captures any ordinary worker
        # failure; KeyboardInterrupt and SystemExit still propagate.
        return DirectorHelpAuditIoResult(error=exc, cancellation=cancellation)
    return DirectorHelpAuditIoResult(value=value, cancellation=cancellation)


async def settle_director_help_audit_io(
    operation: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> DirectorHelpAuditIoResult[T]:
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    return await settle_director_help_audit_task(task)


async def run_director_help_audit_io(
    operation: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Run blocking audit I/O off-loop and finish it before propagating cancellation."""

    result = await settle_director_help_audit_io(operation, *args, **kwargs)
    if result.cancellation is not None:
        raise result.cancellation
    if result.error is not None:
        raise result.error
    return cast(T, result.value)


@contextmanager
def _audit_transaction(settings: Settings) -> Iterator[DirectorHelpAuditService]:
    connection = connect(
        settings.db_path,
        synchronous=settings.sqlite_synchronous,
    )
    try:
        yield DirectorHelpAuditService(Repository(connection))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def begin_director_help_audit(
    settings: Settings,
    *,
    campaign_id: str,
    run_id: str,
    requested_by_member_id: str,
    request_id: str,
    question: str,
) -> DirectorHelpAuditAttempt:
    try:
        with _audit_transaction(settings) as audit:
            return audit.begin(
                campaign_id=campaign_id,
                run_id=run_id,
                requested_by_member_id=requested_by_member_id,
                request_id=request_id,
                question=question,
            )
    except Exception as exc:
        raise DirectorHelpAuditUnavailableError(_AUDIT_UNAVAILABLE_MESSAGE) from exc


def complete_director_help_audit(
    settings: Settings,
    attempt: DirectorHelpAuditAttempt,
    advice: dict[str, Any],
    *,
    duration_ms: int,
) -> str:
    try:
        with _audit_transaction(settings) as audit:
            return audit.complete(attempt, advice, duration_ms=duration_ms)
    except Exception as exc:
        raise DirectorHelpAuditUnavailableError(_AUDIT_UNAVAILABLE_MESSAGE) from exc


def terminate_director_help_audit(
    settings: Settings,
    attempt: DirectorHelpAuditAttempt,
    *,
    outcome: DirectorHelpTerminalOutcome,
    error_code: str,
    duration_ms: int,
) -> None:
    try:
        with _audit_transaction(settings) as audit:
            audit.terminate(
                attempt,
                outcome=outcome,
                error_code=error_code,
                duration_ms=duration_ms,
            )
    except Exception as exc:
        raise DirectorHelpAuditUnavailableError(_AUDIT_UNAVAILABLE_MESSAGE) from exc


__all__ = [
    "DirectorHelpAuditIoResult",
    "begin_director_help_audit",
    "complete_director_help_audit",
    "run_director_help_audit_io",
    "settle_director_help_audit_io",
    "settle_director_help_audit_task",
    "terminate_director_help_audit",
]

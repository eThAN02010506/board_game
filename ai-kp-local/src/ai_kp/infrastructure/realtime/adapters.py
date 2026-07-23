"""Starlette and SQLite adapters for the realtime application ports."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
from typing import Any, TypeVar

from fastapi import WebSocket, WebSocketDisconnect

from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.realtime.ports import FrameTooLargeError, RealtimeDisconnected
from ai_kp.platform.sessions.models import AuthenticatedMember


SEND_TIMEOUT_SECONDS = 5.0
MAX_CLIENT_FRAME_BYTES = 8 * 1024

T = TypeVar("T")


async def run_thread_safely(
    operation: Callable[..., T], /, *args: object, **kwargs: object
) -> T:
    """Finish a started worker operation before cancellation closes SQLite."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise


class StarletteRealtimeChannel:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        max_frame_bytes: int = MAX_CLIENT_FRAME_BYTES,
        send_timeout: float = SEND_TIMEOUT_SECONDS,
    ) -> None:
        self.websocket = websocket
        self.max_frame_bytes = max_frame_bytes
        self.send_timeout = send_timeout

    async def receive_json(self) -> object:
        try:
            raw_message = await self.websocket.receive_text()
        except WebSocketDisconnect as error:
            raise RealtimeDisconnected from error
        if len(raw_message.encode("utf-8")) > self.max_frame_bytes:
            raise FrameTooLargeError
        return json.loads(raw_message)

    async def send_json(self, payload: dict[str, Any]) -> None:
        await asyncio.wait_for(
            self.websocket.send_json(payload),
            timeout=self.send_timeout,
        )

    async def close(self, code: int, reason: str) -> None:
        await self.websocket.close(code=code, reason=reason)


class ThreadedRealtimeStore:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def consume_ticket(self, ticket: str) -> AuthenticatedMember | None:
        return await run_thread_safely(self.repo.consume_realtime_ticket, ticket)

    async def resolve_cursor(
        self,
        *,
        event_key: str,
        session_id: str,
        role: str,
        member_id: str,
    ) -> int | None:
        return await run_thread_safely(
            self.repo.resolve_visible_realtime_cursor,
            event_key=event_key,
            session_id=session_id,
            role=role,
            member_id=member_id,
        )

    async def get_active_member(
        self,
        member_id: str,
        session_id: str,
    ) -> AuthenticatedMember | None:
        return await run_thread_safely(
            self.repo.get_active_session_member,
            member_id,
            session_id,
        )

    async def get_connection_status(self, member_id: str, session_id: str) -> str:
        return await run_thread_safely(
            self.repo.get_session_member_connection_status,
            member_id,
            session_id,
        )

    async def list_events(
        self,
        *,
        session_id: str,
        role: str,
        member_id: str,
        after_cursor: int,
    ) -> list[dict[str, Any]]:
        return await run_thread_safely(
            self.repo.list_visible_realtime_events,
            session_id=session_id,
            role=role,
            member_id=member_id,
            after_cursor=after_cursor,
        )

    def event_for_wire(self, event: dict[str, Any]) -> dict[str, Any]:
        return self.repo.realtime_event_for_wire(event)

    async def close(self) -> None:
        await run_thread_safely(self.repo.connection.close)

import asyncio
from collections.abc import Callable, Iterable
from contextlib import suppress
import json
from pathlib import Path
import time
from typing import TypeVar

from fastapi import WebSocket, WebSocketDisconnect

from ai_kp.core.db import connect
from ai_kp.core.repository import Repository


AUTH_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.35
SEND_TIMEOUT_SECONDS = 5.0
HEARTBEAT_INTERVAL_SECONDS = 15.0
CLIENT_TIMEOUT_SECONDS = 45.0
MAX_CLIENT_FRAME_BYTES = 8 * 1024

T = TypeVar("T")


class FrameTooLargeError(ValueError):
    pass


async def _run_thread_safely(
    operation: Callable[..., T], /, *args: object, **kwargs: object
) -> T:
    """Finish a started worker operation before cancellation can close its resources."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise


async def _receive_limited_json(websocket: WebSocket) -> object:
    raw_message = await websocket.receive_text()
    if len(raw_message.encode("utf-8")) > MAX_CLIENT_FRAME_BYTES:
        raise FrameTooLargeError
    return json.loads(raw_message)


async def _send_json(websocket: WebSocket, payload: dict) -> None:
    await asyncio.wait_for(
        websocket.send_json(payload),
        timeout=SEND_TIMEOUT_SECONDS,
    )


async def handle_realtime_websocket(
    websocket: WebSocket,
    *,
    db_path: Path | str,
    allowed_origins: Iterable[str],
) -> None:
    origin = websocket.headers.get("origin")
    origin_allowlist = set(allowed_origins)
    if not origin or origin not in origin_allowlist:
        await websocket.close(code=4403, reason="Origin not allowed")
        return

    await websocket.accept()
    connection = await _run_thread_safely(connect, db_path)
    repo = Repository(connection)
    receive_task: asyncio.Task[object] | None = None
    try:
        try:
            message = await asyncio.wait_for(
                _receive_limited_json(websocket),
                timeout=AUTH_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            await websocket.close(code=4408, reason="Authentication timeout")
            return
        except FrameTooLargeError:
            await websocket.close(code=4409, reason="Authentication frame too large")
            return
        except (WebSocketDisconnect, ValueError, RuntimeError):
            await websocket.close(code=4400, reason="Invalid authentication frame")
            return

        if not isinstance(message, dict) or message.get("type") != "authenticate":
            await websocket.close(code=4400, reason="Authentication frame required")
            return
        ticket = message.get("ticket")
        if not isinstance(ticket, str) or not ticket:
            await websocket.close(code=4401, reason="Invalid realtime ticket")
            return
        identity = await _run_thread_safely(repo.consume_realtime_ticket, ticket)
        ticket = ""
        if identity is None:
            await websocket.close(code=4401, reason="Invalid realtime ticket")
            return
        requested_cursor = message.get("after_cursor")
        cursor_key = requested_cursor if isinstance(requested_cursor, str) else ""
        resolved_cursor = (
            await _run_thread_safely(
                repo.resolve_visible_realtime_cursor,
                event_key=cursor_key,
                session_id=identity.session_id,
                role=identity.role,
                member_id=identity.member_id,
            )
            if cursor_key
            else 0
        )
        resync_required = resolved_cursor is None
        cursor = resolved_cursor or 0
        if resync_required:
            cursor_key = ""
        message = {}
        await _send_json(
            websocket,
            {
                "type": "realtime.ready",
                "session_id": identity.session_id,
                "campaign_id": identity.campaign_id,
                "member_id": identity.member_id,
                "role": identity.role,
                "cursor": cursor_key,
                "resync_required": resync_required,
            }
        )
        last_client_activity = time.monotonic()
        last_heartbeat = last_client_activity

        while True:
            refreshed_identity = await _run_thread_safely(
                repo.get_active_session_member,
                identity.member_id,
                identity.session_id,
            )
            if refreshed_identity is None:
                connection_status = await _run_thread_safely(
                    repo.get_session_member_connection_status,
                    identity.member_id,
                    identity.session_id,
                )
                close_code = 4403 if connection_status == "revoked" else 4408
                await _send_json(
                    websocket,
                    {
                        "type": "realtime.auth_expired",
                        "reason": connection_status,
                    },
                )
                await websocket.close(close_code, reason="Session credential expired")
                return
            identity = refreshed_identity
            events = await _run_thread_safely(
                repo.list_visible_realtime_events,
                session_id=identity.session_id,
                role=identity.role,
                member_id=identity.member_id,
                after_cursor=cursor,
            )
            for event in events:
                wire_event = repo.realtime_event_for_wire(event)
                await _send_json(
                    websocket,
                    {"type": "realtime.event", "event": wire_event},
                )
                cursor = int(event["id"])
                cursor_key = str(event["event_key"])

            now = time.monotonic()
            if now - last_client_activity >= CLIENT_TIMEOUT_SECONDS:
                await websocket.close(code=4408, reason="Client heartbeat timeout")
                return
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                await _send_json(websocket, {"type": "realtime.ping"})
                last_heartbeat = now

            if receive_task is None:
                receive_task = asyncio.create_task(_receive_limited_json(websocket))
            done, _pending = await asyncio.wait(
                {receive_task},
                timeout=POLL_INTERVAL_SECONDS,
            )
            if not done:
                continue
            completed_task = receive_task
            receive_task = None
            try:
                client_message = completed_task.result()
            except WebSocketDisconnect:
                return
            except FrameTooLargeError:
                await websocket.close(code=4409, reason="Client frame too large")
                return
            except (ValueError, RuntimeError):
                await _send_json(
                    websocket,
                    {"type": "protocol.error", "detail": "Expected a JSON object"}
                )
                continue
            last_client_activity = time.monotonic()
            if not isinstance(client_message, dict):
                await _send_json(
                    websocket,
                    {"type": "protocol.error", "detail": "Expected a JSON object"}
                )
            elif client_message.get("type") == "ping":
                await _send_json(websocket, {"type": "pong", "cursor": cursor_key})
            elif client_message.get("type") == "pong":
                continue
            else:
                await _send_json(
                    websocket,
                    {"type": "protocol.error", "detail": "Unsupported client message"}
                )
    finally:
        if receive_task is not None and not receive_task.done():
            receive_task.cancel()
            with suppress(asyncio.CancelledError):
                await receive_task
        await _run_thread_safely(connection.close)

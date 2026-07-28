"""WebSocket transport composition for authenticated realtime sessions."""

from collections.abc import Iterable
from pathlib import Path

from fastapi import WebSocket

from ai_kp.application.realtime import RealtimeSession
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect
from ai_kp.infrastructure.realtime.adapters import (
    MAX_CLIENT_FRAME_BYTES,
    SEND_TIMEOUT_SECONDS,
    StarletteRealtimeChannel,
    ThreadedRealtimeStore,
    run_thread_safely,
)
from ai_kp.platform.realtime.ports import FrameTooLargeError, RealtimeDisconnected

AUTH_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.35
HEARTBEAT_INTERVAL_SECONDS = 15.0
CLIENT_TIMEOUT_SECONDS = 45.0


async def handle_realtime_websocket(
    websocket: WebSocket,
    *,
    db_path: Path | str,
    allowed_origins: Iterable[str],
) -> None:
    origin = websocket.headers.get("origin")
    if not origin or origin not in set(allowed_origins):
        await websocket.close(code=4403, reason="Origin not allowed")
        return

    await websocket.accept()
    connection = await run_thread_safely(connect, db_path)
    store = ThreadedRealtimeStore(Repository(connection))
    channel = StarletteRealtimeChannel(websocket)
    try:
        await RealtimeSession(channel, store).run()
    except RealtimeDisconnected:
        return
    finally:
        await store.close()


__all__ = [
    "AUTH_TIMEOUT_SECONDS",
    "CLIENT_TIMEOUT_SECONDS",
    "HEARTBEAT_INTERVAL_SECONDS",
    "MAX_CLIENT_FRAME_BYTES",
    "POLL_INTERVAL_SECONDS",
    "SEND_TIMEOUT_SECONDS",
    "FrameTooLargeError",
    "RealtimeDisconnected",
    "handle_realtime_websocket",
]

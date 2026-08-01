"""Framework-neutral lifecycle for one authenticated realtime connection."""

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass

from ai_kp.application.realtime.messages import (
    ProtocolRejection,
    client_response,
    parse_authentication,
    ready_message,
)
from ai_kp.platform.realtime.ports import (
    FrameTooLargeError,
    RealtimeChannel,
    RealtimeDisconnected,
    RealtimeStore,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


@dataclass(frozen=True)
class RealtimePolicy:
    authentication_timeout: float = 5.0
    poll_interval: float = 0.35
    heartbeat_interval: float = 15.0
    client_timeout: float = 45.0


@dataclass
class RealtimeState:
    identity: AuthenticatedMember
    cursor: int
    cursor_key: str
    last_client_activity: float
    last_heartbeat: float


class RealtimeSession:
    def __init__(
        self,
        channel: RealtimeChannel,
        store: RealtimeStore,
        *,
        policy: RealtimePolicy | None = None,
    ) -> None:
        self.channel = channel
        self.store = store
        self.policy = policy or RealtimePolicy()
        self._receive_task: asyncio.Task[object] | None = None

    async def run(self) -> None:
        state = await self._authenticate()
        if state is None:
            return
        try:
            while await self._connection_is_active(state):
                await self._deliver_events(state)
                if await self._apply_timers(state):
                    return
                await self._poll_client(state)
        finally:
            await self._cancel_receive()

    async def _authenticate(self) -> RealtimeState | None:
        try:
            message = await asyncio.wait_for(
                self.channel.receive_json(),
                timeout=self.policy.authentication_timeout,
            )
        except TimeoutError:
            await self.channel.close(4408, "Authentication timeout")
            return None
        except FrameTooLargeError:
            await self.channel.close(4409, "Authentication frame too large")
            return None
        except (RealtimeDisconnected, ValueError, RuntimeError):
            await self.channel.close(4400, "Invalid authentication frame")
            return None

        request = parse_authentication(message)
        if isinstance(request, ProtocolRejection):
            await self.channel.close(request.code, request.reason)
            return None
        identity = await self.store.consume_ticket(request.ticket)
        if identity is None:
            await self.channel.close(4401, "Invalid realtime ticket")
            return None

        cursor_key = request.after_cursor
        resolved_cursor = (
            await self.store.resolve_cursor(
                event_key=cursor_key,
                session_id=identity.session_id,
                role=identity.role,
                member_id=identity.member_id,
            )
            if cursor_key
            else 0
        )
        resync_required = resolved_cursor is None
        if resync_required:
            cursor_key = ""
        await self.channel.send_json(
            ready_message(
                identity,
                cursor_key=cursor_key,
                resync_required=resync_required,
            )
        )
        now = time.monotonic()
        return RealtimeState(
            identity=identity,
            cursor=resolved_cursor or 0,
            cursor_key=cursor_key,
            last_client_activity=now,
            last_heartbeat=now,
        )

    async def _connection_is_active(self, state: RealtimeState) -> bool:
        refreshed = await self.store.get_active_member(
            state.identity.member_id,
            state.identity.session_id,
        )
        if refreshed is not None:
            state.identity = refreshed
            return True
        status = await self.store.get_connection_status(
            state.identity.member_id,
            state.identity.session_id,
        )
        await self.channel.send_json(
            {"type": "realtime.auth_expired", "reason": status}
        )
        await self.channel.close(
            4403 if status == "revoked" else 4408,
            "Session credential expired",
        )
        return False

    async def _deliver_events(self, state: RealtimeState) -> None:
        events = await self.store.list_events(
            session_id=state.identity.session_id,
            role=state.identity.role,
            member_id=state.identity.member_id,
            after_cursor=state.cursor,
        )
        for event in events:
            await self.channel.send_json(
                {
                    "type": "realtime.event",
                    "event": self.store.event_for_wire(event),
                }
            )
            state.cursor = int(event["id"])
            state.cursor_key = str(event["event_key"])

    async def _apply_timers(self, state: RealtimeState) -> bool:
        now = time.monotonic()
        if now - state.last_client_activity >= self.policy.client_timeout:
            await self.channel.close(4408, "Client heartbeat timeout")
            return True
        if now - state.last_heartbeat >= self.policy.heartbeat_interval:
            await self.channel.send_json({"type": "realtime.ping"})
            state.last_heartbeat = now
        return False

    async def _poll_client(self, state: RealtimeState) -> None:
        if self._receive_task is None:
            self._receive_task = asyncio.create_task(self.channel.receive_json())
        done, _pending = await asyncio.wait(
            {self._receive_task},
            timeout=self.policy.poll_interval,
        )
        if not done:
            return
        completed_task = self._receive_task
        self._receive_task = None
        try:
            message = completed_task.result()
        except RealtimeDisconnected:
            raise
        except FrameTooLargeError:
            await self.channel.close(4409, "Client frame too large")
            raise RealtimeDisconnected
        except (ValueError, RuntimeError):
            await self.channel.send_json(
                {"type": "protocol.error", "detail": "Expected a JSON object"}
            )
            return
        state.last_client_activity = time.monotonic()
        response = client_response(message, cursor_key=state.cursor_key)
        if response is not None:
            await self.channel.send_json(response)

    async def _cancel_receive(self) -> None:
        task = self._receive_task
        self._receive_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        with suppress(
            asyncio.CancelledError,
            RealtimeDisconnected,
            FrameTooLargeError,
            ValueError,
            RuntimeError,
        ):
            await task

"""Ports shared by realtime application services and delivery adapters."""

from typing import Any, Protocol

from ai_kp.platform.sessions.models import AuthenticatedMember


class RealtimeDisconnected(Exception):
    """The client closed its transport while a frame was being received."""


class FrameTooLargeError(ValueError):
    """The client sent a frame larger than the configured protocol limit."""


class RealtimeChannel(Protocol):
    async def receive_json(self) -> object: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self, code: int, reason: str) -> None: ...


class RealtimeStore(Protocol):
    async def consume_ticket(self, ticket: str) -> AuthenticatedMember | None: ...

    async def resolve_cursor(
        self,
        *,
        event_key: str,
        session_id: str,
        role: str,
        member_id: str,
    ) -> int | None: ...

    async def get_active_member(
        self,
        member_id: str,
        session_id: str,
    ) -> AuthenticatedMember | None: ...

    async def get_connection_status(self, member_id: str, session_id: str) -> str: ...

    async def list_events(
        self,
        *,
        session_id: str,
        role: str,
        member_id: str,
        after_cursor: int,
    ) -> list[dict[str, Any]]: ...

    def event_for_wire(self, event: dict[str, Any]) -> dict[str, Any]: ...

    async def close(self) -> None: ...

import unittest
from typing import Any

from ai_kp.application.realtime.messages import (
    AuthenticationRequest,
    ProtocolRejection,
    client_response,
    parse_authentication,
)
from ai_kp.application.realtime.session import RealtimePolicy, RealtimeSession
from ai_kp.platform.realtime import RealtimeDisconnected
from ai_kp.platform.sessions.models import AuthenticatedMember


IDENTITY = AuthenticatedMember(
    member_id="member-1",
    session_id="session-1",
    campaign_id="campaign-1",
    role="player",
    display_name="Player",
    pc_id="pc-1",
)


class FakeChannel:
    def __init__(self, messages: list[object]) -> None:
        self.messages = iter(messages)
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str] | None = None

    async def receive_json(self) -> object:
        try:
            message = next(self.messages)
        except StopIteration as error:
            raise RealtimeDisconnected from error
        if isinstance(message, Exception):
            raise message
        return message

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


class FakeStore:
    def __init__(
        self,
        *,
        identity: AuthenticatedMember | None = IDENTITY,
        resolved_cursor: int | None = 0,
    ) -> None:
        self.identity = identity
        self.resolved_cursor = resolved_cursor

    async def consume_ticket(self, ticket: str) -> AuthenticatedMember | None:
        return self.identity if ticket == "ticket-1" else None

    async def resolve_cursor(self, **_scope: Any) -> int | None:
        return self.resolved_cursor

    async def get_active_member(
        self, _member_id: str, _session_id: str
    ) -> AuthenticatedMember | None:
        return self.identity

    async def get_connection_status(
        self, _member_id: str, _session_id: str
    ) -> str:
        return "revoked"

    async def list_events(self, **_scope: Any) -> list[dict[str, Any]]:
        return []

    def event_for_wire(self, event: dict[str, Any]) -> dict[str, Any]:
        return event

    async def close(self) -> None:
        return None


class RealtimeMessageTests(unittest.TestCase):
    def test_authentication_parser_separates_ticket_and_cursor(self) -> None:
        parsed = parse_authentication(
            {
                "type": "authenticate",
                "ticket": "ticket-1",
                "after_cursor": "event-7",
            }
        )

        self.assertEqual(
            parsed,
            AuthenticationRequest(ticket="ticket-1", after_cursor="event-7"),
        )

    def test_authentication_parser_rejects_wrong_frame_and_credential(self) -> None:
        self.assertEqual(
            parse_authentication({"type": "ping"}),
            ProtocolRejection(4400, "Authentication frame required"),
        )
        self.assertEqual(
            parse_authentication({"type": "authenticate", "access_token": "secret"}),
            ProtocolRejection(4401, "Invalid realtime ticket"),
        )

    def test_client_protocol_is_independent_of_transport(self) -> None:
        self.assertEqual(
            client_response({"type": "ping"}, cursor_key="event-7"),
            {"type": "pong", "cursor": "event-7"},
        )
        self.assertIsNone(client_response({"type": "pong"}, cursor_key="event-7"))
        self.assertEqual(
            client_response(["not", "an", "object"], cursor_key=""),
            {"type": "protocol.error", "detail": "Expected a JSON object"},
        )


class RealtimeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_handles_ping_after_authentication(self) -> None:
        channel = FakeChannel(
            [
                {"type": "authenticate", "ticket": "ticket-1"},
                {"type": "ping"},
            ]
        )
        session = RealtimeSession(
            channel,
            FakeStore(),
            policy=RealtimePolicy(poll_interval=0.01),
        )

        with self.assertRaises(RealtimeDisconnected):
            await session.run()

        self.assertEqual(channel.sent[0]["type"], "realtime.ready")
        self.assertIn({"type": "pong", "cursor": ""}, channel.sent)

    async def test_unknown_cursor_requests_full_resync(self) -> None:
        channel = FakeChannel(
            [
                {
                    "type": "authenticate",
                    "ticket": "ticket-1",
                    "after_cursor": "unknown",
                }
            ]
        )
        session = RealtimeSession(
            channel,
            FakeStore(resolved_cursor=None),
            policy=RealtimePolicy(poll_interval=0.01),
        )

        with self.assertRaises(RealtimeDisconnected):
            await session.run()

        self.assertEqual(channel.sent[0]["cursor"], "")
        self.assertTrue(channel.sent[0]["resync_required"])

    async def test_invalid_ticket_closes_without_entering_event_loop(self) -> None:
        channel = FakeChannel(
            [{"type": "authenticate", "ticket": "not-valid"}]
        )

        await RealtimeSession(channel, FakeStore()).run()

        self.assertEqual(channel.closed, (4401, "Invalid realtime ticket"))
        self.assertEqual(channel.sent, [])


if __name__ == "__main__":
    unittest.main()

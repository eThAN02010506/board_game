import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.core.db import connect
from ai_kp.core.repository import Repository
from ai_kp.security.tokens import hash_realtime_ticket
from tests.support_investigators import (
    coc7_sheet,
    confirm_current_session_zero_sync,
    create_approved_player_sync,
)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class RealtimeDefaultSettingsTests(unittest.TestCase):
    def test_default_frontend_origins_include_both_loopback_names(self) -> None:
        origins = set(Settings(_env_file=None).cors_origin_list)

        self.assertIn("http://localhost:5173", origins)
        self.assertIn("http://127.0.0.1:5173", origins)


class RealtimeWebSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "realtime.sqlite3"
        self.settings = Settings(
            db_path=self.db_path,
            cors_origins="http://testserver",
            admin_token="realtime-test-admin",
        )
        self.client = TestClient(create_app(self.settings))
        self.admin_headers = {"X-AI-KP-Admin-Token": "realtime-test-admin"}
        self.campaign = self.client.post(
            "/campaigns",
            headers=self.admin_headers,
            json={"title": "Realtime Campaign"},
        ).json()
        self.session = self.client.post(
            f"/campaigns/{self.campaign['id']}/sessions",
            headers=self.admin_headers,
            json={"kp_display_name": "Realtime KP"},
        ).json()
        self.kp_headers = bearer(self.session["access_token"])
        approved_player = create_approved_player_sync(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Realtime Player",
            sheet=coc7_sheet("Realtime Investigator"),
        )
        self.pc = approved_player["pc"]
        self.player = approved_player["bundle"]
        self.player_headers = approved_player["headers"]
        confirm_current_session_zero_sync(
            self.client,
            campaign_id=self.campaign["id"],
            member_headers=(self.kp_headers, self.player_headers),
        )
        self.saved_map = self.client.post(
            f"/campaigns/{self.campaign['id']}/maps/generate",
            headers=self.kp_headers,
            json={
                "title": "Realtime Map",
                "prompt": "Start, Hall",
                "locations": ["Start", "Hall"],
                "routes": [["Start", "Hall"]],
            },
        ).json()
        self.token = self.client.post(
            f"/maps/{self.saved_map['id']}/tokens",
            headers=self.kp_headers,
            json={
                "label": "Investigator Token",
                "location_name": "Start",
                "actor_type": "pc",
                "actor_id": self.pc["id"],
            },
        ).json()
        self.client.post(
            f"/maps/{self.saved_map['id']}/publish",
            headers=self.kp_headers,
            json={
                "expected_revision_id": self.saved_map["revision_id"],
                "expected_selected_asset_id": None,
            },
        ).raise_for_status()
    def tearDown(self) -> None:
        self.client.close()
        self.tmpdir.cleanup()

    def issue_ticket(self, headers: dict[str, str]) -> str:
        response = self.client.post("/realtime/tickets", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["ticket"])

    def latest_visible_cursor(self, headers: dict[str, str]) -> str:
        identity = self.client.get("/auth/me", headers=headers).json()
        connection = connect(self.db_path)
        try:
            events = Repository(connection).list_visible_realtime_events(
                session_id=identity["session_id"],
                role=identity["role"],
                member_id=identity["member_id"],
                after_cursor=0,
                limit=200,
            )
            return str(events[-1]["event_key"]) if events else ""
        finally:
            connection.close()

    def authenticate_socket(self, websocket, headers: dict[str, str], cursor: str = "") -> dict:
        websocket.send_json(
            {
                "type": "authenticate",
                "ticket": self.issue_ticket(headers),
                "after_cursor": cursor,
            }
        )
        ready = websocket.receive_json()
        self.assertEqual(ready["type"], "realtime.ready")
        return ready

    def assert_ticket_rejected(self, ticket: str, expected_code: int = 4401) -> None:
        with self.assertRaises(WebSocketDisconnect) as rejected:
            with self.client.websocket_connect(
                "/ws",
                headers={"origin": "http://testserver"},
            ) as websocket:
                websocket.send_json({"type": "authenticate", "ticket": ticket})
                websocket.receive_json()
        self.assertEqual(rejected.exception.code, expected_code)

    def test_ticket_response_is_not_cacheable(self) -> None:
        response = self.client.post("/realtime/tickets", headers=self.player_headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_websocket_rejects_missing_and_untrusted_origins(self) -> None:
        for headers in ({}, {"origin": "http://attacker.invalid"}):
            with self.subTest(headers=headers):
                with self.assertRaises(WebSocketDisconnect) as denied:
                    with self.client.websocket_connect("/ws", headers=headers):
                        pass
                self.assertEqual(denied.exception.code, 4403)

    def test_expired_ticket_cannot_be_consumed(self) -> None:
        ticket = self.issue_ticket(self.player_headers)
        connection = connect(self.db_path)
        try:
            connection.execute(
                "UPDATE realtime_tickets SET expires_at = ? WHERE ticket_hash = ?",
                (int(time.time()) - 1, hash_realtime_ticket(ticket)),
            )
            connection.commit()
        finally:
            connection.close()

        self.assert_ticket_rejected(ticket)

    def test_ticket_issued_before_member_revocation_cannot_be_consumed(self) -> None:
        ticket = self.issue_ticket(self.player_headers)
        revoked = self.client.post(
            f"/sessions/{self.session['session']['id']}/members/"
            f"{self.player['member']['id']}/revoke",
            headers=self.kp_headers,
        )

        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assert_ticket_rejected(ticket)

    def test_ticket_issued_before_session_close_cannot_be_consumed(self) -> None:
        ticket = self.issue_ticket(self.kp_headers)
        closed = self.client.post(
            f"/sessions/{self.session['session']['id']}/close",
            headers=self.kp_headers,
        )

        self.assertEqual(closed.status_code, 200, closed.text)
        self.assert_ticket_rejected(ticket)

    def test_concurrent_ticket_consumption_succeeds_exactly_once(self) -> None:
        ticket = self.issue_ticket(self.player_headers)
        start = threading.Barrier(2)

        def consume() -> bool:
            connection = connect(self.db_path)
            try:
                start.wait(timeout=2)
                return Repository(connection).consume_realtime_ticket(ticket) is not None
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: consume(), range(2)))

        self.assertEqual(sorted(outcomes), [False, True])

    def test_saved_map_can_be_listed_and_loaded_after_app_restart(self) -> None:
        moved = self.client.post(
            f"/map-tokens/{self.token['id']}/move",
            headers=self.kp_headers,
            json={
                "to_location_name": "Hall",
                "expected_version": self.token["version"],
            },
        )
        self.assertEqual(moved.status_code, 200, moved.text)

        with TestClient(create_app(self.settings)) as restarted_client:
            listed = restarted_client.get(
                f"/campaigns/{self.campaign['id']}/maps",
                headers=self.kp_headers,
            )
            loaded = restarted_client.get(
                f"/maps/{self.saved_map['id']}",
                headers=self.kp_headers,
            )

        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn(self.saved_map["id"], {item["id"] for item in listed.json()})
        self.assertEqual(loaded.status_code, 200, loaded.text)
        restored = loaded.json()
        self.assertEqual(restored["id"], self.saved_map["id"])
        self.assertEqual(restored["prompt"], "Start, Hall")
        self.assertTrue(restored["svg_text"])
        restored_token = next(item for item in restored["tokens"] if item["id"] == self.token["id"])
        self.assertEqual(restored_token["location_name"], "Hall")
        self.assertEqual(restored_token["version"], 1)

    def test_stale_map_token_version_cannot_apply_a_second_move(self) -> None:
        initial_moves = self.client.get(
            f"/map-tokens/{self.token['id']}/moves",
            headers=self.kp_headers,
        ).json()
        first_move = self.client.post(
            f"/map-tokens/{self.token['id']}/move",
            headers=self.kp_headers,
            json={
                "to_location_name": "Hall",
                "expected_version": self.token["version"],
            },
        )
        stale_move = self.client.post(
            f"/map-tokens/{self.token['id']}/move",
            headers=self.kp_headers,
            json={
                "to_location_name": "Start",
                "expected_version": self.token["version"],
            },
        )
        loaded = self.client.get(
            f"/maps/{self.saved_map['id']}",
            headers=self.kp_headers,
        )
        moves = self.client.get(
            f"/map-tokens/{self.token['id']}/moves",
            headers=self.kp_headers,
        )

        self.assertEqual(first_move.status_code, 200, first_move.text)
        self.assertEqual(stale_move.status_code, 409, stale_move.text)
        current_token = next(
            item for item in loaded.json()["tokens"] if item["id"] == self.token["id"]
        )
        self.assertEqual(current_token["location_name"], "Hall")
        self.assertEqual(current_token["version"], 1)
        self.assertEqual(len(moves.json()), len(initial_moves) + 1)

    def test_hidden_token_move_and_map_withdrawal_emit_safe_player_events(self) -> None:
        connection = connect(self.db_path)
        try:
            connection.execute(
                "UPDATE map_locations SET visibility = 'kp' WHERE map_id = ? AND name = ?",
                (self.saved_map["id"], "Hall"),
            )
            connection.commit()
        finally:
            connection.close()

        player_cursor = self.latest_visible_cursor(self.player_headers)
        with self.client.websocket_connect(
            "/ws",
            headers={"origin": "http://testserver"},
        ) as player_socket:
            self.authenticate_socket(player_socket, self.player_headers, player_cursor)
            moved = self.client.post(
                f"/map-tokens/{self.token['id']}/move",
                headers=self.kp_headers,
                json={
                    "to_location_name": "Hall",
                    "expected_version": self.token["version"],
                },
            )
            self.assertEqual(moved.status_code, 200, moved.text)

            changed = player_socket.receive_json()
            self.assertEqual(changed["type"], "realtime.event")
            self.assertEqual(changed["event"]["event_type"], "map.changed")
            self.assertEqual(changed["event"]["resource_type"], "map")
            self.assertEqual(changed["event"]["resource_id"], self.saved_map["id"])
            self.assertEqual(changed["event"]["payload"], {"map_id": self.saved_map["id"]})
            self.assertNotIn(self.token["id"], str(changed))
            self.assertNotIn("Investigator Token", str(changed))
            self.assertNotIn("Hall", str(changed))

            player_map = self.client.get(
                f"/maps/{self.saved_map['id']}",
                params={"view": "player"},
                headers=self.player_headers,
            )
            self.assertEqual(player_map.status_code, 200, player_map.text)
            self.assertNotIn(self.token["id"], {item["id"] for item in player_map.json()["tokens"]})
            self.assertNotIn("Hall", {item["name"] for item in player_map.json()["locations"]})

            withdrawn = self.client.post(
                f"/maps/{self.saved_map['id']}/unpublish",
                headers=self.kp_headers,
            )
            self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
            unpublished = player_socket.receive_json()
            self.assertEqual(unpublished["event"]["event_type"], "map.unpublished")
            self.assertEqual(unpublished["event"]["resource_id"], self.saved_map["id"])
            self.assertEqual(unpublished["event"]["payload"], {"status": "draft"})
            self.assertNotIn("svg_text", str(unpublished))
            self.assertNotIn("prompt", str(unpublished))
            self.assertNotIn("tokens", str(unpublished))

        player_maps = self.client.get(
            f"/campaigns/{self.campaign['id']}/maps",
            params={"view": "player"},
            headers=self.player_headers,
        )
        withdrawn_direct = self.client.get(
            f"/maps/{self.saved_map['id']}",
            params={"view": "player"},
            headers=self.player_headers,
        )
        self.assertNotIn(self.saved_map["id"], {item["id"] for item in player_maps.json()})
        self.assertEqual(withdrawn_direct.status_code, 404)

    def test_ticket_is_hashed_single_use_and_access_token_is_not_a_ws_credential(self) -> None:
        ticket = self.issue_ticket(self.player_headers)
        connection = connect(self.db_path)
        try:
            stored = connection.execute(
                "SELECT ticket_hash FROM realtime_tickets ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(stored["ticket_hash"], hash_realtime_ticket(ticket))
        self.assertNotEqual(stored["ticket_hash"], ticket)

        with self.client.websocket_connect(
            "/ws",
            headers={"origin": "http://testserver"},
        ) as websocket:
            websocket.send_json({"type": "authenticate", "ticket": ticket})
            self.assertEqual(websocket.receive_json()["type"], "realtime.ready")

        with self.assertRaises(WebSocketDisconnect) as reused:
            with self.client.websocket_connect(
                "/ws",
                headers={"origin": "http://testserver"},
            ) as websocket:
                websocket.send_json({"type": "authenticate", "ticket": ticket})
                websocket.receive_json()
        self.assertEqual(reused.exception.code, 4401)

        with self.assertRaises(WebSocketDisconnect) as bearer_attempt:
            with self.client.websocket_connect(
                "/ws",
                headers={"origin": "http://testserver"},
            ) as websocket:
                websocket.send_json(
                    {
                        "type": "authenticate",
                        "access_token": self.player["access_token"],
                    }
                )
                websocket.receive_json()
        self.assertEqual(bearer_attempt.exception.code, 4401)

    def test_action_proposal_and_resolution_are_role_filtered_in_realtime(self) -> None:
        kp_cursor = self.latest_visible_cursor(self.kp_headers)
        player_cursor = self.latest_visible_cursor(self.player_headers)
        with self.client.websocket_connect(
            "/ws",
            headers={"origin": "http://testserver"},
        ) as kp_socket:
            self.authenticate_socket(kp_socket, self.kp_headers, kp_cursor)
            with self.client.websocket_connect(
                "/ws",
                headers={"origin": "http://testserver"},
            ) as player_socket:
                self.authenticate_socket(player_socket, self.player_headers, player_cursor)
                action = self.client.post(
                    f"/campaigns/{self.campaign['id']}/actions",
                    headers=self.player_headers,
                    json={
                        "action_text": "I listen at the hall door.",
                        "token_id": self.token["id"],
                        "map_id": self.saved_map["id"],
                        "client_action_id": "realtime-action-0001",
                    },
                ).json()
                submitted_event = kp_socket.receive_json()
                self.assertEqual(submitted_event["type"], "realtime.event")
                self.assertEqual(
                    submitted_event["event"]["event_type"],
                    "player_action.submitted",
                )
                self.assertEqual(submitted_event["event"]["resource_id"], action["id"])
                self.assertNotIn("action_text", submitted_event["event"]["payload"])
                self.assertNotIn("audience", submitted_event["event"])
                self.assertNotIn("id", submitted_event["event"])

                proposal = self.client.post(
                    f"/campaigns/{self.campaign['id']}/proposals",
                    headers=self.kp_headers,
                    json={
                        "player_action_id": action["id"],
                        "player_action": action["action_text"],
                        "public_narration": "You hear footsteps beyond the door.",
                        "kp_notes": "This must never enter the player stream.",
                    },
                ).json()
                reviewed_event = player_socket.receive_json()
                self.assertEqual(
                    reviewed_event["event"]["event_type"],
                    "player_action.reviewed",
                )
                self.assertEqual(reviewed_event["event"]["resource_id"], action["id"])
                self.assertNotIn("kp_notes", str(reviewed_event))
                self.assertNotIn(proposal["id"], str(reviewed_event))

                approved = self.client.post(
                    f"/kp/proposals/{proposal['id']}/approve",
                    headers=self.kp_headers,
                    json={"note": "realtime lifecycle"},
                )
                self.assertEqual(approved.status_code, 200, approved.text)
                resolved_event = player_socket.receive_json()
                self.assertEqual(
                    resolved_event["event"]["event_type"],
                    "player_action.resolved",
                )
                world_event = player_socket.receive_json()
                self.assertEqual(world_event["event"]["event_type"], "world.updated")

    def test_map_move_replays_after_cursor_and_revocation_expires_socket(self) -> None:
        player_cursor = self.latest_visible_cursor(self.player_headers)
        with self.client.websocket_connect(
            "/ws",
            headers={"origin": "http://testserver"},
        ) as player_socket:
            self.authenticate_socket(player_socket, self.player_headers, player_cursor)
            move = self.client.post(
                f"/map-tokens/{self.token['id']}/move",
                headers=self.kp_headers,
                json={"to_location_name": "Hall"},
            )
            self.assertEqual(move.status_code, 200, move.text)
            moved_event = player_socket.receive_json()
            self.assertEqual(moved_event["event"]["event_type"], "map.token_moved")
            replay_cursor = moved_event["event"]["cursor"]

        self.client.post(
            f"/maps/{self.saved_map['id']}/unpublish",
            headers=self.kp_headers,
        ).raise_for_status()
        self.client.post(
            f"/maps/{self.saved_map['id']}/publish",
            headers=self.kp_headers,
            json={
                "expected_revision_id": self.saved_map["revision_id"],
                "expected_selected_asset_id": None,
            },
        ).raise_for_status()

        with self.client.websocket_connect(
            "/ws",
            headers={"origin": "http://testserver"},
        ) as replay_socket:
            self.authenticate_socket(replay_socket, self.player_headers, replay_cursor)
            self.assertEqual(
                replay_socket.receive_json()["event"]["event_type"],
                "map.unpublished",
            )
            self.assertEqual(
                replay_socket.receive_json()["event"]["event_type"],
                "map.published",
            )

            revoked = self.client.post(
                f"/sessions/{self.session['session']['id']}/members/"
                f"{self.player['member']['id']}/revoke",
                headers=self.kp_headers,
            )
            self.assertEqual(revoked.status_code, 200, revoked.text)
            expired = replay_socket.receive_json()
            self.assertEqual(expired["type"], "realtime.auth_expired")
            with self.assertRaises(WebSocketDisconnect) as disconnected:
                replay_socket.receive_json()
            self.assertEqual(disconnected.exception.code, 4403)


if __name__ == "__main__":
    unittest.main()

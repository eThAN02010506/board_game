import tempfile
import unittest
from pathlib import Path

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings


EXPECTED_HTTP_ROUTES = {
    ("GET", "/health"),
    ("GET", "/capabilities"),
    ("POST", "/player-profiles"),
    ("GET", "/player-profile"),
    ("GET", "/investigator-skills/catalog"),
    ("POST", "/investigator-imports/preview"),
    ("POST", "/investigators"),
    ("GET", "/investigators"),
    ("GET", "/investigators/{investigator_id}"),
    ("GET", "/investigators/{investigator_id}/revisions"),
    ("POST", "/investigators/preview"),
    ("POST", "/investigators/{investigator_id}/revisions"),
    ("POST", "/campaigns/{campaign_id}/investigators/{investigator_id}/submit"),
    ("GET", "/campaigns/{campaign_id}/my-investigators"),
    ("GET", "/campaigns/{campaign_id}/investigator-submissions"),
    ("POST", "/campaigns/{campaign_id}/investigators/{investigator_id}/review"),
    ("GET", "/campaigns/{campaign_id}/investigators/public"),
    ("PATCH", "/campaigns/{campaign_id}/investigators/{investigator_id}/state"),
    ("POST", "/rulebooks/sources"),
    ("GET", "/rulebooks/sources"),
    ("GET", "/rulebooks/sources/{source_id}"),
    ("POST", "/rulebooks/sources/{source_id}/index"),
    ("POST", "/rulebooks/sources/{source_id}/extract-rules"),
    ("POST", "/rules/query"),
    ("POST", "/rules/execute"),
    ("POST", "/realtime/tickets"),
    ("GET", "/campaigns"),
    ("POST", "/campaigns"),
    ("POST", "/campaigns/{campaign_id}/sessions"),
    ("POST", "/sessions/join"),
    ("GET", "/auth/me"),
    ("GET", "/sessions/{session_id}"),
    ("GET", "/sessions/{session_id}/members"),
    ("POST", "/sessions/{session_id}/members/{member_id}/revoke"),
    ("POST", "/sessions/{session_id}/members/{member_id}/assign-pc"),
    ("POST", "/sessions/{session_id}/members/{member_id}/assign-investigator"),
    ("POST", "/sessions/{session_id}/rotate-join-code"),
    ("POST", "/sessions/{session_id}/close"),
    ("POST", "/campaigns/{campaign_id}/pcs"),
    ("GET", "/campaigns/{campaign_id}/pcs"),
    ("POST", "/campaigns/{campaign_id}/events"),
    ("POST", "/campaigns/{campaign_id}/memories"),
    ("GET", "/campaigns/{campaign_id}/modules"),
    ("POST", "/campaigns/{campaign_id}/modules"),
    ("GET", "/modules/{module_id}/chunks"),
    ("GET", "/campaigns/{campaign_id}/memory/search"),
    ("GET", "/campaigns/{campaign_id}/maps"),
    ("POST", "/campaigns/{campaign_id}/maps/generate"),
    ("GET", "/maps/{map_id}"),
    ("POST", "/maps/{map_id}/publish"),
    ("POST", "/maps/{map_id}/unpublish"),
    ("POST", "/maps/{map_id}/tokens"),
    ("POST", "/map-tokens/{token_id}/move"),
    ("GET", "/map-tokens/{token_id}/moves"),
    ("POST", "/npcs"),
    ("POST", "/campaigns/{campaign_id}/npcs"),
    ("POST", "/campaigns/{campaign_id}/npcs/{npc_id}"),
    ("GET", "/campaigns/{campaign_id}/npc-candidates"),
    ("POST", "/campaigns/{campaign_id}/actions"),
    ("GET", "/campaigns/{campaign_id}/actions"),
    ("GET", "/player-actions/{action_id}"),
    ("GET", "/campaigns/{campaign_id}/proposals"),
    ("POST", "/campaigns/{campaign_id}/proposals"),
    ("GET", "/kp/proposals/{proposal_id}"),
    ("GET", "/kp/proposals/{proposal_id}/context"),
    ("POST", "/kp/proposals/{proposal_id}/approve"),
    ("POST", "/kp/proposals/{proposal_id}/reject"),
    ("POST", "/kp/turn"),
}


class ApiContractTests(unittest.TestCase):
    def test_refactor_preserves_public_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(Settings(db_path=Path(tmpdir) / "contract.sqlite3"))

        actual_routes = [
            (method, route.path)
            for route in app.routes
            for method in (getattr(route, "methods", None) or set())
            if route.path not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
            and method not in {"HEAD", "OPTIONS"}
        ]
        actual = set(actual_routes)
        self.assertEqual(EXPECTED_HTTP_ROUTES, actual)
        self.assertEqual(len(EXPECTED_HTTP_ROUTES), len(actual_routes), "duplicate HTTP route")
        self.assertEqual(1, sum(route.path == "/ws" for route in app.routes))


if __name__ == "__main__":
    unittest.main()

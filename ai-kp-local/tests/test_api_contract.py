import tempfile
import unittest
from pathlib import Path

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings

EXPECTED_HTTP_ROUTES = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/rulesets"),
    ("GET", "/capabilities"),
    ("GET", "/debug/diagnostics"),
    ("GET", "/debug/requests"),
    ("GET", "/debug/database/tables/{table_name}"),
    ("GET", "/debug/database/check"),
    ("GET", "/debug/model/probe"),
    ("GET", "/debug/logs"),
    ("POST", "/debug/xlsx/preview"),
    ("POST", "/player-profiles"),
    ("GET", "/player-profile"),
    ("GET", "/investigator-skills/catalog"),
    ("POST", "/investigator-skills/recommend"),
    ("GET", "/model-settings"),
    ("PUT", "/model-settings"),
    ("POST", "/model-settings/discover"),
    ("GET", "/image-model-settings"),
    ("PUT", "/image-model-settings"),
    ("POST", "/image-model-settings/discover"),
    ("GET", "/model-runtime"),
    ("POST", "/model-runtime/start"),
    ("POST", "/model-runtime/stop"),
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
    ("POST", "/campaigns/{campaign_id}/sessions/recover-kp"),
    ("POST", "/sessions/join"),
    ("GET", "/auth/me"),
    ("GET", "/sessions/{session_id}"),
    ("GET", "/sessions/{session_id}/members"),
    ("POST", "/sessions/{session_id}/members/{member_id}/revoke"),
    ("POST", "/sessions/{session_id}/members/{member_id}/assign-pc"),
    ("POST", "/sessions/{session_id}/members/{member_id}/assign-investigator"),
    ("POST", "/sessions/{session_id}/rotate-join-code"),
    ("POST", "/sessions/{session_id}/close"),
    ("POST", "/sessions/{session_id}/seats"),
    ("GET", "/sessions/{session_id}/seats"),
    ("POST", "/sessions/{session_id}/seats/{seat_id}/reissue"),
    ("POST", "/sessions/{session_id}/seats/{seat_id}/revoke"),
    ("PATCH", "/sessions/{session_id}/seats/{seat_id}/pc"),
    ("POST", "/session-seats/claim"),
    ("GET", "/player-profile/session-seats"),
    ("POST", "/session-seats/{seat_id}/recover"),
    ("POST", "/campaigns/{campaign_id}/checks"),
    ("GET", "/campaigns/{campaign_id}/checks"),
    ("GET", "/checks/{check_id}"),
    ("POST", "/checks/{check_id}/resolve"),
    ("POST", "/checks/{check_id}/replay"),
    ("POST", "/checks/{check_id}/override"),
    ("POST", "/checks/{check_id}/cancel"),
    ("POST", "/checks/{check_id}/push"),
    ("POST", "/checks/{check_id}/consequence-proposal"),
    ("POST", "/campaigns/{campaign_id}/facts"),
    ("GET", "/campaigns/{campaign_id}/facts"),
    ("GET", "/campaigns/{campaign_id}/facts/{fact_key}"),
    ("POST", "/campaigns/{campaign_id}/facts/{fact_key}/retcon"),
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
    ("GET", "/maps/{map_id}/image-prompt"),
    ("POST", "/maps/{map_id}/image-assets/generate"),
    ("POST", "/maps/{map_id}/assets/{asset_id}/select"),
    ("GET", "/map-assets/{asset_id}/content"),
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
            (method.upper(), path)
            for path, path_item in app.openapi()["paths"].items()
            for method in path_item
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        ]
        actual_routes.extend(
            (method, route.path)
            for router in app.state.domain_routers
            for route in router.routes
            if route.__class__.__name__ == "APIRoute"
            and not route.include_in_schema
            for method in route.methods
            if method not in {"HEAD", "OPTIONS"}
        )
        actual = set(actual_routes)
        self.assertEqual(EXPECTED_HTTP_ROUTES, actual)
        self.assertEqual(len(EXPECTED_HTTP_ROUTES), len(actual_routes), "duplicate HTTP route")
        websocket_paths = {
            route.path
            for router in app.state.domain_routers
            for route in router.routes
            if route.__class__.__name__ == "APIWebSocketRoute"
        }
        self.assertEqual({"/ws", "/debug/ws"}, websocket_paths)


if __name__ == "__main__":
    unittest.main()

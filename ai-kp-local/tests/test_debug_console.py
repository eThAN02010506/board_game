from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.infrastructure.database.migrations import LATEST_SCHEMA_VERSION


def _app(tmp_path: Path):
    return create_app(
        Settings(
            db_path=tmp_path / "debug.sqlite3",
            local_admin_enabled=False,
            admin_token="debug-admin",
            llm_api_key="must-not-leak",
        )
    )


def test_debug_dashboard_and_diagnostics_are_admin_only(tmp_path: Path) -> None:
    app = _app(tmp_path)
    headers = {"X-AI-KP-Admin-Token": "debug-admin"}

    with TestClient(app) as client:
        denied = client.get("/")
        denied_runtime_identity = client.get("/debug/runtime-identity")
        dashboard = client.get("/", headers=headers)
        diagnostics = client.get("/debug/diagnostics", headers=headers)
        runtime_identity = client.get("/debug/runtime-identity", headers=headers)

    assert denied.status_code == 403
    assert denied_runtime_identity.status_code == 403
    assert dashboard.status_code == 200
    assert "AI KP Debug Console" in dashboard.text
    assert dashboard.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in dashboard.headers["content-security-policy"]

    assert diagnostics.status_code == 200
    assert runtime_identity.status_code == 200
    assert runtime_identity.json()["status"] == "ready"
    assert runtime_identity.json()["os_pid"] > 0
    assert runtime_identity.json()["process_instance_id"].startswith("process_")
    assert runtime_identity.json()["persistent_store_id"].startswith("store_")
    payload = diagnostics.json()
    assert payload["service"]["status"] == "ok"
    assert payload["database"]["latest_supported_schema"] == LATEST_SCHEMA_VERSION
    assert payload["settings"]["llm_api_key_configured"] is True
    assert "must-not-leak" not in diagnostics.text
    assert any(route["path"] == "/debug/diagnostics" for route in payload["routes"])


def test_runtime_identity_changes_process_instance_but_preserves_store(
    tmp_path: Path,
) -> None:
    headers = {"X-AI-KP-Admin-Token": "debug-admin"}
    with TestClient(_app(tmp_path)) as first_client:
        first = first_client.get("/debug/runtime-identity", headers=headers).json()
    with TestClient(_app(tmp_path)) as restarted_client:
        restarted = restarted_client.get(
            "/debug/runtime-identity", headers=headers
        ).json()

    assert first["process_instance_id"] != restarted["process_instance_id"]
    assert first["persistent_store_id"] == restarted["persistent_store_id"]
    assert first["schema_version"] == LATEST_SCHEMA_VERSION
    assert restarted["schema_version"] == LATEST_SCHEMA_VERSION

    other_path = tmp_path / "other"
    other_path.mkdir()
    with TestClient(_app(other_path)) as other_client:
        other = other_client.get("/debug/runtime-identity", headers=headers).json()
    assert other["persistent_store_id"] != first["persistent_store_id"]


def test_debug_database_preview_redacts_credentials(tmp_path: Path) -> None:
    app = _app(tmp_path)
    headers = {"X-AI-KP-Admin-Token": "debug-admin"}

    with TestClient(app) as client:
        client.post(
            "/campaigns",
            headers=headers,
            json={"title": "Debug campaign"},
        )
        table = client.get(
            "/debug/database/tables/model_configuration",
            headers=headers,
        )
        integrity = client.get("/debug/database/check", headers=headers)
        requests = client.get("/debug/requests", headers=headers)

    assert table.status_code == 200
    assert table.json()["table"] == "model_configuration"
    assert integrity.status_code == 200
    assert integrity.json()["ok"] is True
    assert requests.status_code == 200
    assert requests.json()["summary"]["total_requests"] >= 3


def test_debug_table_name_is_allowlisted(tmp_path: Path) -> None:
    app = _app(tmp_path)
    headers = {"X-AI-KP-Admin-Token": "debug-admin"}

    with TestClient(app) as client:
        response = client.get(
            "/debug/database/tables/not_a_real_table",
            headers=headers,
        )

    assert response.status_code == 404

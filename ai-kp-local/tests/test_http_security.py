from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings


def test_local_mode_adds_headers_without_enabling_rate_limit(tmp_path: Path) -> None:
    app = create_app(Settings(db_path=tmp_path / "local.sqlite3"))
    with TestClient(app) as client:
        responses = [client.get("/health") for _ in range(3)]
        docs = client.get("/docs")

    assert all(response.status_code == 200 for response in responses)
    assert responses[0].headers["x-content-type-options"] == "nosniff"
    assert responses[0].headers["x-frame-options"] == "DENY"
    assert len(responses[0].headers["x-request-id"]) == 32
    assert "content-security-policy" not in docs.headers


def test_lan_mode_requires_admin_token() -> None:
    with pytest.raises(ValidationError, match="ADMIN_TOKEN"):
        Settings(deployment_mode="lan")


def test_lan_mode_enforces_host_and_sensitive_operation_rate_limit(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            db_path=tmp_path / "lan.sqlite3",
            deployment_mode="lan",
            admin_token="lan-admin",
            trusted_hosts="table.local,testserver",
            sensitive_rate_limit_requests=2,
            sensitive_rate_limit_window_seconds=60,
        )
    )
    with TestClient(app, base_url="http://table.local") as client:
        first = client.post(
            "/sessions/join",
            json={"join_code": "A" * 12, "display_name": "Ada"},
        )
        second = client.post(
            "/sessions/join",
            json={"join_code": "B" * 12, "display_name": "Ada"},
        )
        limited = client.post(
            "/sessions/join",
            json={"join_code": "C" * 12, "display_name": "Ada"},
        )
    with TestClient(app, base_url="http://blocked.local") as blocked_client:
        blocked = blocked_client.get("/health")

    assert first.status_code == 404
    assert second.status_code == 404
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limit_exceeded"
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.headers["x-content-type-options"] == "nosniff"
    assert limited.headers["x-request-id"]
    assert blocked.status_code == 400
    assert blocked.headers["x-frame-options"] == "DENY"


def test_request_id_accepts_only_bounded_safe_values(tmp_path: Path) -> None:
    app = create_app(Settings(db_path=tmp_path / "ids.sqlite3"))
    with TestClient(app) as client:
        accepted = client.get("/health", headers={"X-Request-ID": "request-1234"})
        rejected = client.get("/health", headers={"X-Request-ID": "<script>"})

    assert accepted.headers["x-request-id"] == "request-1234"
    assert rejected.headers["x-request-id"] != "<script>"

import asyncio
from pathlib import Path

import httpx
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


def test_lan_mode_never_grants_loopback_admin_without_token(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "lan-loopback.sqlite3",
        deployment_mode="lan",
        admin_token="lan-admin",
        trusted_hosts="table.local",
    )
    app = create_app(settings)

    async def request_as_loopback() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 43120))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://table.local",
        ) as client:
            denied = await client.post("/campaigns", json={"title": "Denied"})
            allowed = await client.post(
                "/campaigns",
                headers={"X-AI-KP-Admin-Token": "lan-admin"},
                json={"title": "Allowed"},
            )
        return denied, allowed

    denied, allowed = asyncio.run(request_as_loopback())

    assert settings.local_admin_enabled is False
    assert denied.status_code == 403
    assert allowed.status_code == 200


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


def test_json_body_limit_rejects_declared_and_chunked_payloads(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            db_path=tmp_path / "json-limit.sqlite3",
            json_body_max_bytes=64 * 1024,
        )
    )
    oversized = b'{"display_name":"' + (b"x" * (70 * 1024)) + b'"}'

    async def request_bodies() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            declared = await client.post(
                "/player-profiles",
                headers={"Content-Type": "application/json"},
                content=oversized,
            )

            async def chunks():
                yield oversized[: 32 * 1024]
                yield oversized[32 * 1024 :]

            chunked = await client.post(
                "/player-profiles",
                headers={"Content-Type": "application/json"},
                content=chunks(),
            )
            healthy = await client.get("/health")
        return declared, chunked, healthy

    declared, chunked, healthy = asyncio.run(request_bodies())

    assert declared.status_code == 413
    assert declared.json()["code"] == "request_body_too_large"
    assert chunked.status_code == 413
    assert chunked.json()["code"] == "request_body_too_large"
    assert healthy.status_code == 200


def test_lan_rate_limit_covers_anonymous_player_profile_creation(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            db_path=tmp_path / "profile-rate-limit.sqlite3",
            deployment_mode="lan",
            admin_token="lan-admin",
            trusted_hosts="table.local,testserver",
            sensitive_rate_limit_requests=2,
            sensitive_rate_limit_window_seconds=60,
        )
    )
    with TestClient(app, base_url="http://table.local") as client:
        first = client.post("/player-profiles", json={"display_name": "玩家一"})
        second = client.post("/player-profiles", json={"display_name": "玩家二"})
        limited = client.post("/player-profiles", json={"display_name": "玩家三"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limit_exceeded"

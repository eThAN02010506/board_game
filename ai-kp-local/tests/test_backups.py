import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.backups import BackupService, BackupVerificationError
from ai_kp.infrastructure.database.schema import connect


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "data" / "ai_kp.sqlite3",
        map_asset_root=tmp_path / "data" / "map-assets",
        module_asset_root=tmp_path / "data" / "module-assets",
        rulebook_index_root=tmp_path / "data" / "rag" / "rulesets",
        backup_root=tmp_path / "data" / "backups",
        local_admin_enabled=False,
        admin_token="backup-admin",
    )


def _service(settings: Settings) -> BackupService:
    return BackupService(
        db_path=settings.db_path,
        map_asset_root=settings.map_asset_root,
        module_asset_root=settings.module_asset_root,
        rulebook_index_root=settings.rulebook_index_root,
        backup_root=settings.backup_root,
        app_version="test",
    )


def test_online_backup_round_trip_restores_database_and_file_stores(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    headers = {"X-AI-KP-Admin-Token": "backup-admin"}
    settings.map_asset_root.mkdir(parents=True)
    settings.module_asset_root.mkdir(parents=True)
    settings.rulebook_index_root.mkdir(parents=True)
    (settings.map_asset_root / "sha256" / "ab").mkdir(parents=True)
    (settings.map_asset_root / "sha256" / "ab" / "map.png").write_bytes(b"map-image")
    (settings.module_asset_root / "sources").mkdir(parents=True)
    (settings.module_asset_root / "sources" / "module.pdf").write_bytes(b"module-source")
    (settings.rulebook_index_root / "rules.json").write_text(
        '{"rule": "sanity"}',
        encoding="utf-8",
    )

    with TestClient(app) as client:
        campaign = client.post(
            "/campaigns",
            headers=headers,
            json={"title": "Backup campaign"},
        ).json()
        created = client.post("/admin/backups", headers=headers)
        listed = client.get("/admin/backups", headers=headers)
        verified = client.post(
            f"/admin/backups/{created.json()['filename']}/verify",
            headers=headers,
        )
        downloaded = client.get(
            f"/admin/backups/{created.json()['filename']}/content",
            headers=headers,
        )

    assert created.status_code == 200
    assert listed.json()[0]["filename"] == created.json()["filename"]
    assert verified.json()["ok"] is True
    assert downloaded.headers["content-type"] == "application/zip"
    assert campaign["title"] == "Backup campaign"

    settings.db_path.unlink()
    (settings.map_asset_root / "sha256" / "ab" / "map.png").write_bytes(b"corrupt")
    (settings.module_asset_root / "sources" / "module.pdf").unlink()
    (settings.rulebook_index_root / "rules.json").unlink()
    archive = settings.backup_root / created.json()["filename"]
    manifest = _service(settings).restore_offline(archive)

    assert manifest.schema_version >= 1
    connection = connect(settings.db_path)
    try:
        row = connection.execute(
            "SELECT title FROM campaigns WHERE id = ?",
            (campaign["id"],),
        ).fetchone()
    finally:
        connection.close()
    assert row["title"] == "Backup campaign"
    assert (settings.map_asset_root / "sha256" / "ab" / "map.png").read_bytes() == b"map-image"
    assert (
        settings.module_asset_root / "sources" / "module.pdf"
    ).read_bytes() == b"module-source"
    assert json.loads(
        (settings.rulebook_index_root / "rules.json").read_text(encoding="utf-8")
    ) == {"rule": "sanity"}


def test_backup_endpoints_require_local_administrator(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/admin/backups").status_code == 403
        assert client.post("/admin/backups").status_code == 403


def test_backup_verification_rejects_hash_tampering_and_path_traversal(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    create_app(settings)
    service = _service(settings)
    archive, _manifest = service.create()

    with ZipFile(archive) as source:
        manifest = json.loads(source.read("manifest.json"))
        database = source.read("database/ai_kp.sqlite3")
    tampered = settings.backup_root / "ai-kp-backup-tampered.zip"
    with ZipFile(tampered, "w", ZIP_DEFLATED) as target:
        target.writestr("database/ai_kp.sqlite3", database + b"tampered")
        target.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(BackupVerificationError, match="size mismatch"):
        service.verify(tampered)

    unsafe = settings.backup_root / "ai-kp-backup-unsafe.zip"
    with ZipFile(unsafe, "w", ZIP_DEFLATED) as target:
        target.writestr("../outside.txt", "unsafe")
        target.writestr("manifest.json", "{}")
    with pytest.raises(BackupVerificationError, match="Unsafe backup member"):
        service.verify(unsafe)

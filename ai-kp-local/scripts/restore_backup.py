"""Restore a verified AI KP backup while the API service is stopped."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.backups import BackupLimits, BackupService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore a verified AI KP backup. Stop the API server first."
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--confirm",
        required=True,
        help="Must be exactly RESTORE; existing local data will be replaced.",
    )
    args = parser.parse_args()
    if args.confirm != "RESTORE":
        parser.error("--confirm must be exactly RESTORE")

    settings = Settings()
    service = BackupService(
        db_path=settings.db_path,
        map_asset_root=settings.map_asset_root,
        rulebook_index_root=settings.rulebook_index_root,
        backup_root=settings.backup_root,
        app_version="0.1.0",
        limits=BackupLimits(
            max_files=settings.backup_max_files,
            max_uncompressed_bytes=settings.backup_max_uncompressed_bytes,
        ),
    )
    manifest = service.restore_offline(args.archive.resolve())
    print(
        f"Restored schema {manifest.schema_version} backup created at "
        f"{manifest.created_at}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

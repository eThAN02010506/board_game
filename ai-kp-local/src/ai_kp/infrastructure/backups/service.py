"""Create, verify, and restore self-contained local backup archives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from ai_kp.infrastructure.database.migrations import LATEST_SCHEMA_VERSION
from ai_kp.infrastructure.database.schema import connect

BACKUP_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATABASE_ARCHIVE_PATH = "database/ai_kp.sqlite3"


class BackupVerificationError(ValueError):
    """Raised when an archive is malformed, unsafe, incomplete, or corrupted."""


class BackupNotFoundError(FileNotFoundError):
    """Raised when a requested backup name is not present in the controlled root."""


@dataclass(frozen=True)
class BackupLimits:
    max_files: int = 20_000
    max_uncompressed_bytes: int = 20 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class BackupFile:
    archive_path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    app_version: str
    schema_version: int
    created_at: str
    files: tuple[BackupFile, ...]

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "files": [asdict(item) for item in self.files],
        }

    @classmethod
    def from_dict(cls, payload: object) -> BackupManifest:
        if not isinstance(payload, dict):
            raise BackupVerificationError("Backup manifest must be a JSON object")
        try:
            files_payload = payload["files"]
            if not isinstance(files_payload, list):
                raise TypeError
            files = tuple(
                BackupFile(
                    archive_path=str(item["archive_path"]),
                    size=int(item["size"]),
                    sha256=str(item["sha256"]),
                )
                for item in files_payload
                if isinstance(item, dict)
            )
            if len(files) != len(files_payload):
                raise TypeError
            return cls(
                format_version=int(payload["format_version"]),
                app_version=str(payload["app_version"]),
                schema_version=int(payload["schema_version"]),
                created_at=str(payload["created_at"]),
                files=files,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupVerificationError("Backup manifest has an invalid shape") from exc


class BackupService:
    """Own backup paths and enforce archive integrity at every boundary."""

    def __init__(
        self,
        *,
        db_path: Path,
        map_asset_root: Path,
        module_asset_root: Path,
        rulebook_index_root: Path,
        backup_root: Path,
        app_version: str,
        limits: BackupLimits | None = None,
    ):
        self.db_path = db_path.resolve()
        self.map_asset_root = map_asset_root.resolve()
        self.module_asset_root = module_asset_root.resolve()
        self.rulebook_index_root = rulebook_index_root.resolve()
        self.backup_root = backup_root.resolve()
        self.app_version = app_version
        self.limits = limits or BackupLimits()

    def create(self) -> tuple[Path, BackupManifest]:
        """Create an online SQLite snapshot and package it with durable file stores."""

        self.backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        final_path = self.backup_root / f"ai-kp-backup-{timestamp}.zip"
        temporary_path = final_path.with_suffix(".zip.partial")

        with tempfile.TemporaryDirectory(dir=self.backup_root) as temp_dir:
            snapshot_path = Path(temp_dir) / "ai_kp.sqlite3"
            self._snapshot_database(snapshot_path)
            files = [(snapshot_path, DATABASE_ARCHIVE_PATH)]
            files.extend(self._tree_files(self.map_asset_root, "map-assets"))
            files.extend(self._tree_files(self.module_asset_root, "module-assets"))
            files.extend(self._tree_files(self.rulebook_index_root, "rulebook-index"))
            if len(files) > self.limits.max_files:
                raise BackupVerificationError("Backup contains too many files")

            manifest_files = tuple(
                BackupFile(
                    archive_path=archive_path,
                    size=source.stat().st_size,
                    sha256=_sha256_file(source),
                )
                for source, archive_path in files
            )
            if sum(item.size for item in manifest_files) > self.limits.max_uncompressed_bytes:
                raise BackupVerificationError("Backup exceeds the configured size limit")
            manifest = BackupManifest(
                format_version=BACKUP_FORMAT_VERSION,
                app_version=self.app_version,
                schema_version=LATEST_SCHEMA_VERSION,
                created_at=datetime.now(UTC).isoformat(),
                files=manifest_files,
            )
            try:
                with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
                    for source, archive_path in files:
                        archive.write(source, archive_path)
                    archive.writestr(
                        MANIFEST_NAME,
                        json.dumps(
                            manifest.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                            indent=2,
                        ),
                    )
                os.replace(temporary_path, final_path)
                os.chmod(final_path, 0o600)
            finally:
                temporary_path.unlink(missing_ok=True)
        return final_path, manifest

    def list(self) -> list[dict]:
        if not self.backup_root.exists():
            return []
        results: list[dict] = []
        for path in sorted(self.backup_root.glob("ai-kp-backup-*.zip"), reverse=True):
            if not path.is_file():
                continue
            results.append(
                {
                    "filename": path.name,
                    "size": path.stat().st_size,
                    "created_at": datetime.fromtimestamp(
                        path.stat().st_mtime,
                        tz=UTC,
                    ).isoformat(),
                }
            )
        return results

    def resolve(self, filename: str) -> Path:
        if not filename or Path(filename).name != filename or not filename.endswith(".zip"):
            raise BackupVerificationError("Invalid backup filename")
        path = (self.backup_root / filename).resolve()
        if path.parent != self.backup_root or not path.is_file():
            raise BackupNotFoundError(f"Backup not found: {filename}")
        return path

    def verify(self, archive_path: Path) -> BackupManifest:
        """Verify allowlisted paths, limits, manifest hashes, and SQLite integrity."""

        try:
            with ZipFile(archive_path) as archive:
                infos = archive.infolist()
                self._validate_members(infos)
                names = {item.filename for item in infos}
                if MANIFEST_NAME not in names:
                    raise BackupVerificationError("Backup manifest is missing")
                if archive.getinfo(MANIFEST_NAME).file_size > 1024 * 1024:
                    raise BackupVerificationError("Backup manifest is too large")
                try:
                    manifest = BackupManifest.from_dict(
                        json.loads(archive.read(MANIFEST_NAME))
                    )
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise BackupVerificationError("Backup manifest is not valid JSON") from exc
                self._validate_manifest(manifest, names)
                for item in manifest.files:
                    info = archive.getinfo(item.archive_path)
                    if info.file_size != item.size:
                        raise BackupVerificationError(
                            f"Backup file size mismatch: {item.archive_path}"
                        )
                    if _sha256_archive_member(archive, info) != item.sha256:
                        raise BackupVerificationError(
                            f"Backup file hash mismatch: {item.archive_path}"
                        )
                with tempfile.TemporaryDirectory() as temp_dir:
                    database_path = Path(temp_dir) / "verify.sqlite3"
                    _extract_archive_member(
                        archive,
                        archive.getinfo(DATABASE_ARCHIVE_PATH),
                        database_path,
                    )
                    self._verify_database(database_path, manifest.schema_version)
                return manifest
        except BadZipFile as exc:
            raise BackupVerificationError("Backup is not a valid ZIP archive") from exc

    def restore_offline(self, archive_path: Path) -> BackupManifest:
        """Restore a verified archive. The caller must ensure the API process is stopped."""

        manifest = self.verify(archive_path)
        targets = {
            "map-assets": self.map_asset_root,
            "module-assets": self.module_asset_root,
            "rulebook-index": self.rulebook_index_root,
        }
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.db_path.parent) as temp_dir:
            staging = Path(temp_dir)
            with ZipFile(archive_path) as archive:
                for item in manifest.files:
                    destination = staging / PurePosixPath(item.archive_path)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    _extract_archive_member(
                        archive,
                        archive.getinfo(item.archive_path),
                        destination,
                    )

            staged_database = staging / DATABASE_ARCHIVE_PATH
            self._verify_database(staged_database, manifest.schema_version)
            staged_database.replace(self.db_path)
            Path(f"{self.db_path}-wal").unlink(missing_ok=True)
            Path(f"{self.db_path}-shm").unlink(missing_ok=True)
            for archive_prefix, target in targets.items():
                staged_tree = staging / archive_prefix
                replacement = target.with_name(f".{target.name}.restore")
                if replacement.exists():
                    shutil.rmtree(replacement)
                if staged_tree.exists():
                    shutil.copytree(staged_tree, replacement)
                else:
                    replacement.mkdir(parents=True)
                if target.exists():
                    previous = target.with_name(f".{target.name}.previous")
                    if previous.exists():
                        shutil.rmtree(previous)
                    target.replace(previous)
                    replacement.replace(target)
                    shutil.rmtree(previous)
                else:
                    replacement.replace(target)
        return manifest

    def _snapshot_database(self, destination: Path) -> None:
        source = connect(self.db_path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        self._verify_database(destination, LATEST_SCHEMA_VERSION)

    @staticmethod
    def _tree_files(root: Path, archive_prefix: str) -> list[tuple[Path, str]]:
        if not root.exists():
            return []
        files: list[tuple[Path, str]] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise BackupVerificationError(f"Backup source contains a symlink: {path}")
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                files.append((path, f"{archive_prefix}/{relative}"))
        return files

    def _validate_members(self, infos: list[ZipInfo]) -> None:
        if len(infos) > self.limits.max_files + 1:
            raise BackupVerificationError("Backup contains too many files")
        total = 0
        seen: set[str] = set()
        for info in infos:
            path = PurePosixPath(info.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in info.filename
                or info.filename in seen
                or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK
            ):
                raise BackupVerificationError(f"Unsafe backup member: {info.filename}")
            if info.is_dir():
                continue
            seen.add(info.filename)
            total += info.file_size
            if total > self.limits.max_uncompressed_bytes:
                raise BackupVerificationError("Backup exceeds the configured size limit")

    @staticmethod
    def _validate_manifest(manifest: BackupManifest, archive_names: set[str]) -> None:
        if manifest.format_version != BACKUP_FORMAT_VERSION:
            raise BackupVerificationError(
                f"Unsupported backup format: {manifest.format_version}"
            )
        if manifest.schema_version > LATEST_SCHEMA_VERSION:
            raise BackupVerificationError("Backup database is newer than this application")
        paths = [item.archive_path for item in manifest.files]
        if len(paths) != len(set(paths)):
            raise BackupVerificationError("Backup manifest contains duplicate files")
        if DATABASE_ARCHIVE_PATH not in paths:
            raise BackupVerificationError("Backup database is missing")
        if set(paths) | {MANIFEST_NAME} != archive_names:
            raise BackupVerificationError("Backup files do not match the manifest")
        if any(item.size < 0 or len(item.sha256) != 64 for item in manifest.files):
            raise BackupVerificationError("Backup manifest contains invalid file metadata")

    @staticmethod
    def _verify_database(path: Path, expected_schema_version: int) -> None:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise BackupVerificationError("Backup database integrity check failed")
            row = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            version = int(row[0] or 0) if row else 0
            if version != expected_schema_version:
                raise BackupVerificationError(
                    "Backup database schema does not match its manifest"
                )
        except sqlite3.DatabaseError as exc:
            raise BackupVerificationError("Backup database is invalid") from exc
        finally:
            connection.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_archive_member(archive: ZipFile, info: ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive_member(archive: ZipFile, info: ZipInfo, destination: Path) -> None:
    with archive.open(info) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)

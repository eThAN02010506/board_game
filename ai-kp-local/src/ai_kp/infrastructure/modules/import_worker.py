"""Local durable worker for private PDF/DOC/DOCX module imports."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path, PurePosixPath
from threading import Event, RLock, Thread

from ai_kp.infrastructure.database.module_imports import ModuleImportClaimLost
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.modules import ModuleDocumentStorage
from ai_kp.infrastructure.modules.document_sandbox import (
    DocumentParsePolicy,
    extract_module_document_isolated,
)
from ai_kp.platform.modules.documents import (
    PARSER_VERSION,
    detect_document_type,
)


def enqueue_module_import(
    db_path: Path,
    module_asset_root: Path,
    *,
    campaign_id: str,
    title: str,
    source_filename: str,
    data: bytes,
    synchronous: str = "FULL",
) -> dict:
    source_type = detect_document_type(data, source_filename)
    source_hash = hashlib.sha256(data).hexdigest()
    storage = ModuleDocumentStorage(module_asset_root)
    storage_path = storage.store_source(
        data,
        source_hash,
        PurePosixPath(source_filename).suffix,
    )
    connection = connect(db_path, synchronous=synchronous)
    try:
        init_db(connection)
        repo = Repository(connection)
        repo.get_campaign(campaign_id)
        job = repo.create_module_import_job(
            campaign_id=campaign_id,
            title=title.strip() or PurePosixPath(source_filename).stem,
            source_filename=source_filename,
            source_type=source_type,
            source_hash=source_hash,
            source_storage_path=storage_path,
            parser_version=PARSER_VERSION,
        )
        connection.commit()
        return job
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def queue_module_import_retry(
    db_path: Path,
    job_id: str,
    *,
    synchronous: str = "FULL",
) -> dict:
    connection = connect(db_path, synchronous=synchronous)
    try:
        init_db(connection)
        job = Repository(connection).retry_module_import_job(job_id)
        connection.commit()
        return job
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_module_import(
    db_path: Path,
    module_asset_root: Path,
    job_id: str,
    *,
    synchronous: str = "FULL",
    parse_policy: DocumentParsePolicy | None = None,
) -> None:
    connection = connect(db_path, synchronous=synchronous)
    storage = ModuleDocumentStorage(module_asset_root)
    try:
        init_db(connection)
        repo = Repository(connection)
        job = repo.claim_module_import_job(job_id)
        connection.commit()
        if job is None:
            return
        _process_claimed_module_import(
            connection,
            storage,
            job,
            parse_policy=parse_policy or DocumentParsePolicy(),
        )
    finally:
        connection.close()


class ModuleImportWorker:
    """One local thread that drains the SQLite-backed module import queue."""

    def __init__(
        self,
        db_path: Path,
        module_asset_root: Path,
        *,
        synchronous: str = "FULL",
        poll_interval_seconds: float = 0.25,
        parse_policy: DocumentParsePolicy | None = None,
    ):
        self.db_path = db_path
        self.module_asset_root = module_asset_root
        self.synchronous = synchronous
        self.poll_interval_seconds = poll_interval_seconds
        self.parse_policy = parse_policy or DocumentParsePolicy()
        self._stop_event = Event()
        self._wake_event = Event()
        self._ready_event = Event()
        self._thread: Thread | None = None
        self._startup_error: BaseException | None = None
        self._last_error: str | None = None
        self._lock = RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._ready_event.clear()
            self._startup_error = None
            self._last_error = None
            self._thread = Thread(
                target=self._run,
                name="ai-kp-module-import-worker",
                daemon=True,
            )
            thread = self._thread
            thread.start()

        if not self._ready_event.wait(timeout=30):
            self.stop()
            raise RuntimeError("Module import worker did not become ready")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise RuntimeError("Module import worker failed to start") from error

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            self._wake_event.set()
        thread.join()
        with self._lock:
            if self._thread is thread:
                self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(
                self.db_path,
                synchronous=self.synchronous,
            )
            init_db(connection)
            repo = Repository(connection)
            repo.recover_interrupted_module_imports()
            connection.commit()
            storage = ModuleDocumentStorage(self.module_asset_root)
            self._ready_event.set()

            while not self._stop_event.is_set():
                try:
                    job = repo.claim_next_module_import_job()
                    connection.commit()
                except sqlite3.Error as exc:
                    connection.rollback()
                    self._last_error = str(exc)
                    self._wait_for_work()
                    continue
                if job is None:
                    self._wait_for_work()
                    continue
                _process_claimed_module_import(
                    connection,
                    storage,
                    job,
                    parse_policy=self.parse_policy,
                )
        except BaseException as exc:  # noqa: BLE001 - propagate startup failure to lifespan
            self._startup_error = exc
            self._last_error = str(exc)
            self._ready_event.set()
        finally:
            if connection is not None:
                connection.close()

    def _wait_for_work(self) -> None:
        self._wake_event.wait(timeout=self.poll_interval_seconds)
        self._wake_event.clear()


def _process_claimed_module_import(
    connection: sqlite3.Connection,
    storage: ModuleDocumentStorage,
    job: dict,
    *,
    parse_policy: DocumentParsePolicy,
) -> None:
    repo = Repository(connection)
    job_id = str(job["id"])
    expected_attempt = int(job["attempt_count"])
    try:
        source_path = storage.resolve(job["source_storage_path"])

        repo.update_module_import_progress(
            job_id,
            expected_attempt=expected_attempt,
            stage="extracting",
        )
        connection.commit()
        extracted = extract_module_document_isolated(
            source_path,
            job["source_filename"],
            title=job["title"],
            expected_hash=job["source_hash"],
            policy=parse_policy,
        )
        if extracted.source_hash != job["source_hash"]:
            raise ValueError("解析结果与源文件哈希不一致")

        total = len(extracted.chunks) + len(extracted.assets)
        repo.update_module_import_progress(
            job_id,
            expected_attempt=expected_attempt,
            stage="storing",
            current=len(extracted.chunks),
            total=total,
        )
        connection.commit()
        stored_assets = []
        for index, asset in enumerate(extracted.assets, start=1):
            suffix = _canonical_asset_suffix(asset.mime_type)
            content_hash, storage_path = storage.store_asset(asset.data, suffix)
            stored_assets.append((asset, content_hash, storage_path))
            repo.update_module_import_progress(
                job_id,
                expected_attempt=expected_attempt,
                stage="storing",
                current=len(extracted.chunks) + index,
                total=total,
            )
            connection.commit()
        repo.complete_module_document_import(
            job_id,
            expected_attempt=expected_attempt,
            chunks=extracted.chunks,
            assets=tuple(stored_assets),
        )
        connection.commit()
    except ModuleImportClaimLost:
        connection.rollback()
    except Exception as exc:  # noqa: BLE001 - persistent job boundary records parser failures
        connection.rollback()
        try:
            Repository(connection).fail_module_import_job(
                job_id,
                str(exc),
                expected_attempt=expected_attempt,
            )
            connection.commit()
        except (KeyError, sqlite3.Error):
            # The campaign/job may have been deliberately deleted while a
            # parser was running; that must not terminate the durable worker.
            connection.rollback()


def _canonical_asset_suffix(mime_type: str) -> str:
    return {
        "image/bmp": ".bmp",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/tiff": ".tiff",
        "image/webp": ".webp",
    }.get(mime_type, ".bin")

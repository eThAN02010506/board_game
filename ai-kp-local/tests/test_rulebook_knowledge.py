import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from typing import ClassVar
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.rulebook_service import RulebookService
from ai_kp.bootstrap.settings import Settings
from ai_kp.core.db import connect, init_db
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.knowledge.minirag import MiniRagOriginalIndex
from ai_kp.rule_authoring.review import approval_allows_execution
from ai_kp.rulebook.engine import RuleExecutionError, execute_rule
from ai_kp.rulebook.models import RuleObject, RuleReviewSubmission
from ai_kp.rulebook.pdf_ingestion import extract_rulebook_pdf
from ai_kp.rulebook.validation import RuleValidator


class _FakePage:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self) -> str:
        return self.text


class _FakeReader:
    is_encrypted = False
    metadata: ClassVar[dict[str, str]] = {"/Title": "测试规则书"}
    strict_values: ClassVar[list[bool]] = []

    def __init__(self, _stream, strict=True):
        self.strict_values.append(strict)
        self.pages = [
            _FakePage("克苏鲁的呼唤 第七版\n6.1 检定\n技能值的一半为困难成功。\n1"),
            _FakePage("第七章 战斗\n单次伤害达到最大生命值一半时造成重伤。\n2"),
        ]


class _TransactionCheckingIndex:
    def __init__(self, repo: Repository, db_path: Path, source_id: str):
        self.repo = repo
        self.db_path = db_path
        self.source_id = source_id

    async def index_chunks(self, chunks: list[dict]) -> dict[str, str]:
        if self.repo.connection.in_transaction:
            raise AssertionError("MiniRAG await started with an open transaction")
        observer = connect(self.db_path)
        try:
            source = observer.execute(
                "SELECT status FROM rule_sources WHERE id = ?",
                (self.source_id,),
            ).fetchone()
            if source is None or source["status"] != "indexing":
                raise AssertionError("indexing claim was not committed before await")
            observer.execute("BEGIN IMMEDIATE")
            observer.rollback()
        finally:
            observer.close()
        return {chunk["id"]: f"mini-{chunk['id']}" for chunk in chunks}

    async def retrieve(self, _query: str, _top_k: int = 8) -> list[str]:
        return []


class _TransactionCheckingLlm:
    def __init__(
        self,
        repo: Repository,
        db_path: Path,
        chunk_id: str,
        response: dict,
    ):
        self.repo = repo
        self.db_path = db_path
        self.chunk_id = chunk_id
        self.response = response

    async def complete(self, _messages, temperature: float = 0.7) -> str:
        if self.repo.connection.in_transaction:
            raise AssertionError("LLM await started with an open transaction")
        observer = connect(self.db_path)
        try:
            chunk = observer.execute(
                "SELECT extraction_status FROM rule_chunks WHERE id = ?",
                (self.chunk_id,),
            ).fetchone()
            if chunk is None or chunk["extraction_status"] != "processing":
                raise AssertionError("processing claim was not committed before await")
            observer.execute("BEGIN IMMEDIATE")
            observer.rollback()
        finally:
            observer.close()
        return json.dumps(self.response, ensure_ascii=False)


class _RecoveringRuleLlm:
    def __init__(
        self,
        db_path: Path,
        chunk_id: str,
        response: dict,
        *,
        fail_after_recovery: bool = False,
    ):
        self.db_path = db_path
        self.chunk_id = chunk_id
        self.response = response
        self.fail_after_recovery = fail_after_recovery
        self.calls = 0

    async def complete(self, _messages, temperature: float = 0.7) -> str:
        self.calls += 1
        recovery_connection = connect(self.db_path)
        try:
            recovery_repo = Repository(recovery_connection)
            current = recovery_repo.get_rule_chunk(self.chunk_id)
            if current["extraction_status"] != "processing":
                raise AssertionError("old rule attempt was not processing")
            if current["attempt_count"] != 1:
                raise AssertionError("unexpected old rule attempt generation")
            if recovery_repo.recover_interrupted_rule_extractions() != 1:
                raise AssertionError("old rule attempt was not recovered")
            if recovery_repo.recover_interrupted_rule_ingestion_runs() != 1:
                raise AssertionError("old ingestion run was not recovered")
            recovery_connection.commit()
            fresh_attempt = recovery_repo.claim_rule_chunk_for_extraction(
                self.chunk_id
            )
            recovery_connection.commit()
            if fresh_attempt != 2:
                raise AssertionError("fresh rule attempt did not advance generation")
        finally:
            recovery_connection.close()
        if self.fail_after_recovery:
            raise RuntimeError("stale rule extraction failed")
        return json.dumps(self.response, ensure_ascii=False)


class _RecoveringIndex:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def index_chunks(self, chunks: list[dict]) -> dict[str, str]:
        recovery_connection = connect(self.db_path)
        try:
            recovery_repo = Repository(recovery_connection)
            if recovery_repo.recover_interrupted_rule_ingestion_runs() != 1:
                raise AssertionError("active index run was not recovered")
            recovery_connection.commit()
        finally:
            recovery_connection.close()
        return {chunk["id"]: f"stale-index-{chunk['id']}" for chunk in chunks}

    async def retrieve(self, _query: str, _top_k: int = 8) -> list[str]:
        return []


class _ReplacingFailingIndex:
    def __init__(self, db_path: Path, source_id: str):
        self.db_path = db_path
        self.source_id = source_id

    async def index_chunks(self, _chunks: list[dict]) -> dict[str, str]:
        replacement_connection = connect(self.db_path)
        try:
            replacement_repo = Repository(replacement_connection)
            if replacement_repo.recover_interrupted_rule_ingestion_runs() != 1:
                raise AssertionError("old index run was not recovered")
            replacement_connection.commit()
            replacement_repo.begin_immediate()
            replacement = replacement_repo.create_ingestion_run(
                self.source_id,
                stage="minirag_index",
            )
            replacement_repo.set_rule_source_status(self.source_id, "indexing")
            transitioned = replacement_repo.transition_ingestion_run(
                replacement["id"],
                expected_status="running",
                status="completed",
            )
            if transitioned is None:
                raise AssertionError("replacement index run did not complete")
            replacement_repo.set_rule_source_status(self.source_id, "ready")
            replacement_connection.commit()
        finally:
            replacement_connection.close()
        raise RuntimeError("old index failed after replacement completed")

    async def retrieve(self, _query: str, _top_k: int = 8) -> list[str]:
        return []


class _BlockingIndex:
    def __init__(self, started: Event, release: Event):
        self.started = started
        self.release = release

    async def index_chunks(self, chunks: list[dict]) -> dict[str, str]:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("blocking index was not released")
        return {chunk["id"]: f"active-index-{chunk['id']}" for chunk in chunks}

    async def retrieve(self, _query: str, _top_k: int = 8) -> list[str]:
        return []


class _StaticIndex:
    async def index_chunks(self, chunks: list[dict]) -> dict[str, str]:
        return {chunk["id"]: f"static-index-{chunk['id']}" for chunk in chunks}

    async def retrieve(self, _query: str, _top_k: int = 8) -> list[str]:
        return []


class _StaticRuleLlm:
    def __init__(self, response: dict):
        self.response = response

    async def complete(self, _messages, temperature: float = 0.7) -> str:
        return json.dumps(self.response, ensure_ascii=False)


def _candidate(chunk_id: str, *, summary: str = "达到阈值时标记重伤") -> dict:
    return {
        "rule_key": "coc7.damage.major_wound",
        "ruleset_id": "coc7-keeper-cn-2002c",
        "title": "重伤判定",
        "rule_type": "damage",
        "summary": summary,
        "audience": "all",
        "tags": ["伤害", "重伤"],
        "execution": {
            "kind": "condition_effects",
            "inputs": ["damage", "max_hp"],
            "branches": [
                {
                    "label": "major-wound",
                    "all": [
                        {
                            "left": {"field": "damage"},
                            "operator": "gte",
                            "right": {"field": "max_hp", "divisor": 2},
                        }
                    ],
                    "effects": [
                        {
                            "target": "major_wound",
                            "operation": "set",
                            "operand": {"value": True},
                        }
                    ],
                }
            ],
            "rows": [],
        },
        "citations": [
            {
                "chunk_id": chunk_id,
                "page": 2,
                "evidence_text": "单次伤害达到最大生命值一半时造成重伤",
            }
        ],
        "confidence": 0.95,
    }


def _single_chunk_rule_repo(
    db_path: Path,
) -> tuple[Repository, dict, dict]:
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    source = repo.create_rule_source(
        ruleset_id="coc7-keeper-cn-2002c",
        title="并发审核规则",
        source_filename="concurrent-rules.pdf",
        source_hash="c" * 64,
        page_count=2,
        metadata={},
    )
    chunk = {
        "id": "rulechunk_concurrent_review",
        "source_id": source["id"],
        "page_start": 2,
        "page_end": 2,
        "order_index": 0,
        "chapter": "伤害",
        "section": "重伤",
        "content_kind": "text",
        "audience": "all",
        "text": "单次伤害达到最大生命值一半时造成重伤。",
        "text_hash": "d" * 64,
    }
    repo.replace_rule_chunks(source["id"], [chunk])
    repo.commit()
    return repo, source, chunk


def _rulebook_service(repo: Repository, index_root: Path) -> RulebookService:
    return RulebookService(
        repo,
        extractor=lambda _data, _filename: None,
        index_factory=lambda ruleset_id, source_id: MiniRagOriginalIndex(
            index_root,
            ruleset_id,
            source_id,
        ),
    )


def _passing_review(*, decision: str = "approved") -> RuleReviewSubmission:
    if decision == "rejected":
        return RuleReviewSubmission.model_validate(
            {"decision": "rejected", "note": "不采用该候选"}
        )
    return RuleReviewSubmission.model_validate(
        {
            "decision": "approved",
            "note": "已核对规则原文",
            "golden_cases": [
                {
                    "name": "达到重伤阈值",
                    "inputs": {"damage": 6, "max_hp": 10},
                    "expected_output": {
                        "damage": 6,
                        "max_hp": 10,
                        "major_wound": True,
                        "_matched_branches": ["major-wound"],
                    },
                }
            ],
        }
    )


@pytest.mark.parametrize("failure_point", ("begin", "store"))
def test_unexpected_rule_persistence_failure_releases_claim_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    repo, source, chunk = _single_chunk_rule_repo(tmp_path / "rule-failure.sqlite3")
    service = _rulebook_service(repo, tmp_path / "rule-index")
    llm = _StaticRuleLlm({"rules": [_candidate(chunk["id"])]})
    if failure_point == "begin":
        original = repo.begin_immediate
        begin_calls = 0

        def fail_once() -> None:
            nonlocal begin_calls
            begin_calls += 1
            # The first call creates the run lease. Inject the first result-
            # persistence BEGIN after the claim has been durably committed.
            if begin_calls == 2:
                raise sqlite3.OperationalError("injected begin failure")
            original()

        monkeypatch.setattr(repo, "begin_immediate", fail_once)
    else:
        original_store = repo.store_rule_object
        failed_once = False

        def fail_store_once(*args, **kwargs):
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise sqlite3.OperationalError("injected rule store failure")
            return original_store(*args, **kwargs)

        monkeypatch.setattr(repo, "store_rule_object", fail_store_once)

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        asyncio.run(
            service.extract_rules(
                source["id"],
                llm,
                model_name="persistence-failure",
            )
        )

    failed = repo.get_rule_chunk(chunk["id"])
    assert failed["extraction_status"] == "failed"
    assert failed["attempt_count"] == 1
    assert repo.list_rule_objects(source["id"]) == []
    assert repo.latest_ingestion_run(source["id"])["status"] == "failed"

    retried = asyncio.run(
        service.extract_rules(
            source["id"],
            llm,
            model_name="persistence-retry",
            retry_failed=True,
        )
    )

    completed = repo.get_rule_chunk(chunk["id"])
    assert retried["accepted_count"] == 1
    assert completed["extraction_status"] == "completed"
    assert completed["attempt_count"] == 2
    assert len(repo.list_rule_objects(source["id"])) == 1
    repo.connection.close()


def test_recovered_index_run_cannot_publish_results_or_return_to_completed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stale-index.sqlite3"
    repo, source, chunk = _single_chunk_rule_repo(db_path)
    index = _RecoveringIndex(db_path)
    service = RulebookService(
        repo,
        extractor=lambda _data, _filename: None,
        index_factory=lambda _ruleset_id, _source_id: index,
    )

    recovered_source = asyncio.run(service.index_source(source["id"]))

    run = repo.latest_ingestion_run(source["id"])
    assert recovered_source["status"] == "failed"
    assert run is not None
    assert run["status"] == "failed"
    assert "interrupted" in run["error_text"].lower()
    assert repo.get_rule_chunk(chunk["id"])["minirag_doc_id"] is None
    repo.connection.close()


def test_stale_index_failure_cannot_overwrite_replacement_ready_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stale-index-failure.sqlite3"
    repo, source, chunk = _single_chunk_rule_repo(db_path)
    index = _ReplacingFailingIndex(db_path, source["id"])
    service = RulebookService(
        repo,
        extractor=lambda _data, _filename: None,
        index_factory=lambda _ruleset_id, _source_id: index,
    )

    with pytest.raises(RuntimeError, match="old index failed"):
        asyncio.run(service.index_source(source["id"]))

    runs = repo.connection.execute(
        """
        SELECT status, error_text
        FROM rule_ingestion_runs
        WHERE source_id = ? AND stage = 'minirag_index'
        ORDER BY created_at, id
        """,
        (source["id"],),
    ).fetchall()
    assert repo.get_rule_source(source["id"])["status"] == "ready"
    assert {row["status"] for row in runs} == {"failed", "completed"}
    failed_run = next(row for row in runs if row["status"] == "failed")
    assert "interrupted" in failed_run["error_text"].lower()
    assert repo.get_rule_chunk(chunk["id"])["minirag_doc_id"] is None
    repo.connection.close()


def test_same_source_allows_only_one_active_index_run(tmp_path: Path) -> None:
    db_path = tmp_path / "active-index-lease.sqlite3"
    setup_repo, source, chunk = _single_chunk_rule_repo(db_path)
    setup_repo.connection.close()
    repo_one = Repository(connect(db_path))
    repo_two = Repository(connect(db_path))
    started = Event()
    release = Event()
    blocking_index = _BlockingIndex(started, release)
    service_one = RulebookService(
        repo_one,
        extractor=lambda _data, _filename: None,
        index_factory=lambda _ruleset_id, _source_id: blocking_index,
    )
    service_two = RulebookService(
        repo_two,
        extractor=lambda _data, _filename: None,
        index_factory=lambda _ruleset_id, _source_id: _StaticIndex(),
    )
    errors: list[BaseException] = []

    def run_first_index() -> None:
        try:
            asyncio.run(service_one.index_source(source["id"]))
        except BaseException as exc:  # noqa: BLE001 - thread assertion collection
            errors.append(exc)

    first_thread = Thread(target=run_first_index)
    first_thread.start()
    assert started.wait(timeout=5)
    with pytest.raises(ValueError, match="already active"):
        asyncio.run(service_two.index_source(source["id"]))
    release.set()
    first_thread.join(timeout=5)

    assert errors == []
    assert not first_thread.is_alive()
    assert repo_one.get_rule_source(source["id"])["status"] == "ready"
    assert repo_one.get_rule_chunk(chunk["id"])["minirag_doc_id"].startswith(
        "active-index-"
    )
    assert repo_one.connection.execute(
        """
        SELECT COUNT(*) FROM rule_ingestion_runs
        WHERE source_id = ? AND stage = 'minirag_index'
        """,
        (source["id"],),
    ).fetchone()[0] == 1
    repo_one.connection.close()
    repo_two.connection.close()


def test_terminal_rule_reviews_cannot_overwrite_completed_evidence(
    tmp_path: Path,
) -> None:
    repo, source, chunk = _single_chunk_rule_repo(tmp_path / "terminal-review.sqlite3")
    validator = RuleValidator(repo)
    first_rule, first_validation = validator.validate(
        source["id"],
        _candidate(chunk["id"]),
    )
    first = repo.store_rule_object(
        source["id"],
        first_rule.model_dump(mode="json"),
        status="review_required",
        validation=first_validation,
    )
    second_rule, second_validation = validator.validate(
        source["id"],
        _candidate(chunk["id"], summary="另一份待拒绝候选"),
    )
    second = repo.store_rule_object(
        source["id"],
        second_rule.model_dump(mode="json"),
        status="review_required",
        validation=second_validation,
    )
    repo.commit()
    service = _rulebook_service(repo, tmp_path / "terminal-index")

    repo.begin_immediate()
    with pytest.raises(ValueError, match="Rule review changed"):
        repo.update_rule_object_review(
            first["id"],
            status="quarantined",
            validation=first["validation"],
            expected_status="review_required",
            expected_object_hash="stale-object-hash",
        )
    repo.rollback()
    assert repo.get_rule_object(first["id"])["status"] == "review_required"

    approved = service.review_rule(
        first["id"],
        _passing_review(),
        reviewer_member_id="member_approver",
    )
    rejected = service.review_rule(
        second["id"],
        _passing_review(decision="rejected"),
        reviewer_member_id="member_rejector",
    )
    approved_evidence = json.loads(json.dumps(approved["validation"]))
    rejected_evidence = json.loads(json.dumps(rejected["validation"]))

    with pytest.raises(ValueError, match="Only review-required"):
        service.review_rule(
            first["id"],
            _passing_review(decision="rejected"),
            reviewer_member_id="member_late_rejector",
        )
    with pytest.raises(ValueError, match="Only review-required"):
        service.review_rule(
            second["id"],
            _passing_review(),
            reviewer_member_id="member_late_approver",
        )

    assert repo.get_rule_object(first["id"])["validation"] == approved_evidence
    assert repo.get_rule_object(second["id"])["validation"] == rejected_evidence
    repo.connection.close()


def test_concurrent_rule_reviews_serialize_conflict_validation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "concurrent-review.sqlite3"
    setup_repo, source, chunk = _single_chunk_rule_repo(db_path)
    validator = RuleValidator(setup_repo)
    records = []
    for summary in ("并发候选甲", "并发候选乙"):
        rule, validation = validator.validate(
            source["id"],
            _candidate(chunk["id"], summary=summary),
        )
        records.append(
            setup_repo.store_rule_object(
                source["id"],
                rule.model_dump(mode="json"),
                status="review_required",
                validation=validation,
            )
        )
    setup_repo.commit()
    setup_repo.connection.close()

    repo_one = Repository(connect(db_path))
    repo_two = Repository(connect(db_path))
    service_one = _rulebook_service(repo_one, tmp_path / "concurrent-index-one")
    service_two = _rulebook_service(repo_two, tmp_path / "concurrent-index-two")
    first_at_update = Event()
    second_begin_called = Event()
    release_first = Event()
    original_update = repo_one.update_rule_object_review
    original_begin_two = repo_two.begin_immediate

    def pause_first_update(*args, **kwargs):
        first_at_update.set()
        if not release_first.wait(timeout=5):
            raise AssertionError("second review did not reach BEGIN IMMEDIATE")
        return original_update(*args, **kwargs)

    def observe_second_begin() -> None:
        second_begin_called.set()
        original_begin_two()

    repo_one.update_rule_object_review = pause_first_update
    repo_two.begin_immediate = observe_second_begin
    results: list[dict] = []
    errors: list[BaseException] = []

    def review(service: RulebookService, object_id: str, reviewer: str) -> None:
        try:
            results.append(
                service.review_rule(
                    object_id,
                    _passing_review(),
                    reviewer_member_id=reviewer,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - thread assertion collection
            errors.append(exc)

    first_thread = Thread(
        target=review,
        args=(service_one, records[0]["id"], "member_one"),
    )
    second_thread = Thread(
        target=review,
        args=(service_two, records[1]["id"], "member_two"),
    )
    first_thread.start()
    assert first_at_update.wait(timeout=5)
    second_thread.start()
    assert second_begin_called.wait(timeout=5)
    assert second_thread.is_alive()
    release_first.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    observer = Repository(connect(db_path))
    try:
        stored = observer.list_rule_objects(
            source["id"],
            rule_key="coc7.damage.major_wound",
        )
        executable = [item for item in stored if approval_allows_execution(item)]
        assert errors == []
        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        assert {item["status"] for item in results} == {
            "validated",
            "quarantined",
        }
        assert len(executable) == 1
    finally:
        observer.connection.close()
        repo_one.connection.close()
        repo_two.connection.close()


class RulebookKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.connection = connect(Path(self.tmpdir.name) / "rules.sqlite3")
        init_db(self.connection)
        self.repo = Repository(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def _source_and_chunks(self) -> tuple[dict, list[dict]]:
        with patch("ai_kp.infrastructure.knowledge.pdf_ingestion.PdfReader", _FakeReader):
            extracted = extract_rulebook_pdf(b"fake-pdf", "rules.pdf")
        source = self.repo.create_rule_source(
            ruleset_id="coc7-keeper-cn-2002c",
            title=extracted.title,
            source_filename="rules.pdf",
            source_hash=extracted.source_hash,
            page_count=extracted.page_count,
            metadata=extracted.metadata,
        )
        self.repo.replace_rule_chunks(source["id"], extracted.chunks)
        return self.repo.get_rule_source(source["id"]), extracted.chunks

    def _service(self) -> RulebookService:
        return RulebookService(
            self.repo,
            extractor=lambda _data, _filename: None,
            index_factory=lambda ruleset_id, source_id: MiniRagOriginalIndex(
                Path(self.tmpdir.name) / "rag",
                ruleset_id,
                source_id,
            ),
        )

    def test_pdf_extraction_preserves_page_provenance_and_stable_hash(self) -> None:
        _FakeReader.strict_values.clear()
        with patch("ai_kp.infrastructure.knowledge.pdf_ingestion.PdfReader", _FakeReader):
            first = extract_rulebook_pdf(b"fake-pdf", "rules.pdf")
            second = extract_rulebook_pdf(b"fake-pdf", "rules.pdf")

        self.assertEqual([False, False], _FakeReader.strict_values)
        self.assertEqual(2, first.page_count)
        self.assertEqual(hashlib.sha256(b"fake-pdf").hexdigest(), first.source_hash)
        self.assertEqual(first.chunks, second.chunks)
        self.assertEqual({"kp"}, {chunk["audience"] for chunk in first.chunks})
        self.assertEqual(2, first.chunks[-1]["page_start"])
        self.assertIn("第七章", first.chunks[-1]["chapter"])

    def test_rule_source_idempotence_is_scoped_to_one_ruleset(self) -> None:
        source_hash = hashlib.sha256(b"identical rulebook bytes").hexdigest()
        coc_source = self.repo.create_rule_source(
            ruleset_id="coc7",
            title="CoC interpretation",
            source_filename="shared.pdf",
            source_hash=source_hash,
            page_count=10,
            metadata={"system": "coc7"},
        )
        repeated_coc_source = self.repo.create_rule_source(
            ruleset_id="coc7",
            title="Ignored duplicate metadata",
            source_filename="renamed.pdf",
            source_hash=source_hash,
            page_count=99,
            metadata={"system": "should-not-overwrite"},
        )
        cyberpunk_source = self.repo.create_rule_source(
            ruleset_id="cyberpunk-red",
            title="Cyberpunk interpretation",
            source_filename="shared.pdf",
            source_hash=source_hash,
            page_count=10,
            metadata={"system": "cyberpunk-red"},
        )

        self.assertEqual(coc_source["id"], repeated_coc_source["id"])
        self.assertNotEqual(coc_source["id"], cyberpunk_source["id"])
        self.assertEqual("coc7", repeated_coc_source["ruleset_id"])
        self.assertEqual(
            {"system": "coc7"},
            repeated_coc_source["metadata"],
        )
        self.assertEqual("cyberpunk-red", cyberpunk_source["ruleset_id"])
        self.assertEqual(
            2,
            self.connection.execute("SELECT COUNT(*) FROM rule_sources").fetchone()[0],
        )

    def test_ai_candidate_requires_kp_golden_review_before_validation(self) -> None:
        source, chunks = self._source_and_chunks()
        validator = RuleValidator(self.repo)
        rule, report = validator.validate(source["id"], _candidate(chunks[-1]["id"]))

        self.assertTrue(report["schema"]["passed"])
        self.assertTrue(report["source_binding"]["passed"])
        self.assertTrue(report["citations"]["passed"])
        self.assertTrue(report["conflicts"]["passed"])
        self.assertEqual("review_required", validator.status_for(rule, report).value)
        stored = self.repo.store_rule_object(
            source["id"],
            rule.model_dump(mode="json"),
            status="review_required",
            validation=report,
        )
        with self.assertRaises(KeyError):
            self._service().execute(
                ruleset_id=source["ruleset_id"],
                rule_key=rule.rule_key,
                inputs={"damage": 6, "max_hp": 10},
                audience="kp",
            )

        reviewed = self._service().review_rule(
            stored["id"],
            RuleReviewSubmission.model_validate(
                {
                    "decision": "approved",
                    "note": "与规则书示例逐项核对",
                    "golden_cases": [
                        {
                            "name": "六点伤害对十点生命",
                            "inputs": {"damage": 6, "max_hp": 10},
                            "expected_output": {
                                "damage": 6,
                                "max_hp": 10,
                                "major_wound": True,
                                "_matched_branches": ["major-wound"],
                            },
                        }
                    ],
                }
            ),
            reviewer_member_id="member_kp",
        )
        self.assertEqual("validated", reviewed["status"])
        self.assertTrue(reviewed["validation"]["golden_tests"]["passed"])
        self.assertEqual(
            "member_kp",
            reviewed["validation"]["human_review"]["reviewer_member_id"],
        )
        self.assertTrue(
            reviewed["validation"]["human_review"]["reviewed_at"]
        )
        execution = self._service().execute(
            ruleset_id=source["ruleset_id"],
            rule_key=rule.rule_key,
            inputs={"damage": 6, "max_hp": 10},
            audience="kp",
        )
        self.assertTrue(execution["result"]["major_wound"])
        missing_review_time = json.loads(json.dumps(reviewed))
        del missing_review_time["validation"]["human_review"]["reviewed_at"]
        self.assertFalse(approval_allows_execution(missing_review_time))

        changed, changed_report = validator.validate(
            source["id"], _candidate(chunks[-1]["id"], summary="冲突版本")
        )
        self.assertFalse(changed_report["conflicts"]["passed"])
        self.assertEqual("quarantined", validator.status_for(changed, changed_report).value)

    def test_ruleset_mismatch_is_quarantined_and_confidence_never_self_approves(
        self,
    ) -> None:
        source, chunks = self._source_and_chunks()
        candidate = _candidate(chunks[-1]["id"])
        candidate["ruleset_id"] = "another-ruleset"
        candidate["confidence"] = 1.0

        rule, report = RuleValidator(self.repo).validate(source["id"], candidate)

        self.assertFalse(report["source_binding"]["passed"])
        self.assertEqual("quarantined", RuleValidator.status_for(rule, report).value)

    def test_failed_golden_case_keeps_rule_non_executable(self) -> None:
        source, chunks = self._source_and_chunks()
        rule, report = RuleValidator(self.repo).validate(
            source["id"], _candidate(chunks[-1]["id"])
        )
        stored = self.repo.store_rule_object(
            source["id"],
            rule.model_dump(mode="json"),
            status="review_required",
            validation=report,
        )

        reviewed = self._service().review_rule(
            stored["id"],
            RuleReviewSubmission.model_validate(
                {
                    "decision": "approved",
                    "golden_cases": [
                        {
                            "name": "布尔值不能用整数冒充",
                            "inputs": {"damage": 6, "max_hp": 10},
                            "expected_output": {
                                "damage": 6,
                                "max_hp": 10,
                                "major_wound": 1,
                                "_matched_branches": ["major-wound"],
                            },
                        }
                    ],
                }
            ),
            reviewer_member_id="member_kp",
        )

        self.assertEqual("review_required", reviewed["status"])
        self.assertFalse(reviewed["validation"]["golden_tests"]["passed"])
        with self.assertRaises(KeyError):
            self._service().execute(
                ruleset_id=source["ruleset_id"],
                rule_key=rule.rule_key,
                inputs={"damage": 6, "max_hp": 10},
                audience="kp",
            )

    def test_repository_rejects_direct_validated_insert(self) -> None:
        source, chunks = self._source_and_chunks()
        rule, report = RuleValidator(self.repo).validate(
            source["id"], _candidate(chunks[-1]["id"])
        )

        with self.assertRaisesRegex(ValueError, "must enter review"):
            self.repo.store_rule_object(
                source["id"],
                rule.model_dump(mode="json"),
                status="validated",
                validation=report,
            )

    def test_legacy_validated_row_without_review_evidence_fails_closed(self) -> None:
        source, chunks = self._source_and_chunks()
        rule, report = RuleValidator(self.repo).validate(
            source["id"], _candidate(chunks[-1]["id"])
        )
        stored = self.repo.store_rule_object(
            source["id"],
            rule.model_dump(mode="json"),
            status="review_required",
            validation=report,
        )
        self.repo.connection.execute(
            "UPDATE rule_objects SET status = 'validated' WHERE id = ?",
            (stored["id"],),
        )
        self.repo.commit()

        with self.assertRaisesRegex(KeyError, "executable"):
            self._service().execute(
                ruleset_id=source["ruleset_id"],
                rule_key=rule.rule_key,
                inputs={"damage": 6, "max_hp": 10},
                audience="kp",
            )
        self.assertEqual(
            [],
            self.repo.search_validated_rules(
                source["id"],
                "重伤",
                audience="kp",
            ),
        )

    def test_external_index_and_llm_calls_do_not_hold_sqlite_writer(self) -> None:
        source, chunks = self._source_and_chunks()
        initial_attempt = self.repo.claim_rule_chunk_for_extraction(chunks[0]["id"])
        self.assertIsNotNone(initial_attempt)
        self.repo.mark_rule_chunk(
            chunks[0]["id"],
            extraction_status="completed",
            expected_attempt=initial_attempt,
        )
        self.repo.commit()
        db_path = Path(self.tmpdir.name) / "rules.sqlite3"
        checking_index = _TransactionCheckingIndex(
            self.repo,
            db_path,
            source["id"],
        )
        service = RulebookService(
            self.repo,
            extractor=lambda _data, _filename: None,
            index_factory=lambda _ruleset_id, _source_id: checking_index,
        )

        indexed = asyncio.run(service.index_source(source["id"]))
        self.assertEqual("ready", indexed["status"])
        observer = connect(db_path)
        try:
            stored_index_id = observer.execute(
                "SELECT minirag_doc_id FROM rule_chunks WHERE id = ?",
                (chunks[-1]["id"],),
            ).fetchone()["minirag_doc_id"]
            self.assertEqual(f"mini-{chunks[-1]['id']}", stored_index_id)
        finally:
            observer.close()

        llm = _TransactionCheckingLlm(
            self.repo,
            db_path,
            chunks[-1]["id"],
            {"rules": [_candidate(chunks[-1]["id"])]},
        )
        run = asyncio.run(
            service.extract_rules(
                source["id"],
                llm,
                model_name="transaction-check",
                limit=1,
            )
        )

        self.assertEqual("completed", run["status"])
        stored = self.repo.list_rule_objects(
            source["id"], status="review_required"
        )
        self.assertEqual(1, len(stored))

    def test_recovery_prevents_stale_rule_results_and_completion(self) -> None:
        source, chunks = self._source_and_chunks()
        initial_attempt = self.repo.claim_rule_chunk_for_extraction(chunks[0]["id"])
        self.assertEqual(1, initial_attempt)
        self.assertTrue(
            self.repo.mark_rule_chunk(
                chunks[0]["id"],
                extraction_status="completed",
                expected_attempt=initial_attempt,
            )
        )
        self.repo.commit()
        stale_chunk = chunks[-1]
        db_path = Path(self.tmpdir.name) / "rules.sqlite3"
        llm = _RecoveringRuleLlm(
            db_path,
            stale_chunk["id"],
            {"rules": [_candidate(stale_chunk["id"])]},
        )

        run = asyncio.run(
            self._service().extract_rules(
                source["id"],
                llm,
                model_name="stale-attempt",
                limit=1,
            )
        )

        current = self.repo.get_rule_chunk(stale_chunk["id"])
        self.assertEqual("failed", run["status"])
        self.assertIn("interrupted", run["error_text"].lower())
        self.assertEqual(0, run["processed_count"])
        self.assertEqual("processing", current["extraction_status"])
        self.assertEqual(2, current["attempt_count"])
        self.assertEqual([], self.repo.list_rule_objects(source["id"]))
        self.assertEqual([], self.repo.list_rule_validation_issues(source["id"]))
        self.assertFalse(
            self.repo.mark_rule_chunk(
                stale_chunk["id"],
                extraction_status="failed",
                expected_attempt=1,
            )
        )
        self.repo.commit()
        self.assertEqual(
            "processing",
            self.repo.get_rule_chunk(stale_chunk["id"])["extraction_status"],
        )

    def test_recovery_prevents_stale_rule_failure_issue(self) -> None:
        source, chunks = self._source_and_chunks()
        initial_attempt = self.repo.claim_rule_chunk_for_extraction(chunks[0]["id"])
        self.assertEqual(1, initial_attempt)
        self.assertTrue(
            self.repo.mark_rule_chunk(
                chunks[0]["id"],
                extraction_status="completed",
                expected_attempt=initial_attempt,
            )
        )
        self.repo.commit()
        stale_chunk = chunks[-1]
        llm = _RecoveringRuleLlm(
            Path(self.tmpdir.name) / "rules.sqlite3",
            stale_chunk["id"],
            {},
            fail_after_recovery=True,
        )

        run = asyncio.run(
            self._service().extract_rules(
                source["id"],
                llm,
                model_name="stale-failure",
                limit=1,
            )
        )

        current = self.repo.get_rule_chunk(stale_chunk["id"])
        self.assertEqual("failed", run["status"])
        self.assertIn("interrupted", run["error_text"].lower())
        self.assertEqual(0, run["processed_count"])
        self.assertEqual(0, run["rejected_count"])
        self.assertEqual("processing", current["extraction_status"])
        self.assertEqual(2, current["attempt_count"])
        self.assertEqual([], self.repo.list_rule_validation_issues(source["id"]))

    def test_recovered_rule_run_cannot_claim_later_pending_chunks(self) -> None:
        source, chunks = self._source_and_chunks()
        first_chunk = chunks[0]
        second_chunk = chunks[1]
        llm = _RecoveringRuleLlm(
            Path(self.tmpdir.name) / "rules.sqlite3",
            first_chunk["id"],
            {"rules": [_candidate(first_chunk["id"])]},
        )

        run = asyncio.run(
            self._service().extract_rules(
                source["id"],
                llm,
                model_name="stale-multi-chunk-run",
                limit=2,
            )
        )

        first = self.repo.get_rule_chunk(first_chunk["id"])
        second = self.repo.get_rule_chunk(second_chunk["id"])
        self.assertEqual("failed", run["status"])
        self.assertIn("interrupted", run["error_text"].lower())
        self.assertEqual(1, llm.calls)
        self.assertEqual("processing", first["extraction_status"])
        self.assertEqual(2, first["attempt_count"])
        self.assertEqual("pending", second["extraction_status"])
        self.assertEqual(0, second["attempt_count"])
        self.assertEqual([], self.repo.list_rule_objects(source["id"]))

    def test_only_authenticated_kp_can_complete_rule_review_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "review-api.sqlite3"
            app = create_app(Settings(db_path=db_path, admin_token="test-admin"))
            with TestClient(app) as client:
                campaign = client.post(
                    "/campaigns",
                    json={"title": "规则审核测试", "system": "coc7"},
                    headers={"X-AI-KP-Admin-Token": "test-admin"},
                ).json()
                session = client.post(
                    f"/campaigns/{campaign['id']}/sessions",
                    json={"kp_display_name": "Review KP"},
                    headers={"X-AI-KP-Admin-Token": "test-admin"},
                ).json()
                player = client.post(
                    "/sessions/join",
                    json={
                        "join_code": session["join_code"],
                        "display_name": "Player",
                    },
                ).json()
                kp_headers = {
                    "Authorization": f"Bearer {session['access_token']}",
                    "X-AI-KP-Admin-Token": "test-admin",
                }
                player_headers = {
                    "Authorization": f"Bearer {player['access_token']}",
                    "X-AI-KP-Admin-Token": "test-admin",
                }
                kp_without_admin = {
                    "Authorization": f"Bearer {session['access_token']}"
                }

                connection = connect(db_path)
                try:
                    repo = Repository(connection)
                    source = repo.create_rule_source(
                        ruleset_id="coc7-keeper-cn-2002c",
                        title="API 审核规则书",
                        source_filename="api-rules.pdf",
                        source_hash="a" * 64,
                        page_count=2,
                        metadata={},
                    )
                    chunk = {
                        "id": "rulechunk_api_review",
                        "source_id": source["id"],
                        "page_start": 2,
                        "page_end": 2,
                        "order_index": 0,
                        "chapter": "伤害",
                        "section": "重伤",
                        "content_kind": "text",
                        "audience": "all",
                        "text": "单次伤害达到最大生命值一半时造成重伤。",
                        "text_hash": "b" * 64,
                    }
                    repo.replace_rule_chunks(source["id"], [chunk])
                    candidate = _candidate(chunk["id"])
                    rule, report = RuleValidator(repo).validate(
                        source["id"], candidate
                    )
                    stored = repo.store_rule_object(
                        source["id"],
                        rule.model_dump(mode="json"),
                        status="review_required",
                        validation=report,
                    )
                    connection.commit()
                finally:
                    connection.close()

                player_query_before_review = client.post(
                    "/rules/query",
                    json={
                        "question": "单次伤害达到最大生命值一半时会怎样？",
                        "top_k": 8,
                    },
                    headers=player_headers,
                )
                self.assertEqual(200, player_query_before_review.status_code)
                self.assertEqual([], player_query_before_review.json()["chunks"])
                self.assertEqual([], player_query_before_review.json()["rules"])

                kp_query_before_review = client.post(
                    "/rules/query",
                    json={
                        "question": "单次伤害达到最大生命值一半时会怎样？",
                        "top_k": 8,
                    },
                    headers=kp_headers,
                )
                self.assertEqual(200, kp_query_before_review.status_code)
                self.assertTrue(
                    any(
                        "单次伤害达到最大生命值一半时造成重伤" in chunk["text"]
                        for chunk in kp_query_before_review.json()["chunks"]
                    )
                )

                self.assertEqual(
                    403,
                    client.get(
                        f"/rulebooks/sources/{source['id']}/rules",
                        headers=player_headers,
                    ).status_code,
                )
                self.assertEqual(
                    403,
                    client.get(
                        f"/rulebooks/sources/{source['id']}/rules",
                        headers=kp_without_admin,
                    ).status_code,
                )
                payload = {
                    "decision": "approved",
                    "note": "KP 已核对原文与边界例",
                    "golden_cases": [
                        {
                            "name": "达到重伤阈值",
                            "inputs": {"damage": 6, "max_hp": 10},
                            "expected_output": {
                                "damage": 6,
                                "max_hp": 10,
                                "major_wound": True,
                                "_matched_branches": ["major-wound"],
                            },
                        }
                    ],
                }
                self.assertEqual(
                    403,
                    client.post(
                        f"/rulebooks/rules/{stored['id']}/review",
                        json=payload,
                        headers=player_headers,
                    ).status_code,
                )
                reviewed = client.post(
                    f"/rulebooks/rules/{stored['id']}/review",
                    json=payload,
                    headers=kp_headers,
                )
                self.assertEqual(200, reviewed.status_code)
                self.assertEqual("validated", reviewed.json()["status"])

                player_query_after_review = client.post(
                    "/rules/query",
                    json={
                        "question": "重伤判定",
                        "top_k": 8,
                    },
                    headers=player_headers,
                )
                self.assertEqual(200, player_query_after_review.status_code)
                self.assertEqual([], player_query_after_review.json()["chunks"])
                self.assertEqual(
                    [candidate["rule_key"]],
                    [
                        rule["rule_key"]
                        for rule in player_query_after_review.json()["rules"]
                    ],
                )

                repeated_review = client.post(
                    f"/rulebooks/rules/{stored['id']}/review",
                    json={**payload, "decision": "rejected", "golden_cases": []},
                    headers=kp_headers,
                )
                self.assertEqual(409, repeated_review.status_code)
                self.assertEqual("conflict", repeated_review.json()["code"])

                executed = client.post(
                    "/rules/execute",
                    json={
                        "ruleset_id": source["ruleset_id"],
                        "rule_key": candidate["rule_key"],
                        "inputs": {"damage": 6, "max_hp": 10},
                    },
                    headers=kp_headers,
                )
                self.assertEqual(200, executed.status_code)
                self.assertTrue(executed.json()["result"]["major_wound"])

    def test_reviewed_legacy_rule_without_explicit_audience_stays_kp_only(
        self,
    ) -> None:
        source, chunks = self._source_and_chunks()
        candidate = _candidate(chunks[-1]["id"])
        candidate.pop("audience")
        _rule, report = RuleValidator(self.repo).validate(source["id"], candidate)
        stored = self.repo.store_rule_object(
            source["id"],
            candidate,
            status="review_required",
            validation=report,
        )
        reviewed = self._service().review_rule(
            stored["id"],
            _passing_review(),
            reviewer_member_id="member_kp",
        )
        self.assertEqual("validated", reviewed["status"])

        player_result = asyncio.run(
            self._service().query(
                ruleset_id=source["ruleset_id"],
                question="重伤判定",
                audience="player",
            )
        )
        kp_result = asyncio.run(
            self._service().query(
                ruleset_id=source["ruleset_id"],
                question="重伤判定",
                audience="kp",
            )
        )

        self.assertEqual([], player_result["chunks"])
        self.assertEqual([], player_result["rules"])
        self.assertEqual([candidate["rule_key"]], [rule["rule_key"] for rule in kp_result["rules"]])
        with self.assertRaises(KeyError):
            self._service().execute(
                ruleset_id=source["ruleset_id"],
                rule_key=candidate["rule_key"],
                inputs={"damage": 6, "max_hp": 10},
                audience="player",
            )
        self.assertTrue(
            self._service()
            .execute(
                ruleset_id=source["ruleset_id"],
                rule_key=candidate["rule_key"],
                inputs={"damage": 6, "max_hp": 10},
                audience="kp",
            )["result"]["major_wound"]
        )

    def test_deterministic_engine_executes_closed_dsl_without_eval(self) -> None:
        rule = RuleObject.model_validate(_candidate("rulechunk_test"))

        result = execute_rule(rule, {"damage": 6, "max_hp": 10})
        self.assertTrue(result["major_wound"])
        self.assertEqual(["major-wound"], result["_matched_branches"])
        with self.assertRaises(RuleExecutionError):
            execute_rule(rule, {"damage": 6})

    def test_citation_text_must_exist_in_the_declared_chunk(self) -> None:
        source, chunks = self._source_and_chunks()
        candidate = _candidate(chunks[-1]["id"])
        candidate["citations"][0]["evidence_text"] = "原文中不存在的内容"

        rule, report = RuleValidator(self.repo).validate(source["id"], candidate)

        self.assertFalse(report["citations"]["passed"])
        self.assertEqual(
            "review_required", RuleValidator.status_for(rule, report).value
        )

    def test_reimport_does_not_downgrade_ready_source(self) -> None:
        source, _chunks = self._source_and_chunks()
        self.repo.set_rule_source_status(source["id"], "ready")
        with patch("ai_kp.infrastructure.knowledge.pdf_ingestion.PdfReader", _FakeReader):
            extracted = extract_rulebook_pdf(b"fake-pdf", "rules.pdf")
        service = RulebookService(
            self.repo,
            extractor=lambda _data, _filename: extracted,
            index_factory=lambda ruleset_id, source_id: MiniRagOriginalIndex(
                Path(self.tmpdir.name) / "rag",
                ruleset_id,
                source_id,
            ),
        )
        with patch("ai_kp.infrastructure.knowledge.pdf_ingestion.PdfReader", _FakeReader):
            repeated = service.ingest_pdf(
                b"fake-pdf",
                "rules.pdf",
                ruleset_id="coc7-keeper-cn-2002c",
            )

        self.assertEqual("ready", repeated["status"])


if __name__ == "__main__":
    unittest.main()

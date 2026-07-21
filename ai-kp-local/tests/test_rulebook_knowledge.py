import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_kp.core.db import connect, init_db
from ai_kp.core.repository import Repository
from ai_kp.application.rulebook_service import RulebookService
from ai_kp.rulebook.engine import RuleExecutionError, execute_rule
from ai_kp.rulebook.models import RuleObject
from ai_kp.rulebook.pdf_ingestion import extract_rulebook_pdf
from ai_kp.rulebook.validation import RuleValidator


class _FakePage:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self) -> str:
        return self.text


class _FakeReader:
    is_encrypted = False
    metadata = {"/Title": "测试规则书"}

    def __init__(self, _stream, strict=True):
        self.pages = [
            _FakePage("克苏鲁的呼唤 第七版\n6.1 检定\n技能值的一半为困难成功。\n1"),
            _FakePage("第七章 战斗\n单次伤害达到最大生命值一半时造成重伤。\n2"),
        ]


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
        with patch("ai_kp.rulebook.pdf_ingestion.PdfReader", _FakeReader):
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

    def test_pdf_extraction_preserves_page_provenance_and_stable_hash(self) -> None:
        with patch("ai_kp.rulebook.pdf_ingestion.PdfReader", _FakeReader):
            first = extract_rulebook_pdf(b"fake-pdf", "rules.pdf")
            second = extract_rulebook_pdf(b"fake-pdf", "rules.pdf")

        self.assertEqual(2, first.page_count)
        self.assertEqual(hashlib.sha256(b"fake-pdf").hexdigest(), first.source_hash)
        self.assertEqual(first.chunks, second.chunks)
        self.assertEqual(2, first.chunks[-1]["page_start"])
        self.assertIn("第七章", first.chunks[-1]["chapter"])

    def test_three_validation_layers_accept_traceable_rule_and_detect_conflict(self) -> None:
        source, chunks = self._source_and_chunks()
        validator = RuleValidator(self.repo)
        rule, report = validator.validate(source["id"], _candidate(chunks[-1]["id"]))

        self.assertTrue(report["schema"]["passed"])
        self.assertTrue(report["citations"]["passed"])
        self.assertTrue(report["conflicts"]["passed"])
        self.assertEqual("validated", validator.status_for(rule, report).value)
        self.repo.store_rule_object(
            source["id"],
            rule.model_dump(mode="json"),
            status="validated",
            validation=report,
        )

        changed, changed_report = validator.validate(
            source["id"], _candidate(chunks[-1]["id"], summary="冲突版本")
        )
        self.assertFalse(changed_report["conflicts"]["passed"])
        self.assertEqual("quarantined", validator.status_for(changed, changed_report).value)

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
        service = RulebookService(self.repo, index_root=Path(self.tmpdir.name) / "rag")

        with patch("ai_kp.application.rulebook_service.extract_rulebook_pdf") as extract:
            with patch("ai_kp.rulebook.pdf_ingestion.PdfReader", _FakeReader):
                extract.return_value = extract_rulebook_pdf(b"fake-pdf", "rules.pdf")
            repeated = service.ingest_pdf(
                b"fake-pdf",
                "rules.pdf",
                ruleset_id="coc7-keeper-cn-2002c",
            )

        self.assertEqual("ready", repeated["status"])


if __name__ == "__main__":
    unittest.main()

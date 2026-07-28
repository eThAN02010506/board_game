import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.investigators.review_policy import validate_review_decision
from ai_kp.core.config import Settings
from ai_kp.rulesets.coc7.character.pipeline import validate_character_sheet


def _sheet() -> dict:
    return {
        "identity": {"name": "三层校验", "age": 25},
        "characteristics": {
            "str": 50,
            "con": 50,
            "siz": 50,
            "dex": 60,
            "app": 50,
            "int": 60,
            "pow": 50,
            "edu": 50,
            "luck": 50,
        },
        "skills": [],
        "provenance": {
            "source_type": "manual",
            "occupation_point_formula": "edu4",
        },
    }


class CharacterValidationPipelineTests(unittest.TestCase):
    def test_report_separates_structure_rules_and_review_policy(self) -> None:
        sheet = _sheet()
        sheet["identity"]["age"] = 92
        sheet["characteristics"]["luck"] = 0
        sheet["skills"] = [
            {
                "skill_key": "coc7.spot_hidden",
                "display_name": "侦查",
                "base_value": 25,
                "occupation_points": 180,
                "interest_points": 80,
            },
            {
                "skill_key": "coc7.cthulhu_mythos",
                "display_name": "克苏鲁神话",
                "base_value": 0,
                "interest_points": 1,
            },
        ]

        result = validate_character_sheet(sheet)
        report = result.report.as_dict()
        issues = report["issues"]

        self.assertGreater(report["counts"]["structure"], 0)
        self.assertGreater(report["counts"]["ruleset"], 0)
        self.assertGreater(report["counts"]["review_policy"], 0)
        self.assertTrue(report["requires_kp_review"])
        self.assertIn("missing_luck", {issue["code"] for issue in issues})
        self.assertIn("mythos_creation_points", {issue["code"] for issue in issues})
        self.assertIn("unusual_age", {issue["code"] for issue in issues})
        self.assertEqual(result.canonical_sheet["derived"]["max_hp"], 10)

    def test_malformed_sections_are_normalized_without_crashing(self) -> None:
        result = validate_character_sheet(
            {
                "identity": "invalid",
                "characteristics": [],
                "skills": ["invalid"],
            }
        )

        codes = {issue.code for issue in result.report.issues}
        self.assertIn("invalid_section", codes)
        self.assertIn("invalid_skill", codes)
        self.assertEqual(result.canonical_sheet["skills"], [])
        self.assertEqual(result.canonical_sheet["identity"], {})

    def test_legacy_warning_list_is_derived_from_structured_issues(self) -> None:
        result = validate_character_sheet(_sheet())
        preview = result.as_preview()

        self.assertEqual(
            preview["warnings"],
            [issue["message"] for issue in preview["validation"]["issues"]],
        )
        self.assertEqual(
            set(preview["validation"]["counts"]),
            {"structure", "ruleset", "review_policy"},
        )

    def test_authenticated_preview_api_returns_three_layer_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(
                Settings(db_path=Path(tmpdir) / "character-validation.sqlite3")
            )
            with TestClient(app) as client:
                profile = client.post(
                    "/player-profiles",
                    json={"display_name": "校验测试玩家"},
                ).json()
                sheet = _sheet()
                sheet["identity"]["age"] = 92
                response = client.post(
                    "/investigators/preview",
                    headers={
                        "X-AI-KP-Player-Token": profile["player_token"],
                    },
                    json={"canonical_sheet": sheet},
                )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            set(payload["validation"]["counts"]),
            {"structure", "ruleset", "review_policy"},
        )
        self.assertTrue(payload["validation"]["requires_kp_review"])
        self.assertIn(
            "unusual_age",
            {issue["code"] for issue in payload["validation"]["issues"]},
        )


class InvestigatorReviewPolicyTests(unittest.TestCase):
    def test_change_request_requires_player_visible_comment(self) -> None:
        with self.assertRaisesRegex(ValueError, "player-visible comment"):
            validate_review_decision("changes_requested", "   ")

    def test_review_comment_is_normalized_before_persistence(self) -> None:
        self.assertEqual(
            validate_review_decision("changes_requested", "  补充职业说明  "),
            "补充职业说明",
        )
        self.assertIsNone(validate_review_decision("approved", "  "))


if __name__ == "__main__":
    unittest.main()

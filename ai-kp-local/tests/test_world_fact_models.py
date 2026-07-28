import unittest
from dataclasses import FrozenInstanceError

from ai_kp.platform.facts import (
    AppendOnlyCorrectionCommand,
    AppendOnlyCorrectionResult,
    WorldFact,
)


def _fact(**overrides) -> WorldFact:
    values = {
        "fact_id": "fact-1",
        "category": "canonical_fact",
        "visibility": "table",
        "subject": "Marsh House",
        "predicate": "is located in",
        "object_text": "Innsmouth",
    }
    values.update(overrides)
    return WorldFact(**values)


class WorldFactModelTests(unittest.TestCase):
    def test_fact_text_is_canonicalized_and_serializes_for_round_trip(self) -> None:
        fact = _fact(
            fact_id="  fact-１  ",
            subject="  Marsh\u3000House ",
            predicate="\nis   located\tin ",
            object_text=" Innsmouth\r\n waterfront ",
        )

        self.assertEqual(fact.fact_id, "fact-1")
        self.assertEqual(fact.subject, "Marsh House")
        self.assertEqual(fact.predicate, "is located in")
        self.assertEqual(fact.object_text, "Innsmouth waterfront")
        self.assertEqual(WorldFact(**fact.as_dict()), fact)
        with self.assertRaises(FrozenInstanceError):
            fact.subject = "changed"  # type: ignore[misc]

    def test_all_categories_have_a_valid_construction(self) -> None:
        facts = (
            _fact(category="canonical_fact"),
            _fact(fact_id="secret", category="kp_secret", visibility="kp"),
            _fact(
                fact_id="belief",
                category="character_belief",
                visibility="player",
                pc_id="pc-1",
            ),
            _fact(fact_id="rumor", category="rumor"),
            _fact(fact_id="hypothesis", category="ai_hypothesis", visibility="kp"),
            _fact(
                fact_id="correction",
                category="retconned",
                supersedes_fact_id="fact-1",
            ),
        )

        self.assertEqual(
            [fact.category for fact in facts],
            [
                "canonical_fact",
                "kp_secret",
                "character_belief",
                "rumor",
                "ai_hypothesis",
                "retconned",
            ],
        )

    def test_unknown_category_and_visibility_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown fact category"):
            _fact(category="opinion")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "Unknown fact visibility"):
            _fact(visibility="public")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "Unknown fact category"):
            _fact(category=[])  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "Unknown fact visibility"):
            _fact(visibility=[])  # type: ignore[arg-type]

    def test_fact_triple_fields_must_be_non_empty_text(self) -> None:
        for field_name in ("subject", "predicate", "object_text"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, f"{field_name} cannot be blank"):
                    _fact(**{field_name: " \n\t "})
                with self.assertRaisesRegex(ValueError, f"{field_name} must be text"):
                    _fact(**{field_name: None})

    def test_character_belief_owns_the_pc_constraint(self) -> None:
        with self.assertRaisesRegex(ValueError, "character_belief requires pc_id"):
            _fact(category="character_belief", visibility="player")

        belief = _fact(
            category="character_belief",
            visibility="player",
            pc_id="  pc-１ ",
        )
        self.assertEqual(belief.pc_id, "pc-1")

        for category in ("canonical_fact", "kp_secret", "rumor", "ai_hypothesis"):
            with self.subTest(category=category):
                overrides = {
                    "category": category,
                    "visibility": "kp"
                    if category in {"kp_secret", "ai_hypothesis"}
                    else "table",
                    "pc_id": "pc-1",
                }
                with self.assertRaisesRegex(
                    ValueError, "cannot target a PC"
                ):
                    _fact(**overrides)

        correction = _fact(
            fact_id="belief-correction",
            category="retconned",
            visibility="player",
            pc_id="pc-1",
            supersedes_fact_id="belief",
        )
        self.assertEqual(correction.pc_id, "pc-1")

    def test_retconned_owns_the_supersedes_constraint(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "retconned requires supersedes_fact_id"
        ):
            _fact(category="retconned")
        with self.assertRaisesRegex(ValueError, "cannot supersede itself"):
            _fact(category="retconned", supersedes_fact_id="fact-1")

        for category in (
            "canonical_fact",
            "kp_secret",
            "character_belief",
            "rumor",
            "ai_hypothesis",
        ):
            with self.subTest(category=category):
                overrides = {
                    "category": category,
                    "visibility": "kp"
                    if category in {"kp_secret", "ai_hypothesis"}
                    else "table",
                    "supersedes_fact_id": "old-fact",
                }
                if category == "character_belief":
                    overrides["pc_id"] = "pc-1"
                    overrides["visibility"] = "player"
                with self.assertRaisesRegex(
                    ValueError,
                    "supersedes_fact_id is only valid for retconned",
                ):
                    _fact(**overrides)

    def test_secret_and_ai_hypothesis_cannot_be_accidentally_exposed(self) -> None:
        for category in ("kp_secret", "ai_hypothesis"):
            for visibility in ("table", "player"):
                with self.subTest(category=category, visibility=visibility):
                    with self.assertRaisesRegex(ValueError, "visibility must be kp"):
                        _fact(category=category, visibility=visibility)

    def test_public_categories_and_character_beliefs_have_fixed_scopes(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical_fact visibility must be table"):
            _fact(category="canonical_fact", visibility="kp")
        with self.assertRaisesRegex(ValueError, "rumor visibility must be table"):
            _fact(category="rumor", visibility="player")
        with self.assertRaisesRegex(ValueError, "character_belief visibility must be player"):
            _fact(category="character_belief", visibility="table", pc_id="pc-1")

    def test_append_only_correction_command_validates_its_target(self) -> None:
        correction = _fact(
            fact_id="fact-2",
            category="retconned",
            object_text="Arkham",
            supersedes_fact_id="fact-1",
        )
        command = AppendOnlyCorrectionCommand(" fact-１ ", correction)

        self.assertEqual(command.superseded_fact_id, "fact-1")
        self.assertEqual(command.as_dict()["correction"], correction.as_dict())

        with self.assertRaisesRegex(ValueError, "must match the command target"):
            AppendOnlyCorrectionCommand("another-fact", correction)
        with self.assertRaisesRegex(ValueError, "must be a retconned fact"):
            AppendOnlyCorrectionCommand("fact-1", _fact())

    def test_append_only_result_retains_old_fact_and_appends_correction(self) -> None:
        retained = _fact()
        correction = _fact(
            fact_id="fact-2",
            category="retconned",
            object_text="Arkham",
            supersedes_fact_id="fact-1",
        )

        result = AppendOnlyCorrectionResult(retained, correction)
        serialized = result.as_dict()

        self.assertTrue(serialized["append_only"])
        self.assertEqual(serialized["retained_fact"], retained.as_dict())
        self.assertEqual(serialized["appended_fact"], correction.as_dict())
        self.assertEqual(retained.object_text, "Innsmouth")

        wrong_target = _fact(
            fact_id="fact-3",
            category="retconned",
            supersedes_fact_id="another-fact",
        )
        with self.assertRaisesRegex(ValueError, "must supersede retained_fact"):
            AppendOnlyCorrectionResult(retained, wrong_target)

        kp_retained = _fact(
            fact_id="secret-1",
            category="kp_secret",
            visibility="kp",
        )
        kp_audit_correction = _fact(
            fact_id="fact-4",
            category="retconned",
            visibility="kp",
            supersedes_fact_id="secret-1",
        )
        audit_result = AppendOnlyCorrectionResult(kp_retained, kp_audit_correction)
        self.assertEqual(audit_result.appended_fact.visibility, "kp")

        wrong_scope = _fact(
            fact_id="fact-5",
            category="retconned",
            visibility="kp",
            supersedes_fact_id="fact-1",
        )
        with self.assertRaisesRegex(ValueError, "preserve fact visibility"):
            AppendOnlyCorrectionResult(retained, wrong_scope)


if __name__ == "__main__":
    unittest.main()

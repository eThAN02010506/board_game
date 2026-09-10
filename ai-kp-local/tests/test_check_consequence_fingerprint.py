import copy
import unittest

from ai_kp.platform.resolution.check_consequences import (
    build_check_consequence_snapshot,
    check_consequence_fingerprint,
    exact_kernel_outcome,
    has_pending_push_decision,
    project_public_check_consequences,
)


def _check(check_id: str = "check-1", **overrides) -> dict:
    values = {
        "id": check_id,
        "status": "resolved",
        "proposal_id": "proposal-1",
        "player_action_id": "action-1",
        "skill_key": "spot_hidden",
        "skill_name": "Spot Hidden",
        "target": 60,
        "difficulty": "regular",
        "bonus_dice": 0,
        "hidden": False,
        "input_method": "physical",
        "raw_dice": {
            "ones_digit": 4,
            "tens_digits": [4],
            "candidates": [44],
        },
        "selected_roll": 44,
        "threshold": 60,
        "success_level": "regular",
        "passed": True,
        "override_reason": None,
        "original_result": None,
        "pushed_from_check_id": None,
        "ruleset_id": "coc7-keeper-cn-2002c",
        "ruleset_version": "2002c",
        "source_reference": {
            "chapter": "第五章 游戏系统",
            "sections": ["5.7 对抗检定"],
        },
        "requested_by_member_id": "member-secret",
        "actions": [{"reason": "private audit detail"}],
    }
    values.update(overrides)
    return values


class CheckConsequenceFingerprintTests(unittest.TestCase):
    def test_pending_push_requires_failed_pushable_leaf_without_acceptance(
        self,
    ) -> None:
        failed = _check(
            "check-root",
            passed=False,
            success_level="failure",
            allow_push=True,
            push_decision=None,
        )

        self.assertTrue(has_pending_push_decision([failed]))
        self.assertFalse(
            has_pending_push_decision([{**failed, "status": "requested"}])
        )
        self.assertFalse(
            has_pending_push_decision([{**failed, "passed": True}])
        )
        self.assertFalse(
            has_pending_push_decision([{**failed, "allow_push": False}])
        )
        self.assertFalse(
            has_pending_push_decision(
                [
                    {
                        **failed,
                        "push_decision": {"decision": "accept_failure"},
                    }
                ]
            )
        )
        child = _check(
            "check-child",
            pushed_from_check_id="check-root",
            allow_push=False,
        )
        self.assertFalse(has_pending_push_decision([failed, child]))

    def test_exact_kernel_outcome_requires_one_resolved_check_chain(self) -> None:
        preview = {
            "outcome_branches": [
                {"outcome_key": "critical", "commands": [{}]},
                {"outcome_key": "pushed_failure", "commands": [{}]},
            ]
        }
        with self.assertRaisesRegex(ValueError, "At least one"):
            exact_kernel_outcome(preview, [])
        with self.assertRaisesRegex(ValueError, "Requested checks"):
            exact_kernel_outcome(preview, [_check(status="requested")])
        with self.assertRaisesRegex(ValueError, "Cancelled checks"):
            exact_kernel_outcome(
                preview,
                [_check(
                    status="cancelled",
                    input_method=None,
                    raw_dice=None,
                    selected_roll=None,
                    threshold=None,
                    success_level=None,
                    passed=None,
                )],
            )
        with self.assertRaisesRegex(ValueError, "one independent"):
            exact_kernel_outcome(
                preview,
                [_check("check-a"), _check("check-b")],
            )

        self.assertEqual(
            exact_kernel_outcome(
                preview,
                [_check(
                    status="overridden",
                    success_level="critical",
                    override_reason="The table accepted a verified correction.",
                    original_result={
                        "selected_roll": 44,
                        "threshold": 60,
                        "success_level": "regular",
                        "passed": True,
                    },
                )],
            ),
            "critical",
        )
        failed = _check(
            "check-root",
            passed=False,
            success_level="failure",
        )
        pushed_failure = _check(
            "check-push",
            passed=False,
            success_level="failure",
            pushed_from_check_id="check-root",
        )
        self.assertEqual(
            exact_kernel_outcome(preview, [failed, pushed_failure]),
            "pushed_failure",
        )

    def test_rejects_empty_pending_and_unknown_statuses(self) -> None:
        with self.assertRaisesRegex(ValueError, "At least one"):
            check_consequence_fingerprint([])
        with self.assertRaisesRegex(ValueError, "Requested checks"):
            check_consequence_fingerprint([_check(status="requested")])
        with self.assertRaisesRegex(ValueError, "Unknown skill check status"):
            check_consequence_fingerprint([_check(status="lost")])

    def test_fingerprint_is_sha256_and_independent_of_input_order(self) -> None:
        first = _check("check-b")
        second = _check(
            "check-a",
            skill_key="listen",
            skill_name="Listen",
            selected_roll=21,
            raw_dice={
                "candidates": [21],
                "tens_digits": [2],
                "ones_digit": 1,
            },
        )

        forward = check_consequence_fingerprint([first, second])
        reversed_order = check_consequence_fingerprint([second, first])

        self.assertEqual(forward, reversed_order)
        self.assertEqual(len(forward), 64)
        self.assertTrue(all(character in "0123456789abcdef" for character in forward))

    def test_fingerprint_covers_required_audit_fields(self) -> None:
        baseline = _check()
        mutations = {
            "id": "check-other",
            "status": "cancelled",
            "raw_dice": {"ones_digit": 5, "tens_digits": [4], "candidates": [45]},
            "selected_roll": 45,
            "success_level": "failure",
            "passed": False,
            "ruleset_version": "next",
            "source_reference": {"chapter": "changed"},
            "hidden": True,
        }
        baseline_fingerprint = check_consequence_fingerprint([baseline])
        for field_name, replacement in mutations.items():
            with self.subTest(field_name=field_name):
                changed = copy.deepcopy(baseline)
                changed[field_name] = replacement
                if field_name == "status":
                    changed.update(
                        {
                            "raw_dice": None,
                            "selected_roll": None,
                            "threshold": None,
                            "success_level": None,
                            "passed": None,
                            "input_method": None,
                        }
                    )
                self.assertNotEqual(
                    check_consequence_fingerprint([changed]),
                    baseline_fingerprint,
                )

        overridden = _check(
            status="overridden",
            override_reason="The lens was broken.",
            original_result={
                "selected_roll": 44,
                "threshold": 60,
                "success_level": "regular",
                "passed": True,
            },
            success_level="failure",
            passed=False,
        )
        changed_override = copy.deepcopy(overridden)
        changed_override["override_reason"] = "Different ruling."
        self.assertNotEqual(
            check_consequence_fingerprint([overridden]),
            check_consequence_fingerprint([changed_override]),
        )

    def test_push_chain_uses_only_leaf_but_hashes_every_link(self) -> None:
        root = _check(
            "check-root",
            selected_roll=84,
            raw_dice={
                "ones_digit": 4,
                "tens_digits": [8],
                "candidates": [84],
            },
            success_level="failure",
            passed=False,
            check_plan={
                "failure_stakes": "The archive closes for the afternoon.",
                "pushed_failure_stakes": "Security removes the investigator.",
            },
            actions=[
                {
                    "action_type": "pushed",
                    "reason": "I stay past closing and search the restricted shelves.",
                }
            ],
        )
        leaf = _check(
            "check-push",
            pushed_from_check_id="check-root",
            selected_roll=12,
            raw_dice={
                "ones_digit": 2,
                "tens_digits": [1],
                "candidates": [12],
            },
            success_level="extreme",
            passed=True,
        )

        snapshot = build_check_consequence_snapshot([leaf, root])

        self.assertEqual(
            [result["check_id"] for result in snapshot["effective_results"]],
            ["check-push"],
        )
        self.assertEqual(
            snapshot["effective_results"][0]["push_chain_ids"],
            ["check-root", "check-push"],
        )
        result = snapshot["effective_results"][0]
        self.assertEqual(result["outcome"], "success")
        self.assertIn("restricted shelves", result["push_approach"])
        mutated_root = copy.deepcopy(root)
        mutated_root["raw_dice"]["candidates"] = [85]
        self.assertNotEqual(
            snapshot["result_fingerprint"],
            check_consequence_fingerprint([mutated_root, leaf]),
        )

    def test_failed_push_exposes_precommitted_stakes_to_consequence_generation(self) -> None:
        root = _check(
            "check-root",
            passed=False,
            success_level="failure",
            check_plan={
                "failure_stakes": "Lose the remaining archive time.",
                "pushed_failure_stakes": "Security expels the investigator.",
            },
            actions=[
                {
                    "action_type": "pushed",
                    "reason": "Enter the staff archive after being refused.",
                }
            ],
        )
        leaf = _check(
            "check-push",
            pushed_from_check_id="check-root",
            passed=False,
            success_level="failure",
            check_plan=root["check_plan"],
        )

        result = build_check_consequence_snapshot([root, leaf])["effective_results"][0]

        self.assertEqual(result["outcome"], "pushed_failure")
        self.assertEqual(result["accepted_stakes"], "Security expels the investigator.")
        self.assertIn("staff archive", result["push_approach"])

    def test_snapshot_is_stably_sorted_and_omits_audit_payloads(self) -> None:
        second = _check("check-b", skill_name="Listen")
        first = _check("check-a", skill_name="Spot Hidden")

        snapshot = build_check_consequence_snapshot([second, first])
        results = snapshot["effective_results"]

        self.assertEqual(
            [result["check_id"] for result in results],
            ["check-a", "check-b"],
        )
        serialized = repr(snapshot)
        self.assertNotIn("raw_dice", serialized)
        self.assertNotIn("source_reference", serialized)
        self.assertNotIn("private audit detail", serialized)
        self.assertNotIn("member-secret", serialized)

        results[0]["ruleset"]["id"] = "caller-mutation"
        replayed = build_check_consequence_snapshot([first, second])
        self.assertEqual(
            replayed["effective_results"][0]["ruleset"]["id"],
            "coc7-keeper-cn-2002c",
        )

    def test_snapshot_marks_hidden_results_and_uses_batch_strictness(self) -> None:
        public_check = _check("check-public", skill_name="Listen")
        hidden_check = _check(
            "check-hidden",
            hidden=True,
            skill_key="psychology",
            skill_name="Psychology",
        )

        snapshot = build_check_consequence_snapshot([public_check, hidden_check])

        self.assertTrue(snapshot["has_hidden_checks"])
        self.assertEqual(
            {
                result["check_id"]: result["hidden"]
                for result in snapshot["effective_results"]
            },
            {
                "check-hidden": True,
                "check-public": False,
            },
        )
        self.assertNotEqual(
            check_consequence_fingerprint([public_check]),
            check_consequence_fingerprint(
                [{**public_check, "hidden": True}]
            ),
        )

    def test_public_projection_keeps_observable_effects_without_blind_roll_metadata(
        self,
    ) -> None:
        hidden_failure = _check(
            "check-hidden",
            hidden=True,
            skill_key="psychology",
            skill_name="Psychology",
            selected_roll=96,
            threshold=40,
            success_level="fumble",
            passed=False,
            check_plan={
                "failure_stakes": "The witness ends the interview.",
                "automatic_information": ["The witness is visibly trembling."],
            },
        )

        public = project_public_check_consequences(
            build_check_consequence_snapshot([hidden_failure])
        )

        self.assertEqual(
            public["automatic_information"],
            ["The witness is visibly trembling."],
        )
        self.assertEqual(
            public["accepted_stakes"][0]["text"],
            "The witness ends the interview.",
        )
        serialized = repr(public)
        for secret in ("96", "40", "fumble", "Psychology", "psychology"):
            self.assertNotIn(secret, serialized)

    def test_override_and_push_fields_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires override audit data"):
            check_consequence_fingerprint([_check(status="overridden")])
        with self.assertRaisesRegex(ValueError, "cannot contain override audit"):
            check_consequence_fingerprint(
                [_check(override_reason="not valid on resolved")]
            )
        with self.assertRaisesRegex(ValueError, "missing parent"):
            check_consequence_fingerprint(
                [_check("child", pushed_from_check_id="absent")]
            )

        parent = _check("parent", passed=False, success_level="failure")
        first_child = _check("child-a", pushed_from_check_id="parent")
        second_child = _check("child-b", pushed_from_check_id="parent")
        with self.assertRaisesRegex(ValueError, "multiple children"):
            check_consequence_fingerprint([parent, first_child, second_child])

    def test_rejects_mixed_origins_and_incomplete_terminal_results(self) -> None:
        with self.assertRaisesRegex(ValueError, "share one.*origin"):
            check_consequence_fingerprint(
                [_check("check-a"), _check("check-b", proposal_id="proposal-2")]
            )
        with self.assertRaisesRegex(ValueError, "requires a complete result"):
            check_consequence_fingerprint([_check(raw_dice=None)])
        with self.assertRaisesRegex(ValueError, "proposal_id or player_action_id"):
            check_consequence_fingerprint(
                [_check(proposal_id=None, player_action_id=None)]
            )
        with self.assertRaisesRegex(ValueError, "hidden must be a boolean"):
            check_consequence_fingerprint([_check(hidden=None)])

    def test_source_and_raw_result_require_finite_json_objects(self) -> None:
        with self.assertRaisesRegex(ValueError, "raw_dice must be an object"):
            check_consequence_fingerprint([_check(raw_dice=["not", "an", "object"])])
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            check_consequence_fingerprint(
                [_check(source_reference={"score": float("nan")})]
            )


if __name__ == "__main__":
    unittest.main()

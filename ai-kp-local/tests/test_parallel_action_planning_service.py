from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ai_kp.application.errors import ConflictError
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.parallel_action_planning_service import (
    ParallelActionPlanningService,
)
from ai_kp.application.scenario_overlay_service import ScenarioOverlayService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.director.orchestrator import KpOrchestrator
from ai_kp.infrastructure.llm.call_registry import CampaignAiCallRegistry
from ai_kp.platform.resolution.narrative_adapter import (
    deterministic_kernel_narrative,
)
from ai_kp.platform.resolution.semantic_adapter import (
    SemanticCandidate,
    SemanticSelection,
    SemanticSelectionResult,
)
from ai_kp.platform.resolution.tabletop_turn import (
    TabletopTurnFrame,
    TabletopTurnInterpretation,
)
from ai_kp.platform.resolution.world_expansion_contract import (
    WorldExpansionContractProposal,
)
from tests.test_parallel_kernel_service import setup_bound_run


class MechanicalParallelDirector:
    def __init__(
        self,
        connection: sqlite3.Connection,
        selections: dict[str, tuple[str, str | None]],
    ):
        self.connection = connection
        self.selections = selections
        self.transaction_states: list[bool] = []
        self.snapshot_versions: list[int] = []

    def _observe(self, snapshot: Any) -> None:
        self.transaction_states.append(self.connection.in_transaction)
        self.snapshot_versions.append(int(snapshot.run_version))

    async def interpret_tabletop_turn(
        self, *, campaign_id, contract, snapshot, player_action
    ) -> TabletopTurnInterpretation:
        self._observe(snapshot)
        return TabletopTurnInterpretation(
            frame=TabletopTurnFrame(
                kind="action",
                goal=player_action,
                method=player_action,
                confidence="high",
            ),
            route="mechanical",
            attempt_count=1,
        )

    async def select_kernel_action(
        self, *, campaign_id, contract, snapshot, player_action, profile
    ) -> SemanticSelectionResult:
        self._observe(snapshot)
        operator_id, skill_key = self.selections[player_action]
        operator = next(
            item for item in contract.operators if item.operator_id == operator_id
        )
        allowed = tuple(choice.skill_key for choice in operator.skill_choices)
        return SemanticSelectionResult(
            selection=SemanticSelection(
                kind="operator",
                candidate_id=operator_id,
                requested_skill_key=skill_key,
                confidence="high",
            ),
            offered_candidates=(
                SemanticCandidate(
                    candidate_id=operator_id,
                    kind="operator",
                    title=operator.title,
                    allowed_skill_keys=allowed,
                ),
            ),
            allowed_skill_keys=allowed,
            attempt_count=1,
        )

    async def narrate_kernel_action(self, **kwargs):
        raise AssertionError("Small-profile planning must use deterministic narration")


class ForbiddenExplicitSelectionDirector:
    async def interpret_tabletop_turn(self, **kwargs):
        raise AssertionError("Explicit selection must bypass tabletop interpretation")

    async def select_kernel_action(self, **kwargs):
        raise AssertionError("Explicit selection must bypass semantic selection")

    async def narrate_kernel_action(self, **kwargs):
        raise AssertionError("Small-profile planning must use deterministic narration")


class MixedRouteDirector(MechanicalParallelDirector):
    async def interpret_tabletop_turn(
        self, *, campaign_id, contract, snapshot, player_action
    ) -> TabletopTurnInterpretation:
        self._observe(snapshot)
        if player_action == "ask an out-of-character question":
            return TabletopTurnInterpretation(
                frame=TabletopTurnFrame(
                    kind="out_of_character",
                    goal=player_action,
                    confidence="high",
                ),
                route="conversation",
                attempt_count=1,
            )
        return await super().interpret_tabletop_turn(
            campaign_id=campaign_id,
            contract=contract,
            snapshot=snapshot,
            player_action=player_action,
        )


class ClarificationSelectionDirector(MechanicalParallelDirector):
    async def select_kernel_action(self, **kwargs) -> SemanticSelectionResult:
        self._observe(kwargs["snapshot"])
        return SemanticSelectionResult(
            selection=SemanticSelection(
                kind="clarification",
                confidence="low",
                clarification="请补充会影响规则结果的目标或手段。",
            ),
            offered_candidates=(),
            allowed_skill_keys=(),
            attempt_count=2,
        )


class StaleControlDirector(MechanicalParallelDirector):
    def __init__(self, connection, selections, db_path: Path, run_id: str):
        super().__init__(connection, selections)
        self.db_path = db_path
        self.run_id = run_id
        self.changed = False

    async def select_kernel_action(self, **kwargs) -> SemanticSelectionResult:
        result = await super().select_kernel_action(**kwargs)
        if not self.changed:
            self.changed = True
            external = sqlite3.connect(self.db_path)
            try:
                external.execute(
                    """
                    UPDATE campaign_module_runs
                    SET director_control_mode = 'safety_paused',
                        director_control_reason = 'test handoff',
                        version = version + 1
                    WHERE id = ?
                    """,
                    (self.run_id,),
                )
                external.commit()
            finally:
                external.close()
        return result


class ExpansiveNarrativeDirector(MechanicalParallelDirector):
    sentinel = "UNAUTHORIZED: the missing heir is already standing behind the door."

    async def narrate_kernel_action(self, **kwargs):
        deterministic = deterministic_kernel_narrative(
            kwargs["contract"],
            kwargs["preview"],
            kwargs["player_action"],
            snapshot=kwargs["snapshot"],
        )
        return deterministic.model_copy(
            update={
                "source": "model",
                "attempt_count": 1,
                "preview_narration": (
                    f"{deterministic.preview_narration} {self.sentinel}"
                ),
                "outcomes": tuple(
                    outcome.model_copy(
                        update={
                            "public_narration": (
                                f"{outcome.public_narration} {self.sentinel}"
                            )
                        }
                    )
                    for outcome in deterministic.outcomes
                ),
            }
        )


class TransactionObservingLlm:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.transaction_states: list[bool] = []

    async def complete(self, messages, temperature=0.7) -> str:
        self.transaction_states.append(self.connection.in_transaction)
        system = messages[0].content
        user = messages[-1].content
        if "candidate_id" in system and "allowed_skill_keys" in system:
            catalog_text = user.split("候选目录：", 1)[1].split("\n返回", 1)[0]
            candidate = next(
                item
                for item in json.loads(catalog_text)
                if item["kind"] == "operator"
            )
            allowed = list(candidate.get("allowed_skill_keys") or ())
            return json.dumps(
                {
                    "kind": "operator",
                    "candidate_id": candidate["candidate_id"],
                    "requested_skill_key": allowed[0] if allowed else None,
                    "confidence": "high",
                    "clarification": None,
                }
            )
        player_text = user.split("玩家本轮输入：", 1)[-1].splitlines()[0]
        return json.dumps(
            {
                "kind": "action",
                "goal": player_text,
                "method": player_text,
                "confidence": "high",
            }
        )


def _fixture(repo: Repository) -> dict[str, Any]:
    run = setup_bound_run(repo)
    repo.initialize_scenario_run_state(str(run["id"]))
    session = SessionService(repo).create(str(run["campaign_id"]))
    first_bundle = SessionService(repo).join(
        str(session["join_code"]), display_name="Ada"
    )
    second_bundle = SessionService(repo).join(
        str(session["join_code"]), display_name="Bert"
    )
    kp = repo.authenticate_access_token(str(session["access_token"]))
    first = repo.authenticate_access_token(str(first_bundle["access_token"]))
    second = repo.authenticate_access_token(str(second_bundle["access_token"]))
    assert kp is not None and first is not None and second is not None
    actions = (
        TurnService(repo).submit_player_action(
            first,
            action_text="inspect the hidden mark",
            client_action_id="parallel-plan-a",
        ),
        TurnService(repo).submit_player_action(
            second,
            action_text="open the panel",
            client_action_id="parallel-plan-b",
        ),
    )
    repo.connection.commit()
    return {"run": run, "session": session, "kp": kp, "actions": actions}


def _selections() -> dict[str, tuple[str, str | None]]:
    return {
        "inspect the hidden mark": ("find-mark", "spot_hidden"),
        "open the panel": ("open-panel", None),
        "ask an out-of-character question": ("observe-quietly", None),
    }


def _parallel_overlay_proposal(
    repo: Repository,
    run: dict[str, Any],
    state: dict[str, Any],
) -> WorldExpansionContractProposal:
    binding = repo.get_module_run_contract_binding(str(run["id"]))
    prefix = "expansion.parallel_overlay."
    return WorldExpansionContractProposal.model_validate(
        {
            "proposal_id": "parallel_overlay",
            "base_contract_id": binding["contract"].contract_id,
            "base_source_version": binding["contract"].source_version,
            "base_contract_hash": binding["contract_hash"],
            "base_state_version": state["state_version"],
            "confidence": "high",
            "assumptions": ["A second bounded inspection route is available."],
            "rationale": "Add one bounded optional route without replacing source facts.",
            "records": {
                "operators": [
                    {
                        "operator_id": prefix + "inspect-seal",
                        "title": "Inspect the second seal",
                        "policy": "required_check",
                        "skill_choices": [
                            {
                                "skill_key": "coc7.law",
                                "reason": "Notice the faint second seal.",
                                "allow_push": False,
                                "failure_stakes": "The second seal remains unnoticed.",
                            }
                        ],
                        "success_commands": [
                            {
                                "kind": "set_fact",
                                "path": prefix + "seal_found",
                                "value": True,
                            }
                        ],
                        "failure_commands": [
                            {
                                "kind": "set_fact",
                                "path": prefix + "seal_missed",
                                "value": True,
                            }
                        ],
                        "maximum_effect": "Reveal only whether the second seal is present.",
                    }
                ]
            },
        }
    )


def test_two_players_plan_independently_against_one_snapshot_without_transaction(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-readonly-plan.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        director = MechanicalParallelDirector(connection, _selections())

        batch = _run_plan(repo, fixture["actions"], director)

        assert batch.ready is True
        assert batch.base_state_version == 0
        assert [item.action_id for item in batch.prepared] == [
            action["id"] for action in fixture["actions"]
        ]
        assert [item.operator_id for item in batch.prepared] == [
            "find-mark",
            "open-panel",
        ]
        assert {item.preview.run_version for item in batch.prepared} == {0}
        assert all(
            item.preview.action_id == item.action_id for item in batch.prepared
        )
        assert {
            item.module_run_version for item in batch.prepared
        } == {batch.module_run_version}
        assert all(item.narrative.source == "deterministic" for item in batch.prepared)
        assert director.transaction_states == [False, False, False, False]
        assert director.snapshot_versions == [0, 0, 0, 0]
        assert connection.execute("SELECT COUNT(*) FROM turn_proposals").fetchone()[0] == 0
        assert [repo.get_player_action(item["id"])["status"] for item in fixture["actions"]] == [
            "submitted",
            "submitted",
        ]


def test_parallel_plan_uses_active_overlay_contract_and_evolved_snapshot(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-active-overlay.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        run = repo.get_campaign_module_run(str(fixture["run"]["id"]))
        repo.set_module_run_automation_level(
            str(run["id"]),
            expected_version=int(run["version"]),
            level="ai_kp",
            reason="active overlay planning regression",
            member_id=fixture["kp"].member_id,
        )
        connection.commit()
        base_binding = repo.get_module_run_contract_binding(str(run["id"]))
        base_contract = base_binding["contract"]
        base_state = repo.get_scenario_run_state(str(run["id"]))

        overlay = ScenarioOverlayService(repo).propose(
            str(run["id"]),
            _parallel_overlay_proposal(repo, run, base_state),
            created_by_member_id=fixture["kp"].member_id,
        )
        connection.commit()
        assert overlay["status"] == "active", overlay["decision"]
        effective = repo.get_module_run_contract_binding(str(run["id"]))
        evolved = repo.get_scenario_run_state(str(run["id"]))

        batch = _run_plan(
            repo,
            fixture["actions"],
            MechanicalParallelDirector(connection, _selections()),
        )

        assert batch.contract_hash == effective["contract_hash"]
        assert batch.contract == effective["contract"]
        assert batch.contract.source_version == base_contract.source_version + 1
        assert any(
            item.operator_id == "expansion.parallel_overlay.inspect-seal"
            for item in batch.contract.operators
        )
        assert batch.snapshot == evolved["snapshot"]
        assert batch.base_state_version == evolved["state_version"] == 1
        assert batch.snapshot.scenario_version == batch.contract.source_version


def test_review_required_overlay_does_not_change_parallel_planning_authority(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-review-overlay.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        run = repo.get_campaign_module_run(str(fixture["run"]["id"]))
        repo.set_module_run_automation_level(
            str(run["id"]),
            expected_version=int(run["version"]),
            level="balanced",
            reason="review overlay planning regression",
            member_id=fixture["kp"].member_id,
        )
        connection.commit()
        base_binding = repo.get_module_run_contract_binding(str(run["id"]))
        base_state = repo.get_scenario_run_state(str(run["id"]))

        overlay = ScenarioOverlayService(repo).propose(
            str(run["id"]),
            _parallel_overlay_proposal(repo, run, base_state),
            created_by_member_id=fixture["kp"].member_id,
        )
        connection.commit()
        assert overlay["status"] == "review_required", overlay["decision"]
        assert repo.get_module_run_contract_binding(str(run["id"]))[
            "active_overlay_id"
        ] is None

        batch = _run_plan(
            repo,
            fixture["actions"],
            MechanicalParallelDirector(connection, _selections()),
        )

        assert batch.contract_hash == base_binding["contract_hash"]
        assert batch.contract == base_binding["contract"]
        assert batch.snapshot == base_state["snapshot"]
        assert batch.base_state_version == base_state["state_version"] == 0


def test_parallel_explicit_operator_choices_bypass_both_weak_model_stages(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-explicit-selection.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        explicit_texts = (
            '选择已发布行动“Find a hidden mark”',
            '选择已发布行动“Open the panel”',
        )
        for action, action_text in zip(fixture["actions"], explicit_texts, strict=True):
            connection.execute(
                "UPDATE player_actions SET action_text = ? WHERE id = ?",
                (action_text, action["id"]),
            )
        connection.commit()
        actions = tuple(
            repo.get_player_action(str(action["id"]))
            for action in fixture["actions"]
        )

        batch = _run_plan(repo, actions, ForbiddenExplicitSelectionDirector())

        assert batch.ready is True
        assert [item.operator_id for item in batch.prepared] == [
            "find-mark",
            "open-panel",
        ]
        assert [item.selection.attempt_count for item in batch.prepared] == [0, 0]
        assert batch.prepared[0].preview.check_plan is not None
        assert batch.prepared[0].preview.check_plan.selected_skill_key == "spot_hidden"
        assert batch.prepared[0].requested_skill_key is None
        assert batch.prepared[0].selected_skill_key == "spot_hidden"
        assert (
            batch.prepared[0].selection.selection.requested_skill_key
            == "spot_hidden"
        )
        assert connection.execute("SELECT COUNT(*) FROM turn_proposals").fetchone()[0] == 0
        connection.execute("BEGIN IMMEDIATE")
        proposal, persisted_preview = KernelActionService(
            repo
        ).persist_prepared_parallel(
            actions[0], fixture["kp"], batch.prepared[0]
        )
        payload = KernelActionService.kernel_payload(proposal)
        assert persisted_preview == batch.prepared[0].preview
        assert payload is not None
        assert payload["selection"]["selection"]["requested_skill_key"] == (
            "spot_hidden"
        )
        connection.rollback()


def test_real_orchestrator_keeps_every_model_call_outside_sqlite_transactions(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-real-orchestrator.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        llm = TransactionObservingLlm(connection)
        director = KpOrchestrator(
            connection,
            llm,
            call_registry=CampaignAiCallRegistry(),
        )

        batch = _run_plan(repo, fixture["actions"], director)

        assert batch.ready is True
        assert llm.transaction_states == [False, False, False, False]
        assert connection.in_transaction is False


def test_non_mechanical_item_is_structured_attention_and_writes_nothing(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-unsupported.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        second = repo.get_player_action(str(fixture["actions"][1]["id"]))
        connection.execute(
            "UPDATE player_actions SET action_text = ? WHERE id = ?",
            ("ask an out-of-character question", second["id"]),
        )
        connection.commit()
        actions = (
            fixture["actions"][0],
            repo.get_player_action(str(second["id"])),
        )
        director = MixedRouteDirector(connection, _selections())

        batch = _run_plan(repo, actions, director)

        assert batch.ready is False
        assert len(batch.prepared) == 1
        assert len(batch.unsupported) == 1
        assert batch.unsupported[0].route == "conversation"
        assert batch.unsupported[0].needs_attention is True
        assert connection.execute("SELECT COUNT(*) FROM turn_proposals").fetchone()[0] == 0
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "submitted"
            for action in actions
        )


def test_semantic_clarification_is_not_mislabeled_as_world_expansion(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-clarification-route.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        director = ClarificationSelectionDirector(connection, _selections())

        batch = _run_plan(repo, fixture["actions"], director)

        assert batch.ready is False
        assert batch.prepared == ()
        assert len(batch.unsupported) == 2
        assert {item.route for item in batch.unsupported} == {"clarification"}
        assert connection.execute("SELECT COUNT(*) FROM turn_proposals").fetchone()[0] == 0


def test_large_profile_model_narrative_never_enters_the_authoritative_proposal(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-narrative-authority.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        director = ExpansiveNarrativeDirector(connection, _selections())

        batch = _run_plan(repo, fixture["actions"], director, profile="large")
        prepared = batch.prepared[0]

        assert prepared.narrative == prepared.deterministic_narrative
        assert prepared.diagnostic_narrative is not None
        assert director.sentinel in prepared.diagnostic_narrative.preview_narration
        assert director.sentinel not in prepared.narrative.preview_narration

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="exact deterministic narrative"):
            KernelActionService(repo).persist_prepared_parallel(
                fixture["actions"][0],
                fixture["kp"],
                replace(prepared, narrative=prepared.diagnostic_narrative),
            )
        proposal, _preview = KernelActionService(repo).persist_prepared_parallel(
            fixture["actions"][0], fixture["kp"], prepared
        )
        payload = KernelActionService.kernel_payload(proposal)
        assert payload is not None
        assert director.sentinel not in str(proposal["public_narration"])
        assert director.sentinel not in str(payload["narrative"])
        connection.rollback()


def test_control_change_during_model_call_fails_closed_without_partial_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "parallel-stale-control.sqlite3"
    with db_session(db_path) as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        director = StaleControlDirector(
            connection,
            _selections(),
            db_path,
            str(fixture["run"]["id"]),
        )

        with pytest.raises(ConflictError, match="AI control changed"):
            _run_plan(repo, fixture["actions"], director)

        assert director.transaction_states and not any(director.transaction_states)
        assert connection.execute("SELECT COUNT(*) FROM turn_proposals").fetchone()[0] == 0
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "submitted"
            for action in fixture["actions"]
        )


def test_plan_rejects_an_action_whose_owner_is_no_longer_active(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-revoked-before-plan.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        revoked = fixture["actions"][0]
        repo.revoke_session_member(
            str(revoked["session_id"]), str(revoked["member_id"])
        )
        connection.commit()
        director = MechanicalParallelDirector(connection, _selections())

        with pytest.raises(ConflictError, match="owner to remain active"):
            _run_plan(repo, fixture["actions"], director)

        assert director.transaction_states == []
        assert connection.execute("SELECT COUNT(*) FROM turn_proposals").fetchone()[0] == 0


def test_persistence_revalidates_owner_and_module_run_version_under_the_write_lock(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-persist-races.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        batch = _run_plan(
            repo,
            fixture["actions"],
            MechanicalParallelDirector(connection, _selections()),
        )
        prepared = batch.prepared[0]
        connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (prepared.run_id,),
        )
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        with pytest.raises(ValueError, match="Scenario run changed"):
            KernelActionService(repo).persist_prepared_parallel(
                fixture["actions"][0], fixture["kp"], prepared
            )
        connection.rollback()

        fresh_batch = _run_plan(
            repo,
            fixture["actions"],
            MechanicalParallelDirector(connection, _selections()),
        )
        fresh = fresh_batch.prepared[0]
        repo.revoke_session_member(fresh.session_id, fresh.member_id)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        with pytest.raises(ConflictError, match="owner to remain active"):
            KernelActionService(repo).persist_prepared_parallel(
                fixture["actions"][0], fixture["kp"], fresh
            )
        connection.rollback()


def test_parallel_persistence_rejects_a_missing_outer_transaction(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-persist-transaction.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        batch = _run_plan(
            repo,
            fixture["actions"],
            MechanicalParallelDirector(connection, _selections()),
        )

        with pytest.raises(ConflictError, match="caller-owned transaction"):
            KernelActionService(repo).persist_prepared_parallel(
                fixture["actions"][0], fixture["kp"], batch.prepared[0]
            )

        assert connection.execute("SELECT COUNT(*) FROM turn_proposals").fetchone()[0] == 0
        assert repo.get_player_action(batch.prepared[0].action_id)["status"] == "submitted"


def test_skill_repreview_replaces_hash_and_short_transaction_persists_each_proposal(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "parallel-repreview-persist.sqlite3"
    with db_session(db_path) as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        service = ParallelActionPlanningService(repo)
        batch = _run_plan(
            repo,
            fixture["actions"],
            MechanicalParallelDirector(connection, _selections()),
        )
        original = batch.prepared[0]

        changed = service.repreview_selected_skill(
            original, requested_skill_key="art"
        )

        assert original.selected_skill_key == "spot_hidden"
        assert changed.selected_skill_key == "art"
        assert changed.selection.selection.requested_skill_key == "art"
        assert changed.preview.preview_hash != original.preview.preview_hash
        connection.execute("BEGIN IMMEDIATE")
        first_proposal, first_preview = KernelActionService(
            repo
        ).persist_prepared_parallel(
            fixture["actions"][0], fixture["kp"], changed
        )
        second_proposal, second_preview = KernelActionService(
            repo
        ).persist_prepared_parallel(
            fixture["actions"][1], fixture["kp"], batch.prepared[1]
        )

        first_adjudication = repo.create_action_adjudication(
            action_id=str(fixture["actions"][0]["id"]),
            proposal_id=str(first_proposal["id"]),
            mode="skill_check",
            reason="Choose one contract-authorized skill.",
            prompt="",
            skill_options=[
                {
                    "skill_name": "Spot Hidden",
                    "skill_key": "spot_hidden",
                    "target": 60,
                    "difficulty": "regular",
                    "reason": "Notice the faint mark.",
                    "hidden": False,
                },
                {
                    "skill_name": "Art",
                    "skill_key": "art",
                    "target": 50,
                    "difficulty": "regular",
                    "reason": "Recognize the drawing technique.",
                    "hidden": False,
                },
            ],
            selected_skill="Art",
            source_model="parallel-test",
        )
        second_adjudication = repo.create_action_adjudication(
            action_id=str(fixture["actions"][1]["id"]),
            proposal_id=str(second_proposal["id"]),
            mode="direct_resolution",
            reason="The panel is already openable.",
            prompt="",
            skill_options=[],
            selected_skill=None,
            source_model="parallel-test",
        )
        durable = repo.create_parallel_action_batch(
            campaign_id=str(batch.campaign_id),
            session_id=str(batch.session_id),
            run_id=str(batch.run_id),
            module_run_version=batch.module_run_version,
            contract_version_id=str(batch.contract_version_id),
            base_state_version=batch.base_state_version,
            idempotency_key="parallel:planning-restart",
            items=[
                {
                    "action_id": changed.action_id,
                    "proposal_id": first_proposal["id"],
                    "adjudication_id": first_adjudication["id"],
                    "actor_id": changed.actor_id,
                    "operator_id": changed.operator_id,
                    "preview_hash": changed.preview.preview_hash,
                    "selected_skill_key": "art",
                    "priority": 1,
                },
                {
                    "action_id": batch.prepared[1].action_id,
                    "proposal_id": second_proposal["id"],
                    "adjudication_id": second_adjudication["id"],
                    "actor_id": batch.prepared[1].actor_id,
                    "operator_id": batch.prepared[1].operator_id,
                    "preview_hash": second_preview.preview_hash,
                    "outcome_key": "success",
                    "priority": 0,
                },
            ],
            created_by_member_id=fixture["kp"].member_id,
        )
        connection.commit()

        assert first_preview.selected_skill_key == "art"
        assert second_preview.operator_id == "open-panel"
        assert first_proposal["id"] != second_proposal["id"]
        assert KernelActionService.kernel_payload(first_proposal)["preview"][
            "selected_skill_key"
        ] == "art"
        assert [
            repo.get_player_action(str(action["id"]))["proposal_id"]
            for action in fixture["actions"]
        ] == [first_proposal["id"], second_proposal["id"]]
        durable_id = str(durable["id"])
        action_id = changed.action_id
        member_id = changed.member_id
        original_preview_hash = original.preview.preview_hash

    # Reopen the database and reconstruct the replacement solely from durable
    # action, proposal, adjudication, batch, contract, and state authority.
    with db_session(db_path) as restarted_connection:
        restarted_repo = Repository(restarted_connection)
        restarted_service = ParallelActionPlanningService(restarted_repo)
        restarted = restarted_service.repreview_persisted_skill(
            durable_id,
            action_id,
            requested_skill_key="spot_hidden",
            actor_member_id=member_id,
        )

        assert restarted.selected_skill_key == "spot_hidden"
        assert restarted.preview.preview_hash == original_preview_hash
        assert restarted.narrative.source == "deterministic"

        restarted_connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (restarted.run_id,),
        )
        restarted_connection.commit()
        with pytest.raises(ConflictError, match="run control changed"):
            restarted_service.repreview_persisted_skill(
                durable_id,
                action_id,
                requested_skill_key="spot_hidden",
                actor_member_id=member_id,
            )


def test_planner_rejects_entry_with_an_existing_transaction(tmp_path: Path) -> None:
    with db_session(tmp_path / "parallel-existing-transaction.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _fixture(repo)
        connection.execute(
            "UPDATE campaigns SET title = title WHERE id = ?",
            (fixture["run"]["campaign_id"],),
        )

        with pytest.raises(ConflictError, match="holds a transaction"):
            _run_plan(
                repo,
                fixture["actions"],
                MechanicalParallelDirector(connection, _selections()),
            )


def _run_plan(repo, actions, director, *, profile: str = "small"):
    import asyncio

    return asyncio.run(
        ParallelActionPlanningService(repo).plan(
            actions,
            director,
            source_model="test-small-model",
            profile=profile,
        )
    )

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_kp.application.errors import InvalidInputError
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.platform.modules.ingestion import ModuleChunk
from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ScenarioContract,
)
from ai_kp.platform.resolution.evidence_compiler import EvidenceBoundContractCandidate
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.scenario_authoring import ScenarioAuthoringEvidence
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget
from tests.scenario_contract_testkit import (
    bind_payload_to_module,
    kernel_authority_basis,
)
from tests.test_evidence_compiler import sourced_contract

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "scenario_contracts"
    / "open_investigation.json"
)


def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def create_module(repo: Repository, campaign_id: str, title: str = "Module") -> dict:
    return repo.create_module(
        campaign_id,
        title,
        [
            ModuleChunk(
                title="Source",
                text="Generic source evidence.",
                visibility="kp",
                spoiler_tag=None,
                scene_key="briefing",
                order_index=0,
            )
        ],
    )


def downgrade_published_version_to_legacy_links(
    repo: Repository,
    version: dict,
    legacy_payload: dict,
) -> ScenarioContract:
    legacy = ScenarioContract.model_validate(legacy_payload)
    legacy_hash = ScenarioContractCompiler.contract_hash(legacy)
    validation = version["validation"].model_copy(
        update={"contract_hash": legacy_hash}
    )
    repo.connection.execute(
        """UPDATE scenario_contract_versions
           SET contract_hash = ?, contract_json = ?, validation_json = ?
           WHERE id = ?""",
        (
            legacy_hash,
            legacy.model_dump_json(),
            validation.model_dump_json(),
            version["id"],
        ),
    )
    return legacy


def module_evidence_candidate(
    repo: Repository,
    module_id: str,
) -> EvidenceBoundContractCandidate:
    service = ScenarioContractService(repo)
    bound = bind_payload_to_module(
        repo,
        module_id,
        sourced_contract().model_dump(mode="json"),
    )
    contract = service.compiler.compile(bound).contract
    assert contract is not None
    return EvidenceBoundContractCandidate(
        contract=contract,
        confidence="high",
        assumptions=(),
        evidence_blocks=service.authoritative_evidence_blocks(module_id),
    )


def test_coverage_supplements_focus_one_source_with_bounded_neighbor_context() -> None:
    evidence = tuple(
        ScenarioAuthoringEvidence(
            source_block_id=f"block-{index}",
            document_id="document-1",
            text=f"Evidence {index}",
        )
        for index in range(9)
    )
    targets = tuple(
        SourceCoverageSupplementTarget(
            source_block_id=item.source_block_id,
            requirement_key="explicit_checks",
            acceptable_record_kinds=("operators",),
            required_additional_count=1,
            reason="Explicit check",
        )
        for item in evidence
    ) + (
        SourceCoverageSupplementTarget(
            source_block_id="block-3",
            requirement_key="explicit_ruleset_effect",
            acceptable_record_kinds=("operators",),
            required_additional_count=1,
            reason="Explicit effect",
        ),
    )

    groups = ScenarioContractService.coverage_supplement_groups(evidence, targets)

    assert len(groups) == 9
    assert all(len(group_evidence) <= 5 for group_evidence, _ in groups)
    assert all(len(group_targets) <= 6 for _, group_targets in groups)
    assert all(
        {target.source_block_id for target in group_targets}
        == {
            source.source_block_id
            for source in group_evidence
            if source.source_block_id
            in {target.source_block_id for target in group_targets}
        }
        for group_evidence, group_targets in groups
    )
    middle_evidence, middle_targets = groups[3]
    assert [item.source_block_id for item in middle_evidence] == [
        "block-1",
        "block-2",
        "block-3",
        "block-4",
        "block-5",
    ]
    assert len(middle_targets) == 2
    assert {
        (target.source_block_id, target.requirement_key)
        for _, group_targets in groups
        for target in group_targets
    } == {
        (target.source_block_id, target.requirement_key) for target in targets
    }


def test_short_office_source_is_not_truncated_only_because_it_has_many_paragraphs() -> None:
    chunks = [
        {
            "id": f"paragraph-{index}",
            "title": "Imported scenario",
            "text": f"Short source paragraph {index}.",
            "order_index": index,
            "semantic_kind": "text",
            "classification_confidence": 1.0,
        }
        for index in range(384)
    ]

    evidence, truncated = ScenarioContractService.authoring_evidence(
        {"id": "module-many-paragraphs", "source_hash": "source-hash"},
        chunks,
    )

    assert len(evidence) == len(chunks)
    assert truncated is False


def test_authoring_evidence_preserves_server_owned_section_ancestry() -> None:
    evidence, truncated = ScenarioContractService.authoring_evidence(
        {"id": "module-1", "source_hash": "source-hash"},
        [{
            "id": "block-1",
            "title": "床架攻击",
            "text": "床架可能会攻击调查员。",
            "order_index": 1,
            "semantic_kind": "scene",
            "classification_confidence": 1.0,
            "heading_level": 4,
            "section_path": ["场景 2", "一楼", "3号房间：空卧室", "床架攻击"],
            "scene_key": "empty_room",
        }],
    )

    assert truncated is False
    assert evidence[0].heading_level == 4
    assert evidence[0].section_path[-2:] == ("3号房间：空卧室", "床架攻击")
    assert evidence[0].scene_key == "empty_room"


def test_compendium_generation_requires_and_applies_an_explicit_scope(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "compendium.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Compendium campaign")
        module = repo.create_module(
            campaign["id"],
            "Three scenarios",
            [
                ModuleChunk(
                    title=title,
                    text=text,
                    visibility="kp",
                    order_index=index,
                )
                for index, (title, text) in enumerate(
                    (
                        ("Chapter 1: Advice", "Chapter 1: Advice"),
                        ("Chapter 1: Advice", "General keeper advice."),
                        ("Chapter 2: House", "Chapter 2: House"),
                        ("Chapter 2: House", "Search the abandoned house."),
                        ("Chapter 3: Lake", "Chapter 3: Lake"),
                        ("Chapter 3: Lake", "Investigate the lakeside."),
                    )
                )
            ],
        )
        connection.execute(
            """
            UPDATE module_chunks SET semantic_kind = 'heading'
            WHERE module_id = ? AND order_index IN (0, 2, 4)
            """,
            (module["id"],),
        )
        service = ScenarioContractService(repo)

        scopes = service.source_scopes(module["id"])
        assert [scope.title for scope in scopes[1:]] == [
            "Chapter 1: Advice",
            "Chapter 2: House",
            "Chapter 3: Lake",
        ]
        with pytest.raises(InvalidInputError, match="multiple top-level sections"):
            service.prepare_generation(module["id"])

        prepared = service.prepare_generation(
            module["id"],
            source_scope_key=scopes[2].key,
        )
        assert prepared.source_scope.title == "Chapter 2: House"
        assert [item.text for item in prepared.evidence] == [
            "Chapter 2: House",
            "Search the abandoned house.",
        ]
        assert prepared.corpus_total_block_count == 2


def test_contract_draft_publish_and_run_binding_are_versioned_and_immutable(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "contracts.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Contract campaign")
        module = create_module(repo, campaign["id"])
        service = ScenarioContractService(repo)

        bound_payload = bind_payload_to_module(repo, module["id"], payload())
        result, draft = service.compile_draft(
            module["id"], bound_payload, created_by_member_id=None
        )
        assert result.report.valid is True
        assert draft is not None
        assert draft["status"] == "draft"
        assert draft["version"] == 1
        draft_travel_ids = {
            item.operator_id
            for item in draft["contract"].operators
            if item.operator_id.startswith("travel-link-")
        }
        assert len(draft_travel_ids) == 6
        assert draft["validation"].contract_hash == result.report.contract_hash
        repeated = service.compile_draft(
            module["id"], bound_payload, created_by_member_id=None
        )[1]
        assert repeated["id"] == draft["id"]

        published = service.publish(
            draft["id"], expected_row_version=1, published_by_member_id=None
        )
        assert published["status"] == "published"
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="briefing",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        binding = service.bind_run(run["id"], published["id"])
        assert binding["contract"].contract_id == "fixture-open-investigation"
        assert draft_travel_ids <= {
            item.operator_id for item in binding["contract"].operators
        }

        revised_payload = payload()
        revised_payload["source_version"] = 2
        revised_payload["title"] = "Open investigation revision"
        revised_payload = bind_payload_to_module(
            repo, module["id"], revised_payload
        )
        _, revised = service.compile_draft(
            module["id"], revised_payload, created_by_member_id=None
        )
        assert revised["version"] == 2
        revised = service.publish(
            revised["id"], expected_row_version=1, published_by_member_id=None
        )
        assert revised["status"] == "published"
        assert repo.get_scenario_contract_version(published["id"])["status"] == "superseded"

        with pytest.raises(ValueError, match="cannot switch"):
            service.bind_run(run["id"], revised["id"])
        still_bound = repo.get_module_run_contract_binding(run["id"])
        assert still_bound["contract_version_id"] == published["id"]


def test_pristine_run_explicitly_migrates_legacy_published_travel_contract(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "legacy-travel-migration.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Legacy travel migration")
        module = create_module(repo, campaign["id"])
        service = ScenarioContractService(repo)
        bound_payload = bind_payload_to_module(repo, module["id"], payload())
        _, draft = service.compile_draft(
            module["id"], bound_payload, created_by_member_id=None
        )
        assert draft is not None
        published = service.publish(
            draft["id"], expected_row_version=1, published_by_member_id=None
        )
        legacy = downgrade_published_version_to_legacy_links(
            repo, published, bound_payload
        )
        assert not any(
            item.operator_id.startswith("travel-link-") for item in legacy.operators
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="briefing",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )

        binding = service.bind_run(run["id"], published["id"])

        assert binding["contract_version_id"] != published["id"]
        assert any(
            item.operator_id.startswith("travel-link-")
            for item in binding["contract"].operators
        )
        versions = repo.list_module_scenario_contract_versions(module["id"])
        assert [item["status"] for item in versions] == ["published", "superseded"]
        migration_issue = next(
            item
            for item in versions[0]["validation"].issues
            if item.code == "legacy_travel_contract_migrated"
        )
        assert published["id"] in migration_issue.path
        assert not any(
            item.operator_id.startswith("travel-link-")
            for item in versions[1]["contract"].operators
        )
        state = repo.initialize_scenario_run_state(run["id"])
        travel = next(
            item
            for item in binding["contract"].operators
            if item.title == "Briefing → Archive"
        )
        preview = ActionResolutionKernel.from_contract(binding["contract"]).preview(
            state["snapshot"],
            ActionIntent(
                action_id="legacy-travel-action",
                actor_id="pc",
                goal="Go to Archive",
                operator_id=travel.operator_id,
            ),
        )
        committed = repo.commit_action_scenario_batch(
            run_id=run["id"],
            idempotency_key="legacy-travel-commit",
            preview=preview,
            authority_basis=kernel_authority_basis(repo, run["id"], preview),
            outcome="success",
        )
        assert committed["snapshot"].scene_id == "archive"
        repo.update_campaign_module_run(
            run["id"], {"expected_version": run["version"], "status": "paused"}
        )
        second_run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="briefing",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        second_binding = service.bind_run(second_run["id"], published["id"])
        assert second_binding["contract_version_id"] == binding["contract_version_id"]
        repo.update_campaign_module_run(
            second_run["id"],
            {"expected_version": second_run["version"], "status": "paused"},
        )
        queued_run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="briefing",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        connection.execute(
            """INSERT INTO module_run_contract_bindings
               (run_id, contract_version_id) VALUES (?, ?)""",
            (queued_run["id"], published["id"]),
        )
        repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            run_id=queued_run["id"],
            job_type="world_expansion",
            resource_id=queued_run["id"],
            idempotency_key="legacy-migration-active-job",
        )
        with pytest.raises(ValueError, match="new module run"):
            service.bind_run(queued_run["id"], published["id"])
        assert repo.get_module_run_contract_binding(queued_run["id"])[
            "contract_version_id"
        ] == published["id"]
        assert len(repo.list_module_scenario_contract_versions(module["id"])) == 2


def test_superseded_legacy_contract_requires_exact_published_replacement(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "legacy-travel-superseded.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Superseded legacy travel")
        module = create_module(repo, campaign["id"])
        service = ScenarioContractService(repo)
        bound_payload = bind_payload_to_module(repo, module["id"], payload())
        _, draft = service.compile_draft(
            module["id"], bound_payload, created_by_member_id=None
        )
        assert draft is not None
        old = service.publish(
            draft["id"], expected_row_version=1, published_by_member_id=None
        )
        downgrade_published_version_to_legacy_links(repo, old, bound_payload)

        revised_payload = payload()
        revised_payload["source_version"] = 2
        revised_payload["title"] = "Independent reviewed revision"
        revised_payload = bind_payload_to_module(repo, module["id"], revised_payload)
        _, revised_draft = service.compile_draft(
            module["id"], revised_payload, created_by_member_id=None
        )
        assert revised_draft is not None
        revised = service.publish(
            revised_draft["id"],
            expected_row_version=1,
            published_by_member_id=None,
        )
        assert repo.get_scenario_contract_version(old["id"])["status"] == "superseded"
        before = repo.list_module_scenario_contract_versions(module["id"])
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="briefing",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )

        with pytest.raises(ValueError, match="no published exact canonical replacement"):
            service.bind_run(run["id"], old["id"])

        after = repo.list_module_scenario_contract_versions(module["id"])
        assert [(item["id"], item["status"]) for item in after] == [
            (item["id"], item["status"]) for item in before
        ]
        assert repo.get_scenario_contract_version(revised["id"])["status"] == "published"
        with pytest.raises(KeyError, match="no scenario contract binding"):
            repo.get_module_run_contract_binding(run["id"])


def test_progressed_run_fails_closed_before_legacy_travel_migration(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "legacy-travel-progressed.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Progressed legacy travel")
        module = create_module(repo, campaign["id"])
        service = ScenarioContractService(repo)
        bound_payload = bind_payload_to_module(repo, module["id"], payload())
        _, draft = service.compile_draft(
            module["id"], bound_payload, created_by_member_id=None
        )
        assert draft is not None
        published = service.publish(
            draft["id"], expected_row_version=1, published_by_member_id=None
        )
        legacy = downgrade_published_version_to_legacy_links(
            repo, published, bound_payload
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="briefing",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        repo.bind_module_run_contract(
            run_id=run["id"], contract_version_id=published["id"]
        )
        state = repo.initialize_scenario_run_state(run["id"])
        operator = legacy.operators[0]
        preview = ActionResolutionKernel.from_contract(legacy).preview(
            state["snapshot"],
            ActionIntent(
                action_id="legacy-progress",
                actor_id="pc",
                goal=operator.title,
                operator_id=operator.operator_id,
            ),
        )
        repo.commit_action_scenario_batch(
            run_id=run["id"],
            idempotency_key="legacy-progress-commit",
            preview=preview,
            authority_basis=kernel_authority_basis(repo, run["id"], preview),
            outcome="success",
        )

        with pytest.raises(ValueError, match="new module run"):
            service.bind_run(run["id"], published["id"])
        versions = repo.list_module_scenario_contract_versions(module["id"])
        assert len(versions) == 1
        assert versions[0]["status"] == "published"


def test_invalid_contract_is_reported_without_persistence(tmp_path: Path) -> None:
    with db_session(tmp_path / "invalid-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Invalid contract")
        module = create_module(repo, campaign["id"])
        broken = payload()
        broken["initial_scene_id"] = "missing"

        result, saved = ScenarioContractService(repo).compile_draft(
            module["id"], broken, created_by_member_id=None
        )

        assert result.report.valid is False
        assert saved is None
        assert repo.list_module_scenario_contract_versions(module["id"]) == []


def test_structurally_valid_but_unplayable_contract_cannot_be_published(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "unplayable-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Unplayable contract")
        module = create_module(repo, campaign["id"])
        incomplete = payload()
        incomplete["endings"][0]["all_conditions"] = [{
            "path": "facts.case.never_completed",
            "operator": "eq",
            "value": True,
        }]

        result, saved = ScenarioContractService(repo).compile_draft(
            module["id"], incomplete, created_by_member_id=None
        )

        assert result.report.valid is False
        assert saved is None

        # A causally incomplete but schema-valid check remains inspectable as a
        # draft, yet the repository release boundary must still reject it.
        branch_incomplete = payload()
        branch_incomplete["operators"][0]["failure_commands"] = []
        branch_incomplete["operators"][0]["skill_choices"][0][
            "failure_stakes"
        ] = ""
        branch_incomplete = bind_payload_to_module(
            repo, module["id"], branch_incomplete
        )
        branch_result, branch_draft = ScenarioContractService(repo).compile_draft(
            module["id"], branch_incomplete, created_by_member_id=None
        )
        assert branch_result.report.valid is True
        assert branch_result.report.release_ready is False
        assert branch_draft is not None
        with pytest.raises(ValueError, match="not release-ready"):
            ScenarioContractService(repo).publish(
                branch_draft["id"],
                expected_row_version=1,
                published_by_member_id=None,
            )


def test_sourceless_manual_contract_can_be_reviewed_but_not_published(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "sourceless-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Sourceless contract")
        module = create_module(repo, campaign["id"])
        sourceless = payload()
        for group in (
            "locations",
            "location_links",
            "entities",
            "clocks",
            "resources",
            "clues",
            "operators",
            "task_methods",
            "reactive_policies",
            "response_obligations",
            "trigger_rules",
            "pressure_tracks",
            "consequence_signals",
            "endings",
        ):
            for item in sourceless.get(group, []):
                item["source_refs"] = []

        result, draft = ScenarioContractService(repo).compile_draft(
            module["id"], sourceless, created_by_member_id=None
        )

        assert result.report.valid is True
        assert result.report.playability.ready is True
        assert result.report.provenance_ready is False
        assert result.report.release_ready is False
        assert draft is not None
        with pytest.raises(ValueError, match="source provenance"):
            ScenarioContractService(repo).publish(
                draft["id"],
                expected_row_version=1,
                published_by_member_id=None,
            )


def test_nonempty_but_foreign_source_refs_cannot_be_published(tmp_path: Path) -> None:
    with db_session(tmp_path / "foreign-source-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Foreign source contract")
        module = create_module(repo, campaign["id"])

        result, draft = ScenarioContractService(repo).compile_draft(
            module["id"], payload(), created_by_member_id=None
        )

        assert result.report.valid is True
        assert result.report.provenance_ready is False
        assert result.report.release_ready is False
        assert "unknown_source_reference" in {
            item.code for item in result.report.issues
        }
        assert draft is not None
        with pytest.raises(ValueError, match="source provenance"):
            ScenarioContractService(repo).publish(
                draft["id"],
                expected_row_version=1,
                published_by_member_id=None,
            )


def test_legacy_release_ready_report_cannot_bypass_live_provenance_gate(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "legacy-sourceless-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Legacy sourceless contract")
        module = create_module(repo, campaign["id"])
        sourceless = payload()
        for group in (
            "locations",
            "location_links",
            "entities",
            "clocks",
            "resources",
            "clues",
            "operators",
            "task_methods",
            "reactive_policies",
            "response_obligations",
            "trigger_rules",
            "pressure_tracks",
            "consequence_signals",
            "endings",
        ):
            for item in sourceless.get(group, []):
                item["source_refs"] = []

        _, draft = ScenarioContractService(repo).compile_draft(
            module["id"], sourceless, created_by_member_id=None
        )
        assert draft is not None
        legacy_validation = draft["validation"].model_dump(mode="json")
        legacy_validation.pop("provenance_ready")
        legacy_validation["release_ready"] = True
        connection.execute(
            "UPDATE scenario_contract_versions SET validation_json = ? WHERE id = ?",
            (json.dumps(legacy_validation), draft["id"]),
        )

        reloaded = repo.get_scenario_contract_version(draft["id"])
        assert reloaded["validation"].release_ready is True
        assert reloaded["validation"].provenance_ready is False
        with pytest.raises(ValueError, match="source provenance"):
            ScenarioContractService(repo).publish(
                draft["id"],
                expected_row_version=1,
                published_by_member_id=None,
            )


def test_legacy_report_with_complete_provenance_remains_publishable(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "legacy-sourced-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Legacy sourced contract")
        module = create_module(repo, campaign["id"])
        result, draft = ScenarioContractService(repo).compile_draft(
            module["id"],
            bind_payload_to_module(repo, module["id"], payload()),
            created_by_member_id=None,
        )
        assert result.report.release_ready is True
        assert draft is not None
        legacy_validation = draft["validation"].model_dump(mode="json")
        legacy_validation.pop("provenance_ready")
        connection.execute(
            "UPDATE scenario_contract_versions SET validation_json = ? WHERE id = ?",
            (json.dumps(legacy_validation), draft["id"]),
        )

        reloaded = repo.get_scenario_contract_version(draft["id"])
        assert reloaded["validation"].provenance_ready is True
        published = ScenarioContractService(repo).publish(
            draft["id"],
            expected_row_version=1,
            published_by_member_id=None,
        )

        assert published["status"] == "published"


def test_legacy_release_report_cannot_bypass_current_branch_proof(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "legacy-branch-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Legacy branch contract")
        module = create_module(repo, campaign["id"])
        stakes_only = payload()
        stakes_only["operators"][0]["failure_commands"] = []
        assert stakes_only["operators"][0]["skill_choices"][0]["failure_stakes"]
        stakes_only = bind_payload_to_module(repo, module["id"], stakes_only)
        result, draft = ScenarioContractService(repo).compile_draft(
            module["id"], stakes_only, created_by_member_id=None
        )
        assert result.report.valid is True
        assert result.report.release_ready is False
        assert draft is not None

        legacy_validation = draft["validation"].model_dump(mode="json")
        legacy_validation.pop("provenance_ready")
        legacy_validation["release_ready"] = True
        for proof in legacy_validation["playability"]["proofs"]:
            if proof["invariant"] == "branch_consequences":
                proof["status"] = "passed"
                proof["counterexamples"] = []
                proof["witness"] = ["Legacy analyzer accepted narrated stakes."]
        connection.execute(
            "UPDATE scenario_contract_versions SET validation_json = ? WHERE id = ?",
            (json.dumps(legacy_validation), draft["id"]),
        )

        reloaded = repo.get_scenario_contract_version(draft["id"])
        assert reloaded["validation"].release_ready is True
        assert reloaded["validation"].provenance_ready is True
        with pytest.raises(ValueError, match="branch_consequences"):
            ScenarioContractService(repo).publish(
                draft["id"],
                expected_row_version=1,
                published_by_member_id=None,
            )


def test_full_ai_auto_publishes_only_high_confidence_evidence_bound_contracts(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "evidence-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Evidence-bound campaign")
        module = create_module(repo, campaign["id"])
        candidate = module_evidence_candidate(repo, module["id"])

        result, saved, auto_published = ScenarioContractService(
            repo
        ).compile_evidence_bound_draft(
            module["id"],
            candidate,
            automation_level="ai_kp",
            created_by_member_id=None,
        )

        assert result.decision == "auto_publishable"
        assert auto_published is True
        assert saved is not None
        assert saved["status"] == "published"


def test_evidence_candidate_cannot_supply_a_foreign_module_corpus(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "foreign-evidence-candidate.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Foreign evidence campaign")
        module = create_module(repo, campaign["id"])
        candidate = module_evidence_candidate(repo, module["id"])
        foreign = candidate.model_copy(
            update={
                "evidence_blocks": (
                    candidate.evidence_blocks[0].model_copy(
                        update={"document_id": "foreign-document"}
                    ),
                )
            }
        )

        result, saved, auto_published = ScenarioContractService(
            repo
        ).compile_evidence_bound_draft(
            module["id"],
            foreign,
            automation_level="ai_kp",
            created_by_member_id=None,
        )

        assert result.decision == "rejected"
        assert result.report.valid is False
        assert result.report.provenance_ready is False
        assert result.report.release_ready is False
        assert saved is None
        assert auto_published is False


def test_balanced_mode_keeps_auto_publishable_contract_as_reviewable_draft(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "review-contract.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Review contract campaign")
        module = create_module(repo, campaign["id"])
        candidate = module_evidence_candidate(repo, module["id"])

        _, saved, auto_published = ScenarioContractService(
            repo
        ).compile_evidence_bound_draft(
            module["id"],
            candidate,
            automation_level="balanced",
            created_by_member_id=None,
        )

        assert auto_published is False
        assert saved is not None
        assert saved["status"] == "draft"


def test_evidence_compilation_does_not_hold_sqlite_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "compile-without-writer-lock.sqlite3"
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Concurrent compilation")
        module = create_module(repo, campaign["id"])
        candidate = module_evidence_candidate(repo, module["id"])
        connection.commit()
        service = ScenarioContractService(repo)
        original = service.evidence_compiler.compile
        observed = False

        def compile_while_another_writer_commits(current):
            nonlocal observed
            assert connection.in_transaction is False
            with db_session(db_path) as other_connection:
                other_connection.execute("BEGIN IMMEDIATE")
                other_connection.execute(
                    "UPDATE campaigns SET title = title WHERE id = ?",
                    (campaign["id"],),
                )
            observed = True
            return original(current)

        monkeypatch.setattr(
            service.evidence_compiler, "compile", compile_while_another_writer_commits
        )

        result = service.compile_evidence_bound_candidate(module["id"], candidate)

        assert observed is True
        assert result.report.valid is True


def test_precomputed_evidence_persistence_rolls_back_as_one_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with db_session(tmp_path / "evidence-persist-rollback.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Atomic persistence")
        module = create_module(repo, campaign["id"])
        candidate = module_evidence_candidate(repo, module["id"])
        connection.commit()
        service = ScenarioContractService(repo)
        result = service.compile_evidence_bound_candidate(module["id"], candidate)

        def fail_publish(*_args, **_kwargs):
            raise RuntimeError("injected publication failure")

        monkeypatch.setattr(repo, "publish_scenario_contract_version", fail_publish)
        with pytest.raises(RuntimeError, match="injected publication failure"):
            service.persist_evidence_bound_compilation(
                module["id"],
                result,
                automation_level="ai_kp",
                created_by_member_id=None,
            )
        connection.rollback()

        count = connection.execute(
            "SELECT COUNT(*) FROM scenario_contract_versions WHERE module_id = ?",
            (module["id"],),
        ).fetchone()[0]
        assert count == 0

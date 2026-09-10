import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_kp.application.gameplay_service import (
    EncounterCreateCommand,
    GameplayCommand,
    GameplayService,
)
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.ruleset_effect_service import RulesetEffectService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog
from tests.scenario_contract_testkit import (
    bind_payload_to_module,
    source_bound_payload,
)
from tests.test_scenario_contract_service import create_module


def test_san_loss_payload_is_extracted_from_source_not_model_output() -> None:
    effects = coc7_scenario_effect_catalog().extract_source_effects(
        "听到非人的喘息声，进行 SAN 检定并造成 SAN 0/1 的减少。"
    )

    assert len(effects) == 1
    assert effects[0].effect_key == "san_loss"
    assert effects[0].payload == {"loss": "0/1"}


def test_source_effects_keep_their_explicit_failure_outcomes() -> None:
    effects = coc7_scenario_effect_catalog().extract_source_effects(
        "普通失败造成 1 点生命值损失；推动失败造成 1d4 点生命值损失。"
    )

    assert [(item.payload, item.outcome) for item in effects] == [
        ({"damage": "1"}, "failure"),
        ({"damage": "1d4"}, "pushed_failure"),
    ]


def test_catalog_resolves_declared_additive_source_term_to_concrete_expression() -> None:
    effects = coc7_scenario_effect_catalog().extract_source_effects(
        "攻击命中造成 1D3 + 伤害加值 (1D4) 点伤害。"
    )

    assert [(item.effect_key, item.payload) for item in effects] == [
        ("damage", {"damage": "1d3+1d4"})
    ]


def test_catalog_rejects_unresolved_or_injected_dice_variables() -> None:
    catalog = coc7_scenario_effect_catalog()
    assert catalog.validate_effect("damage", {"damage": "1d3+伤害加值"})
    assert catalog.validate_effect("damage", {"damage": "1d3);drop table"})


def test_catalog_extracts_restoration_but_ignores_example_numbers() -> None:
    catalog = coc7_scenario_effect_catalog()
    restored = catalog.extract_source_effects("调查员恢复SAN 1D6点。")
    example = catalog.extract_source_effects("例如：护甲减少2点伤害，仅用于说明计算方式。")

    assert [(item.effect_key, item.payload) for item in restored] == [
        ("san_restore", {"amount": "1d6"})
    ]
    assert example == ()


@pytest.mark.parametrize(
    "source",
    (
        "伤害加值 1D4。",
        "防弹衣的护甲减少2点伤害。",
    ),
)
def test_catalog_does_not_turn_rules_statements_into_runtime_effects(source: str) -> None:
    assert coc7_scenario_effect_catalog().extract_source_effects(source) == ()


def test_catalog_distinguishes_armor_change_and_composite_attack_damage() -> None:
    catalog = coc7_scenario_effect_catalog()

    armor = catalog.extract_source_effects("护甲值降低2点。")
    damage = catalog.extract_source_effects("本攻击造成1D3 + 伤害加值(1D4)。")

    assert [(item.effect_key, item.payload) for item in armor] == [
        ("armor_reduction", {"amount": "2"})
    ]
    assert [(item.effect_key, item.payload) for item in damage] == [
        ("damage", {"damage": "1d3+1d4"})
    ]


def create_effect_subject(repo: Repository, campaign_id: str) -> tuple[dict, dict]:
    profile = repo.create_player_profile("Effect investigator")["profile"]
    sheet = {
        "ruleset_id": "coc7-keeper-cn-2002c",
        "identity": {"name": "Effect investigator", "occupation": "Reporter"},
        "characteristics": {"con": 60, "int": 70, "luck": 50},
        "derived": {
            "max_hp": 10,
            "initial_san": 50,
            "max_san": 99,
            "max_mp": 10,
        },
        "skills": [
            {
                "skill_key": "spot_hidden",
                "display_name": "侦查",
                "current_value": 50,
            },
            {
                "skill_key": "fighting_brawl",
                "display_name": "格斗（斗殴）",
                "current_value": 25,
            },
            {
                "skill_key": "dodge",
                "display_name": "闪避",
                "current_value": 20,
            },
        ],
        "combat": {
            "weapons": [
                {
                    "id": "camera_flash",
                    "name": "改装闪光灯",
                    "skill_key": "spot_hidden",
                    "skill_target": 70,
                    "damage": "1d3",
                    "range": "short",
                }
            ]
        },
    }
    investigator = repo.create_investigator(profile["id"], sheet, source_type="manual")
    pc = repo.create_pc(campaign_id, "Effect investigator", {})
    repo.connection.execute(
        """
        INSERT INTO campaign_investigators
          (campaign_id, investigator_id, owner_profile_id,
           submitted_revision_id, approved_revision_id, legacy_pc_id, status)
        VALUES (?, ?, ?, ?, ?, ?, 'approved')
        """,
        (
            campaign_id,
            investigator["id"],
            profile["id"],
            investigator["current_revision_id"],
            investigator["current_revision_id"],
            pc["id"],
        ),
    )
    repo.connection.execute(
        """
        INSERT INTO investigator_campaign_state
          (campaign_id, investigator_id, approved_revision_id,
           current_hp, current_san, current_mp, current_luck,
           conditions_json, inventory_delta_json, state_version)
        VALUES (?, ?, ?, 10, 50, 10, 50, '[]', '{}', 0)
        """,
        (campaign_id, investigator["id"], investigator["current_revision_id"]),
    )
    return pc, investigator


def test_damage_effect_and_required_major_wound_check_are_idempotent(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "damage-effect.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Damage effect")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        pc, investigator = create_effect_subject(repo, campaign["id"])
        batch = {
            "id": "scenario-damage-1",
            "commands": [
                {
                    "kind": "apply_ruleset_effect",
                    "event_type": "damage",
                    "payload": {"damage": "6"},
                }
            ],
        }

        first = RulesetEffectService(repo).apply_batch(
            batch,
            identity,
            campaign_id=campaign["id"],
            fallback_actor_id=pc["id"],
        )
        replay = RulesetEffectService(repo).apply_batch(
            batch,
            identity,
            campaign_id=campaign["id"],
            fallback_actor_id=pc["id"],
        )

        assert first[0]["event"]["result"]["damage"] == 6
        assert len(first[0]["follow_ups"]) == 1
        assert replay[0]["idempotent_replay"] is True
        assert replay[0]["follow_ups"][0]["idempotent_replay"] is True
        current = repo.get_campaign_investigator(
            campaign["id"], investigator["id"]
        )["campaign_state"]
        assert current["current_hp"] == 4
        events = repo.list_coc7_gameplay_events(
            campaign_id=campaign["id"], investigator_id=investigator["id"]
        )
        assert {item["event_type"] for item in events} == {
            "character.damage",
            "character.major_wound_con",
        }


def test_composite_damage_records_each_term_and_retry_does_not_roll_again(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "composite-damage.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Composite damage")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        pc, _investigator = create_effect_subject(repo, campaign["id"])
        batch = {
            "id": "composite-damage-1",
            "commands": [
                {
                    "kind": "apply_ruleset_effect",
                    "event_type": "damage",
                    "payload": {"damage": "1d3+1d4"},
                }
            ],
        }
        generated: list[int] = []

        def recorded_randbelow(sides: int) -> int:
            generated.append(sides)
            return 0

        with patch("ai_kp.application.gameplay_service.secrets.randbelow", recorded_randbelow):
            first = RulesetEffectService(repo).apply_batch(
                batch,
                identity,
                campaign_id=campaign["id"],
                fallback_actor_id=pc["id"],
            )
            replay = RulesetEffectService(repo).apply_batch(
                batch,
                identity,
                campaign_id=campaign["id"],
                fallback_actor_id=pc["id"],
            )

        evidence = first[0]["event"]["result"]["dice"]
        assert generated == [3, 4]
        assert evidence == {
            "expression": "1d3+1d4",
            "rolls": [1, 1],
            "terms": [
                {"term_index": 0, "expression": "1d3", "rolls": [1], "subtotal": 1},
                {"term_index": 1, "expression": "1d4", "rolls": [1], "subtotal": 1},
            ],
            "modifier": 0,
            "total": 2,
        }
        assert replay[0]["idempotent_replay"] is True
        assert replay[0]["event"]["result"]["dice"] == evidence


def test_resource_skill_and_armor_effects_apply_and_replay_without_rerolls(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "bounded-character-effects.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Bounded character effects")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        pc, investigator = create_effect_subject(repo, campaign["id"])
        batch = {
            "id": "bounded-effects-1",
            "commands": [
                {"kind": "apply_ruleset_effect", "event_type": "san_restore", "payload": {"amount": "1d6"}},
                {"kind": "apply_ruleset_effect", "event_type": "mp_loss", "payload": {"amount": "1d3"}},
                {"kind": "apply_ruleset_effect", "event_type": "skill_gain", "payload": {"skill_key": "spot_hidden", "amount": "4"}},
                {"kind": "apply_ruleset_effect", "event_type": "armor_gain", "payload": {"amount": "2"}},
                {"kind": "apply_ruleset_effect", "event_type": "armor_reduction", "payload": {"amount": "1"}},
                {"kind": "apply_ruleset_effect", "event_type": "damage", "payload": {"damage": "1d6"}},
            ],
        }
        faces = iter((3, 1, 2))
        generated: list[int] = []

        def recorded_randbelow(sides: int) -> int:
            generated.append(sides)
            return next(faces)

        with patch("ai_kp.application.gameplay_service.secrets.randbelow", recorded_randbelow):
            first = RulesetEffectService(repo).apply_batch(
                batch, identity, campaign_id=campaign["id"], fallback_actor_id=pc["id"]
            )
            replay = RulesetEffectService(repo).apply_batch(
                batch, identity, campaign_id=campaign["id"], fallback_actor_id=pc["id"]
            )

        assert generated == [6, 3, 6]
        assert [item["event"]["result"]["new_value"] for item in first[:5]] == [54, 8, 54, 2, 1]
        damage = first[5]["event"]["result"]
        assert (damage["rolled_damage"], damage["armor_reduction"], damage["damage"]) == (3, 1, 2)
        current = repo.get_campaign_investigator(campaign["id"], investigator["id"])["campaign_state"]
        assert (current["current_hp"], current["current_san"], current["current_mp"]) == (8, 54, 8)
        target = repo.resolve_skill_target(campaign["id"], pc["id"], "侦查")
        assert target is not None and target["target"] == 54
        assert target["target_source"] == "investigator_campaign_state"
        listed = repo.list_character_skill_targets(campaign["id"], pc["id"])
        assert next(item for item in listed if item["skill_key"] == "spot_hidden")["target"] == 54
        assert all(item["idempotent_replay"] is True for item in replay)
        assert replay[5]["event"]["result"] == damage


def test_character_effect_catalog_enforces_closed_payloads_and_dice_bounds() -> None:
    catalog = coc7_scenario_effect_catalog()
    for effect_key in ("san_restore", "mp_gain", "mp_loss", "armor_gain", "armor_reduction"):
        assert catalog.validate_effect(effect_key, {"amount": "1d3+1d4"}) == ()
        assert catalog.validate_effect(effect_key, {"amount": "101d6"})
        assert catalog.validate_effect(effect_key, {"amount": "1d6+variable"})
        assert catalog.validate_effect(effect_key, {"amount": "1", "extra": 1})
    assert catalog.validate_effect("skill_gain", {"amount": "4"})
    assert catalog.validate_effect("skill_gain", {"skill_key": "spot_hidden", "amount": "4"}) == ()


def test_runtime_skill_and_armor_changes_sync_an_active_encounter(tmp_path: Path) -> None:
    with db_session(tmp_path / "active-encounter-sync.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Active encounter sync")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        pc, investigator = create_effect_subject(repo, campaign["id"])
        encounter = GameplayService(repo).create_encounter(
            campaign["id"],
            identity,
            EncounterCreateCommand(
                kind="combat",
                title="Runtime sync",
                participants=(
                    {
                        "participant_id": "investigator",
                        "name": "Effect investigator",
                        "investigator_id": investigator["id"],
                    },
                    {
                        "participant_id": "threat",
                        "name": "Threat",
                        "dex": 1,
                        "max_hp": 10,
                        "current_hp": 10,
                    },
                ),
            ),
        )
        batch = {
            "id": "active-encounter-effects",
            "commands": [
                {"kind": "apply_ruleset_effect", "event_type": "skill_gain", "payload": {"skill_key": "fighting_brawl", "amount": "4"}},
                {"kind": "apply_ruleset_effect", "event_type": "skill_gain", "payload": {"skill_key": "dodge", "amount": "5"}},
                {"kind": "apply_ruleset_effect", "event_type": "skill_gain", "payload": {"skill_key": "spot_hidden", "amount": "4"}},
                {"kind": "apply_ruleset_effect", "event_type": "armor_gain", "payload": {"amount": "2"}},
                {"kind": "apply_ruleset_effect", "event_type": "damage", "payload": {"damage": "3"}},
            ],
        }

        RulesetEffectService(repo).apply_batch(
            batch, identity, campaign_id=campaign["id"], fallback_actor_id=pc["id"]
        )

        updated = repo.get_coc7_encounter(encounter["id"])
        participant = next(
            item
            for item in updated["state"]["participants"]
            if item["participant_id"] == "investigator"
        )
        brawl = next(
            item
            for item in participant["action_profiles"]
            if item["action_key"] == "unarmed_brawl"
        )
        assert brawl["skill_target"] == 29
        weapon = next(
            item
            for item in participant["action_profiles"]
            if item["action_key"] == "camera_flash"
        )
        assert weapon["skill_target"] == 74
        assert participant["dodge_target"] == 25
        assert participant["current_hp"] == 9
        assert next(
            item for item in participant["conditions"] if item["type"] == "armor"
        )["value"] == 2
        character = repo.get_campaign_investigator(
            campaign["id"], investigator["id"]
        )["campaign_state"]
        assert character["current_hp"] == participant["current_hp"]
        assert character["conditions"] == participant["conditions"]


def test_runtime_skill_sync_preserves_explicit_encounter_action_profiles(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "declared-encounter-profile.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Declared encounter profile")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        pc, investigator = create_effect_subject(repo, campaign["id"])
        encounter = GameplayService(repo).create_encounter(
            campaign["id"],
            identity,
            EncounterCreateCommand(
                kind="combat",
                title="Declared profile",
                participants=(
                    {
                        "participant_id": "investigator",
                        "name": "Effect investigator",
                        "investigator_id": investigator["id"],
                        "action_profiles": [
                            {
                                "action_key": "unarmed_brawl",
                                "label": "Improvised stance",
                                "kind": "melee",
                                "skill_target": 60,
                                "damage_expression": "1d3",
                            }
                        ],
                    },
                    {
                        "participant_id": "threat",
                        "name": "Threat",
                        "dex": 1,
                        "max_hp": 10,
                        "current_hp": 10,
                    },
                ),
            ),
        )

        RulesetEffectService(repo).apply_batch(
            {
                "id": "declared-profile-skill-effect",
                "commands": [
                    {
                        "kind": "apply_ruleset_effect",
                        "event_type": "skill_gain",
                        "payload": {"skill_key": "fighting_brawl", "amount": "4"},
                    }
                ],
            },
            identity,
            campaign_id=campaign["id"],
            fallback_actor_id=pc["id"],
        )

        participant = next(
            item
            for item in repo.get_coc7_encounter(encounter["id"])["state"]["participants"]
            if item["participant_id"] == "investigator"
        )
        assert participant["action_profiles_source"] == "declared"
        assert participant["action_profiles"] == [
            {
                "action_key": "unarmed_brawl",
                "label": "Improvised stance",
                "kind": "melee",
                "skill_target": 60,
                "damage_expression": "1d3",
            }
        ]


@pytest.mark.parametrize("legacy_profile_kind", ("generated", "custom_same_key"))
def test_legacy_profile_inference_requires_full_canonical_equality(
    tmp_path: Path, legacy_profile_kind: str
) -> None:
    with db_session(tmp_path / f"legacy-{legacy_profile_kind}.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Legacy profile inference")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        pc, investigator = create_effect_subject(repo, campaign["id"])
        declared = (
            [
                {
                    "action_key": "unarmed_brawl",
                    "label": "Legacy custom stance",
                    "kind": "melee",
                    "skill_target": 60,
                    "damage_expression": "1d3",
                },
                {
                    "action_key": "camera_flash",
                    "label": "改装闪光灯",
                    "kind": "firearm",
                    "skill_target": 70,
                    "damage_expression": "1d3",
                },
            ]
            if legacy_profile_kind == "custom_same_key"
            else []
        )
        encounter = GameplayService(repo).create_encounter(
            campaign["id"],
            identity,
            EncounterCreateCommand(
                kind="combat",
                title="Legacy profile",
                participants=(
                    {
                        "participant_id": "investigator",
                        "name": "Effect investigator",
                        "investigator_id": investigator["id"],
                        **({"action_profiles": declared} if declared else {}),
                    },
                    {
                        "participant_id": "threat",
                        "name": "Threat",
                        "dex": 1,
                        "max_hp": 10,
                        "current_hp": 10,
                    },
                ),
            ),
        )
        legacy_state = encounter["state"]
        participant = next(
            item
            for item in legacy_state["participants"]
            if item["participant_id"] == "investigator"
        )
        participant.pop("action_profiles_source")
        connection.execute(
            "UPDATE coc7_encounters SET state_json = ? WHERE id = ?",
            (json.dumps(legacy_state, ensure_ascii=False), encounter["id"]),
        )

        RulesetEffectService(repo).apply_batch(
            {
                "id": f"legacy-profile-{legacy_profile_kind}",
                "commands": [
                    {
                        "kind": "apply_ruleset_effect",
                        "event_type": "skill_gain",
                        "payload": {"skill_key": "fighting_brawl", "amount": "4"},
                    }
                ],
            },
            identity,
            campaign_id=campaign["id"],
            fallback_actor_id=pc["id"],
        )

        updated = next(
            item
            for item in repo.get_coc7_encounter(encounter["id"])["state"]["participants"]
            if item["participant_id"] == "investigator"
        )
        if legacy_profile_kind == "generated":
            assert updated["action_profiles_source"] == "ruleset_generated"
            assert next(
                item
                for item in updated["action_profiles"]
                if item["action_key"] == "unarmed_brawl"
            )["skill_target"] == 29
        else:
            assert "action_profiles_source" not in updated
            assert updated["action_profiles"] == declared


def test_encounter_damage_syncs_canonical_state_to_other_active_encounters(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "cross-encounter-health-sync.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Cross encounter health sync")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        _pc, investigator = create_effect_subject(repo, campaign["id"])

        def create(title: str) -> dict:
            return GameplayService(repo).create_encounter(
                campaign["id"],
                identity,
                EncounterCreateCommand(
                    kind="combat",
                    title=title,
                    participants=(
                        {
                            "participant_id": "investigator",
                            "name": "Effect investigator",
                            "investigator_id": investigator["id"],
                        },
                        {
                            "participant_id": f"threat-{title}",
                            "name": "Threat",
                            "dex": 1,
                            "max_hp": 10,
                            "current_hp": 10,
                        },
                    ),
                ),
            )

        encounter_a = create("A")
        encounter_b = create("B")
        applied = GameplayService(repo).command_encounter(
            encounter_a["id"],
            identity,
            GameplayCommand(
                command_id="encounter-a-damage",
                expected_version=0,
                command_type="damage",
                payload={"target_id": "investigator", "damage": 3},
            ),
        )

        linked = applied["linked_character_transition"]
        character_event_id = linked["event"]["id"]
        expected_sync_id = "character-state-sync-" + hashlib.sha256(
            f"{character_event_id}:{encounter_b['id']}".encode()
        ).hexdigest()
        assert [item["event"]["command_id"] for item in linked["encounter_syncs"]] == [
            expected_sync_id
        ]
        refreshed_a = repo.get_coc7_encounter(encounter_a["id"])
        refreshed_b = repo.get_coc7_encounter(encounter_b["id"])
        hp_a = next(
            item for item in refreshed_a["state"]["participants"] if item["participant_id"] == "investigator"
        )["current_hp"]
        hp_b = next(
            item for item in refreshed_b["state"]["participants"] if item["participant_id"] == "investigator"
        )["current_hp"]
        assert hp_a == hp_b == linked["state"]["current_hp"] == 7
        assert refreshed_a["version"] == 1
        assert refreshed_b["version"] == 1


def test_effect_batch_rolls_back_a_late_failure_then_retries_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with db_session(tmp_path / "effect-batch-rollback.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Effect batch rollback")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        pc, investigator = create_effect_subject(repo, campaign["id"])
        batch = {
            "id": "late-failure-effects",
            "commands": [
                {"kind": "apply_ruleset_effect", "event_type": "armor_gain", "payload": {"amount": "2"}},
                {"kind": "apply_ruleset_effect", "event_type": "skill_gain", "payload": {"skill_key": "spot_hidden", "amount": "4"}},
            ],
        }
        original = repo.transition_coc7_character_state
        calls = 0

        def fail_second(**values: object) -> dict:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected late batch failure")
            return original(**values)

        monkeypatch.setattr(repo, "transition_coc7_character_state", fail_second)
        with pytest.raises(RuntimeError, match="injected late batch failure"):
            RulesetEffectService(repo).apply_batch(
                batch,
                identity,
                campaign_id=campaign["id"],
                fallback_actor_id=pc["id"],
            )

        rolled_back = repo.get_campaign_investigator(
            campaign["id"], investigator["id"]
        )["campaign_state"]
        assert rolled_back["state_version"] == 0
        assert rolled_back["conditions"] == []
        assert repo.list_coc7_gameplay_events(
            campaign_id=campaign["id"], investigator_id=investigator["id"]
        ) == []

        monkeypatch.setattr(repo, "transition_coc7_character_state", original)
        retried = RulesetEffectService(repo).apply_batch(
            batch,
            identity,
            campaign_id=campaign["id"],
            fallback_actor_id=pc["id"],
        )
        replay = RulesetEffectService(repo).apply_batch(
            batch,
            identity,
            campaign_id=campaign["id"],
            fallback_actor_id=pc["id"],
        )
        assert [item["event"]["result"]["new_value"] for item in retried] == [2, 54]
        assert all(item["idempotent_replay"] is True for item in replay)


def test_san_loss_effect_is_evented_and_idempotent(tmp_path: Path) -> None:
    with db_session(tmp_path / "ruleset-effect.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Ruleset effect")
        kp_bundle = SessionService(repo).create(campaign["id"])
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert identity is not None
        profile = repo.create_player_profile("Investigator")["profile"]
        sheet = {
            "ruleset_id": "coc7-keeper-cn-2002c",
            "identity": {"name": "林默", "occupation": "记者"},
            "characteristics": {"int": 70, "luck": 50},
            "derived": {"max_hp": 10, "initial_san": 50, "max_san": 99, "max_mp": 10},
            "skills": [],
        }
        investigator = repo.create_investigator(profile["id"], sheet, source_type="manual")
        pc = repo.create_pc(campaign["id"], "林默", {})
        connection.execute(
            """
            INSERT INTO campaign_investigators
              (campaign_id, investigator_id, owner_profile_id,
               submitted_revision_id, approved_revision_id, legacy_pc_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'approved')
            """,
            (
                campaign["id"],
                investigator["id"],
                profile["id"],
                investigator["current_revision_id"],
                investigator["current_revision_id"],
                pc["id"],
            ),
        )
        connection.execute(
            """
            INSERT INTO investigator_campaign_state
              (campaign_id, investigator_id, approved_revision_id,
               current_hp, current_san, current_mp, current_luck,
               conditions_json, inventory_delta_json, state_version)
            VALUES (?, ?, ?, 10, 50, 10, 50, '[]', '{}', 0)
            """,
            (campaign["id"], investigator["id"], investigator["current_revision_id"]),
        )
        batch = {
            "id": "scenario-batch-1",
            "commands": [
                {
                    "kind": "apply_ruleset_effect",
                    "event_type": "san_loss",
                    "payload": {"loss": "4"},
                }
            ],
        }
        first = RulesetEffectService(repo).apply_batch(
            batch,
            identity,
            campaign_id=campaign["id"],
            fallback_actor_id=pc["id"],
        )
        replay = RulesetEffectService(repo).apply_batch(
            batch,
            identity,
            campaign_id=campaign["id"],
            fallback_actor_id=pc["id"],
        )

        assert first[0]["event"]["result"]["loss"] == 4
        assert replay[0]["idempotent_replay"] is True
        state = repo.get_campaign_investigator(campaign["id"], investigator["id"])[
            "campaign_state"
        ]
        assert state["current_san"] == 46
        events = repo.list_coc7_gameplay_events(
            campaign_id=campaign["id"], investigator_id=investigator["id"]
        )
        assert len(events) == 1
        assert events[0]["command_id"] == "kernel-effect:scenario-batch-1:0"


def test_failed_character_effect_rolls_back_the_scenario_batch(tmp_path: Path) -> None:
    with db_session(tmp_path / "ruleset-effect-rollback.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Atomic effect")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Player")
        identity = repo.authenticate_access_token(kp_bundle["access_token"])
        player_identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert identity is not None and player_identity is not None
        module = create_module(repo, campaign["id"])
        service = ScenarioContractService(repo)
        compilation, draft = service.compile_draft(
            module["id"],
            bind_payload_to_module(repo, module["id"], source_bound_payload({
                "contract_id": "atomic-effect",
                "source_version": 1,
                "ruleset_id": "coc7",
                "title": "Atomic effect",
                    "operators": [
                    {
                        "operator_id": "witness-horror",
                        "title": "Witness horror",
                        "policy": "automatic",
                        "success_commands": [
                            {"kind": "set_fact", "path": "horror.seen", "value": True},
                            {
                                "kind": "apply_ruleset_effect",
                                "event_type": "san_loss",
                                "payload": {"loss": "1"},
                            },
                        ],
                        }
                    ],
                    "endings": [{
                        "ending_id": "horror-witnessed",
                        "title": "Horror witnessed",
                        "all_conditions": [{
                            "path": "facts.horror.seen",
                            "operator": "eq",
                            "value": True,
                        }],
                    }],
                }, source_block_id="ruleset-effect-source")),
            created_by_member_id=identity.member_id,
        )
        assert compilation.report.valid is True
        assert draft is not None
        published = service.publish(
            draft["id"],
            expected_row_version=draft["row_version"],
            published_by_member_id=identity.member_id,
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=identity.member_id,
        )
        service.bind_run(run["id"], published["id"])
        action = TurnService(repo).submit_player_action(
            player_identity,
            action_text="Look at the horror",
            client_action_id="atomic-effect-player-action",
        )
        proposal, preview = KernelActionService(repo).prepare_manual(
            action,
            identity,
            operator_id="witness-horror",
            requested_skill_key=None,
        )
        assert preview.success_commands[1].actor_id == player_identity.member_id

        with pytest.raises(ValueError, match="approved investigator"):
            KernelActionService(repo).commit(
                proposal, outcome="success", identity=identity
            )

        restored = repo.get_scenario_run_state(run["id"])
        assert restored["state_version"] == 0
        assert restored["snapshot"].facts == {}
        assert repo.list_scenario_command_batches(run["id"]) == []

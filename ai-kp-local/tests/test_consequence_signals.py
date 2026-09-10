from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.consequence_signal_service import ConsequenceSignalService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.application.session_service import SessionService
from ai_kp.bootstrap.settings import Settings
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.platform.resolution.consequence_signals import ConsequenceSignalProjector
from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from tests.scenario_contract_testkit import bind_payload_to_module, source_bound_payload
from tests.test_scenario_contract_service import create_module


def signal_contract() -> ScenarioContract:
    return ScenarioContract.model_validate(
        source_bound_payload({
            "contract_id": "generic-consequence-signals",
            "source_version": 1,
            "ruleset_id": "coc7",
            "title": "Generic consequence signals",
            "initial_facts": {
                "weather": {"state": "calm"},
                "rival": {"phase": "preparing"},
            },
            "clocks": [
                {
                    "clock_id": "public_attention",
                    "title": "Public attention",
                    "maximum_value": 4,
                }
            ],
            "resources": [
                {
                    "resource_id": "expedition_supplies",
                    "title": "Expedition supplies",
                    "initial_value": 3,
                    "minimum_value": 0,
                    "maximum_value": 5,
                }
            ],
            "operators": [
                {
                    "operator_id": "take-noticeable-action",
                    "title": "Take a noticeable action",
                    "policy": "automatic",
                    "success_commands": [
                        {
                            "kind": "advance_clock",
                            "clock_id": "public_attention",
                            "delta": 2,
                        }
                    ],
                }
            ],
            "endings": [{
                "ending_id": "attention-raised",
                "title": "Attention raised",
                "all_conditions": [{
                    "path": "clocks.public_attention",
                    "operator": "gte",
                    "value": 2,
                }],
            }],
            "consequence_signals": [
                {
                    "signal_id": "attention_from_specific_hidden_faction",
                    "title": "Hidden faction attention",
                    "visibility": "table",
                    "display_mode": "stage",
                    "source_path": "clocks.public_attention",
                    "public_title": "Unwanted attention",
                    "public_summary": "People have started to notice the investigation.",
                    "bands": [
                        {
                            "band_id": "quiet",
                            "priority": 0,
                            "player_visible": False,
                        },
                        {
                            "band_id": "noticed",
                            "priority": 10,
                            "severity": 2,
                            "all_conditions": [
                                {
                                    "path": "clocks.public_attention",
                                    "operator": "gte",
                                    "value": 2,
                                }
                            ],
                            "player_visible": True,
                            "public_label": "You are being noticed",
                            "public_description": "Questions now draw visible attention.",
                        },
                    ],
                },
                {
                    "signal_id": "shared_supplies",
                    "title": "Exact expedition supplies",
                    "visibility": "table",
                    "display_mode": "exact",
                    "source_path": "resources.expedition_supplies",
                    "public_title": "Shared supplies",
                    "bands": [
                        {
                            "band_id": "available",
                            "player_visible": True,
                            "public_label": "Available",
                        }
                    ],
                },
                {
                    "signal_id": "secret_rival_schedule",
                    "title": "Rival investigators are preparing to depart",
                    "visibility": "kp",
                    "display_mode": "narrative",
                    "source_path": "facts.rival.phase",
                    "bands": [
                        {
                            "band_id": "preparing",
                            "all_conditions": [
                                {
                                    "path": "facts.rival.phase",
                                    "operator": "eq",
                                    "value": "preparing",
                                }
                            ],
                        }
                    ],
                },
                {
                    "signal_id": "coastal_weather",
                    "title": "Coastal weather state",
                    "visibility": "table",
                    "display_mode": "narrative",
                    "public_title": "Weather",
                    "bands": [
                        {
                            "band_id": "calm",
                            "player_visible": True,
                            "public_label": "Calm for now",
                            "public_description": "The weather is not obstructing travel.",
                            "all_conditions": [
                                {
                                    "path": "facts.weather.state",
                                    "operator": "eq",
                                    "value": "calm",
                                }
                            ],
                        }
                    ],
                },
                {
                    "signal_id": "unsafe_structured_exact_value",
                    "title": "Structured weather diagnostics",
                    "visibility": "table",
                    "display_mode": "exact",
                    "source_path": "facts.weather",
                    "public_title": "Unsafe exact projection",
                    "bands": [
                        {
                            "band_id": "default",
                            "player_visible": True,
                            "public_label": "Available",
                        }
                    ],
                },
            ],
        }, source_block_id="consequence-signal-source")
    )


def test_table_projection_is_generic_and_removes_secret_structure() -> None:
    contract = signal_contract()
    snapshot = contract.initial_snapshot("run-signals")
    projector = ConsequenceSignalProjector(contract)

    initial = projector.project(snapshot, audience="table")

    assert {item.title for item in initial} == {"Shared supplies", "Weather"}
    supplies = next(item for item in initial if item.title == "Shared supplies")
    assert supplies.current_value == 3
    assert supplies.source_path is None
    assert supplies.band_id is None
    assert all("secret" not in item.signal_id for item in initial)

    noticed_snapshot = snapshot.model_copy(
        update={"clocks": {"public_attention": 2}}
    )
    noticed = projector.project(noticed_snapshot, audience="table")
    attention = next(item for item in noticed if item.title == "Unwanted attention")
    assert attention.label == "You are being noticed"
    assert attention.current_value is None


def test_kp_projection_keeps_exact_authoritative_diagnostics() -> None:
    contract = signal_contract()
    snapshot = contract.initial_snapshot("run-signals")

    projected = ConsequenceSignalProjector(contract).project(snapshot, audience="kp")

    assert len(projected) == 5
    rival = next(item for item in projected if item.signal_id == "secret_rival_schedule")
    assert rival.title == "Rival investigators are preparing to depart"
    assert rival.source_path == "facts.rival.phase"
    assert rival.current_value == "preparing"
    assert rival.band_id == "preparing"


def test_signal_sources_are_compiler_checked() -> None:
    contract = signal_contract()
    broken = contract.model_copy(
        update={
            "consequence_signals": (
                contract.consequence_signals[0].model_copy(
                    update={"source_path": "facts.nonexistent.secret"}
                ),
                *contract.consequence_signals[1:],
            )
        }
    )

    result = ScenarioContractCompiler().compile(broken)

    assert result.report.valid is False
    assert any(
        item.code == "signal_source_unproduced" for item in result.report.issues
    )


def test_table_signal_cannot_expose_runtime_metadata_even_when_path_exists() -> None:
    contract = signal_contract()
    unsafe = contract.consequence_signals[1].model_dump(mode="python")
    unsafe["source_path"] = "run_version"
    payload = contract.model_dump(mode="python")
    payload["consequence_signals"] = [unsafe]

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert any(
        item.code == "schema_validation"
        and "private runtime metadata" in item.message
        for item in result.report.issues
    )


def test_table_service_events_hide_authoritative_versions_and_identifiers() -> None:
    contract = signal_contract()
    initial = contract.initial_snapshot("run-signals")
    baseline = initial.model_copy(update={"run_version": 6})
    noticed = initial.model_copy(
        update={"run_version": 7, "clocks": {"public_attention": 2}}
    )

    class SignalRepo:
        def get_active_campaign_module_run(self, campaign_id: str):
            assert campaign_id == "campaign-signals"
            return {"id": "run-signals"}

        def get_module_run_contract_binding(self, run_id: str):
            assert run_id == "run-signals"
            return {"contract": contract}

        def initialize_scenario_run_state(self, run_id: str):
            return {"state_version": 7, "snapshot": noticed}

        def list_recent_scenario_command_batches(self, run_id: str, *, limit: int):
            assert limit == 21
            return [
                {
                    "id": "earlier-secret-command-batch-id",
                    "result_version": 6,
                    "snapshot": baseline,
                    "created_at": "2026-08-11T19:00:00Z",
                },
                {
                    "id": "secret-command-batch-id",
                    "result_version": 7,
                    "snapshot": noticed,
                    "created_at": "2026-08-11T20:00:00Z",
                }
            ]

    table = ConsequenceSignalService(SignalRepo()).campaign_view(
        "campaign-signals", audience="table"
    )
    kp = ConsequenceSignalService(SignalRepo()).campaign_view(
        "campaign-signals", audience="kp"
    )

    assert table["state_version"] is None
    assert table["events"][0]["result_version"] == 1
    assert table["events"][0]["batch_id"].startswith("signal-event-")
    assert "secret-command-batch-id" not in str(table)
    assert "attention_from_specific_hidden_faction" not in str(table)
    assert kp["state_version"] == 7
    assert kp["events"][0]["batch_id"] == "secret-command-batch-id"


def test_http_endpoint_returns_distinct_player_and_kp_projections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "signal-api.sqlite3"
    with db_session(database) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Signal API campaign")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(
            kp_bundle["join_code"], display_name="Signal player"
        )
        kp = repo.authenticate_access_token(kp_bundle["access_token"])
        assert kp is not None
        module = create_module(repo, campaign["id"])
        contracts = ScenarioContractService(repo)
        _, draft = contracts.compile_draft(
            module["id"],
            bind_payload_to_module(
                repo,
                module["id"],
                signal_contract().model_dump(mode="json"),
            ),
            created_by_member_id=kp.member_id,
        )
        assert draft is not None
        published = contracts.publish(
            draft["id"],
            expected_row_version=1,
            published_by_member_id=kp.member_id,
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=kp.member_id,
        )
        contracts.bind_run(run["id"], published["id"])
        assert connection.execute(
            "SELECT COUNT(*) FROM scenario_run_states WHERE run_id = ?", (run["id"],)
        ).fetchone()[0] == 0

    settings = Settings(
        db_path=database,
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
        admin_token="signal-admin",
    )
    with TestClient(create_app(settings)) as client:
        player_response = client.get(
            f"/campaigns/{campaign['id']}/consequence-signals",
            headers={"Authorization": f"Bearer {player_bundle['access_token']}"},
        )
        kp_response = client.get(
            f"/campaigns/{campaign['id']}/consequence-signals",
            headers={"Authorization": f"Bearer {kp_bundle['access_token']}"},
        )

    assert player_response.status_code == 200
    assert player_response.json()["state_version"] is None
    assert "secret_rival_schedule" not in player_response.text
    assert "facts.rival.phase" not in player_response.text
    assert {item["title"] for item in player_response.json()["signals"]} == {
        "Shared supplies",
        "Weather",
    }
    assert kp_response.status_code == 200
    assert kp_response.json()["state_version"] == 0
    assert "secret_rival_schedule" in kp_response.text
    assert "facts.rival.phase" in kp_response.text
    with db_session(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM scenario_run_states WHERE run_id = ?", (run["id"],)
        ).fetchone()[0] == 1

from __future__ import annotations

from ai_kp.platform.resolution.reactive import ReactiveEvent, ReactivePolicyEngine
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from tests.test_bounded_planning import delegation_payload


def reactive_contract():
    payload = delegation_payload()
    payload["entities"] = [
        {
            "entity_id": "threat",
            "entity_type": "creature",
            "title": "Pursuing threat",
            "initial_status": "dormant",
        }
    ]
    payload["reactive_policies"] = [
        {
            "policy_id": "threat-escalation",
            "entity_id": "threat",
            "rules": [
                {
                    "rule_id": "escalate",
                    "trigger": "clock_advanced",
                    "priority": 100,
                    "conditions": [
                        {"path": "clocks.elapsed", "operator": "gte", "value": 3},
                        {
                            "path": "facts.threat.escalated",
                            "operator": "not_exists",
                        },
                    ],
                    "commands": [
                        {
                            "kind": "set_entity_status",
                            "entity_id": "threat",
                            "value": "hunting",
                        },
                        {
                            "kind": "set_fact",
                            "path": "threat.escalated",
                            "value": True,
                        },
                    ],
                    "rationale": "The threat acts when its clock threshold is reached.",
                },
                {
                    "rule_id": "notice",
                    "trigger": "clock_advanced",
                    "priority": 10,
                    "conditions": [
                        {"path": "clocks.elapsed", "operator": "gte", "value": 1},
                        {
                            "path": "entities.threat",
                            "operator": "eq",
                            "value": "dormant",
                        },
                    ],
                    "commands": [
                        {
                            "kind": "set_entity_status",
                            "entity_id": "threat",
                            "value": "alert",
                        }
                    ],
                },
            ],
        }
    ]
    result = ScenarioContractCompiler().compile(payload)
    assert result.report.valid is True
    assert result.contract is not None
    return result.contract


def test_reactive_priority_selector_executes_one_rule_per_entity() -> None:
    contract = reactive_contract()
    snapshot = contract.initial_snapshot("run-1").model_copy(
        update={"clocks": {"elapsed": 3}}
    )
    engine = ReactivePolicyEngine(contract)
    event = ReactiveEvent(event_id="event-1", trigger="clock_advanced")

    decision = engine.evaluate(snapshot, event)
    repeated = engine.evaluate(snapshot, event)

    assert [item.rule_id for item in decision.activations] == ["escalate"]
    assert decision.resulting_snapshot.entities["threat"] == "hunting"
    assert decision.resulting_snapshot.facts["threat"]["escalated"] is True
    assert decision.resulting_snapshot.run_version == snapshot.run_version + 1
    assert decision.decision_hash == repeated.decision_hash


def test_reactive_engine_is_quiet_when_no_triggered_rule_matches() -> None:
    contract = reactive_contract()
    snapshot = contract.initial_snapshot("run-1")

    decision = ReactivePolicyEngine(contract).evaluate(
        snapshot, ReactiveEvent(event_id="event-1", trigger="background_tick")
    )

    assert decision.activations == ()
    assert decision.resulting_snapshot == snapshot

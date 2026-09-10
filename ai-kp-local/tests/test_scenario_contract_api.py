from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings


class SourceAwareScenarioLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        if "本批待审核契约记录：" in prompt:
            return json.dumps({
                "decision": "approve",
                "findings": [],
                "supported_assumption_indices": [],
            })
        if "修复目标：" in prompt:
            target = json.loads(
                prompt.split("修复目标：", 1)[1].split("\n最近校验错误：", 1)[0]
            )["targets"][0]
            repaired = dict(target["original_record"])
            repaired["policy"] = "automatic"
            return json.dumps({
                "replacements": [{
                    "group": target["group"],
                    "record_index": target["record_index"],
                    "record": repaired,
                }]
            }, ensure_ascii=False)
        catalog_text = prompt.split("证据目录：", 1)[1].split("\n返回", 1)[0]
        source = json.loads(catalog_text)[0]
        return json.dumps(
            {
                "confidence": "high",
                "assumptions": [],
                "initial_scene_id": "station-room",
                "locations": [
                    {
                        "id": "station-room",
                        "title": "车站候车室",
                        "visibility": "visited",
                        "source_block_ids": [source["source_block_id"]],
                    }
                ],
                "actions": [
                    {
                        "id": "inspect-seat",
                        "title": "检查座椅下方",
                        "location_slot": 0,
                        "intent_hints": ["检查座椅", "寻找旧车票"],
                        "policy": "invalid-model-policy",
                        "on_success": [
                            {
                                "kind": "set_fact",
                                "path": "station.old_ticket_seen",
                                "value": True,
                            }
                        ],
                        "source_block_ids": [source["source_block_id"]],
                    }
                ],
                "clues": [{
                    "id": "old-ticket",
                    "title": "座椅下的旧车票",
                    "importance": "core",
                    "discovery_action_ids": ["inspect-seat"],
                    "fact_path": "station.old_ticket_seen",
                    "fact_value": True,
                    "recoverable": True,
                    "public_content": ["旧车票上记录了本章需要确认的车次与日期。"],
                    "source_block_ids": [source["source_block_id"]],
                }],
                "endings": [{
                    "id": "ticket-found",
                    "title": "找到旧车票",
                    "all_conditions": [{
                        "path": "facts.station.old_ticket_seen",
                        "operator": "eq",
                        "value": True,
                    }],
                    "source_block_ids": [source["source_block_id"]],
                }],
            },
            ensure_ascii=False,
        )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_kp_can_generate_publish_list_and_bind_an_executable_contract(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "scenario-contract-api.sqlite3",
        llm_base_url="http://unused.local/v1",
        llm_api_key="test",
        llm_model="source-aware-model",
        admin_token="scenario-contract-admin",
    )
    app = create_app(settings)
    with patch(
        "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
        return_value=SourceAwareScenarioLlm(),
    ), TestClient(
        app, headers={"X-AI-KP-Admin-Token": "scenario-contract-admin"}
    ) as client:
        campaign = client.post("/campaigns", json={"title": "Contract API"}).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            json={"kp_display_name": "KP"},
        ).json()
        kp_headers = _headers(session["access_token"])
        module = client.post(
            f"/campaigns/{campaign['id']}/modules",
            headers=kp_headers,
            json={
                "title": "通用车站调查",
                    "text": "# 车站候车室\n\n座椅下方放着一张记有车次和日期的旧车票；找到它后本章结束。",
                "source_type": "plaintext",
                "default_visibility": "kp",
            },
        ).json()
        run = client.post(
            f"/campaigns/{campaign['id']}/module-runs",
            headers=kp_headers,
            json={"module_id": module["id"], "current_scene_key": "station-room"},
        ).json()
        run = client.post(
            f"/module-runs/{run['id']}/automation",
            headers=kp_headers,
            json={
                "expected_version": run["version"],
                "level": "ai_kp",
                "reason": "API contract generation test",
            },
        ).json()

        generated = client.post(
            f"/modules/{module['id']}/scenario-contracts/generate",
            headers=kp_headers,
            json={"ruleset_id": "coc7"},
        )

        assert generated.status_code == 202, generated.text
        job_id = generated.json()["id"]
        body = None
        for _ in range(100):
            job = client.get(
                f"/scenario-contract-jobs/{job_id}", headers=kp_headers
            ).json()
            if job["status"] in {"succeeded", "failed"}:
                body = job["result"]
                assert job["status"] == "succeeded", job
                break
            time.sleep(0.01)
        assert body is not None
        assert body["auto_published"] is True
        assert body["authoring"]["review"] == {
            "decision": "approve",
            "review_kind": "independent_ai",
            "findings": [],
            "issues": [],
            "unsupported_assumption_indices": [],
            "assumptions_resolved": True,
        }
        assert body["authoring"]["partition_count"] == 1
        assert body["authoring"]["completed_partition_count"] == 1
        assert body["authoring"]["repair_diagnostics"][0]["status"] == "applied"
        assert body["authoring"]["repair_diagnostics"][0]["group"] == "actions"
        assert body["version"]["status"] == "published"
        assert body["corpus_truncated"] is False
        assert body["model"] == "source-aware-model"
        assert body["binding"]["run_id"] == run["id"]
        version_id = body["version"]["id"]

        listed = client.get(
            f"/modules/{module['id']}/scenario-contracts",
            headers=kp_headers,
        )
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == version_id
        assert listed.json()[0]["contract"]["operators"][0]["operator_id"] == (
            "inspect-seat"
        )

        bound = client.post(
            f"/module-runs/{run['id']}/scenario-contract-binding",
            headers=kp_headers,
            json={"contract_version_id": version_id},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["contract_version_id"] == version_id


def test_player_cannot_access_scenario_contract_authoring(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "scenario-contract-auth.sqlite3",
        admin_token="scenario-contract-auth-admin",
    )
    app = create_app(settings)
    with TestClient(
        app,
        headers={"X-AI-KP-Admin-Token": "scenario-contract-auth-admin"},
    ) as client:
        campaign = client.post("/campaigns", json={"title": "Contract auth"}).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            json={"kp_display_name": "KP"},
        ).json()
        kp_headers = _headers(session["access_token"])
        module = client.post(
            f"/campaigns/{campaign['id']}/modules",
            headers=kp_headers,
            json={
                "title": "Private module",
                "text": "Secret source.",
                "default_visibility": "kp",
            },
        ).json()
        player = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "Player"},
        ).json()
        player_headers = _headers(player["access_token"])

        listed = client.get(
            f"/modules/{module['id']}/scenario-contracts",
            headers=player_headers,
        )
        generated = client.post(
            f"/modules/{module['id']}/scenario-contracts/generate",
            headers=player_headers,
            json={"ruleset_id": "coc7"},
        )
        jobs = client.get(
            f"/modules/{module['id']}/scenario-contract-jobs",
            headers=player_headers,
        )

        assert listed.status_code == 403
        assert generated.status_code == 403
        assert jobs.status_code == 403

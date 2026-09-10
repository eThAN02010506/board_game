"""Run a reproducible four-player parallel round against a real small model."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from ai_kp.application.module_run_service import AutomationLevelCommand, ModuleRunService
from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.application.parallel_kernel_service import ParallelKernelService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.director.orchestrator import KpOrchestrator
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.llm.call_registry import CampaignAiCallRegistry
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.modules.ingestion import ModuleChunk
from ai_kp.platform.ports.llm import ChatMessage, LlmClient


class RecordingLlm:
    """Keep bounded structured traces so weak-model failures are reproducible."""

    def __init__(self, inner: LlmClient):
        self.inner = inner
        self.traces: list[dict[str, object]] = []

    async def complete(
        self, messages: list[ChatMessage], temperature: float = 0.7
    ) -> str:
        response = await self.inner.complete(messages, temperature=temperature)
        self.traces.append(
            {
                "system": messages[0].content[:500],
                "user": messages[-1].content[:1000],
                "response": response[:1000],
            }
        )
        return response


def _contract_payload(source_ref: dict[str, object]) -> dict[str, object]:
    return {
        "contract_id": "small-model-parallel-realcase",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "通用多人机关场景",
        "operators": [
            {
                "operator_id": "open-panel",
                "title": "打开机关面板",
                "intent_hints": ["打开面板", "掀开面板", "拉开机关面板"],
                "policy": "automatic",
                "success_commands": [
                    {
                        "kind": "set_fact",
                        "path": "realcase.panel_open",
                        "value": True,
                    }
                ],
                "source_refs": [source_ref],
            }
        ],
        "endings": [
            {
                "ending_id": "panel-opened",
                "title": "机关面板已经打开",
                "all_conditions": [
                    {
                        "path": "facts.realcase.panel_open",
                        "operator": "eq",
                        "value": True,
                    }
                ],
                "source_refs": [source_ref],
            }
        ],
    }


def _install_run(repo: Repository) -> dict:
    campaign = repo.create_campaign("Small-model four-player realcase")
    module = repo.create_module(
        str(campaign["id"]),
        "通用多人机关场景",
        [
            ModuleChunk(
                title="机关面板",
                text="调查员可以从任意一侧打开同一块机关面板。",
                visibility="kp",
                scene_key="mechanism-room",
                order_index=0,
            )
        ],
    )
    block = repo.list_module_chunks(
        str(module["id"]),
        allowed_visibility=("player", "table", "kp", "secret"),
        spoiler_tags=None,
    )[0]
    source_ref = {
        "source_block_id": str(block["id"]),
        "document_id": str(module.get("source_hash") or module["id"]),
    }
    service = ScenarioContractService(repo)
    _, draft = service.compile_draft(
        str(module["id"]),
        _contract_payload(source_ref),
        created_by_member_id=None,
    )
    if draft is None:
        raise RuntimeError("Realcase contract failed deterministic compilation")
    published = service.publish(
        str(draft["id"]),
        expected_row_version=int(draft["row_version"]),
        published_by_member_id=None,
    )
    run = repo.start_campaign_module_run(
        campaign_id=str(campaign["id"]),
        module_id=str(module["id"]),
        current_scene_key="mechanism-room",
        active_spoiler_tags=[],
        state={},
        started_by_member_id=None,
    )
    service.bind_run(str(run["id"]), str(published["id"]))
    ParallelKernelService(repo).initialize_run(str(run["id"]))
    return run


async def run_realcase(args: argparse.Namespace) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmpdir:
        connection = connect(Path(tmpdir) / "parallel-small-model.sqlite3")
        try:
            init_db(connection)
            repo = Repository(connection)
            run = _install_run(repo)
            session = SessionService(repo).create(str(run["campaign_id"]))
            kp = repo.authenticate_access_token(str(session["access_token"]))
            if kp is None:
                raise RuntimeError("Realcase KP identity was not created")
            action_texts = (
                "我伸手把墙上的机关面板打开。",
                "我从右侧抓住面板边缘，把它掀开。",
                "我按下卡扣并打开那块机关面板。",
                "我帮同伴将已经松动的机关面板完全拉开。",
            )
            identities = []
            actions = []
            for index, action_text in enumerate(action_texts, 1):
                bundle = SessionService(repo).join(
                    str(session["join_code"]), display_name=f"Real Player {index}"
                )
                identity = repo.authenticate_access_token(str(bundle["access_token"]))
                if identity is None:
                    raise RuntimeError("Realcase player identity was not created")
                identities.append(identity)
                actions.append(
                    TurnService(repo).submit_player_action(
                        identity,
                        action_text=action_text,
                        client_action_id=f"small-model-four-player-{index}",
                    )
                )
            current_run = repo.get_campaign_module_run(str(run["id"]))
            ModuleRunService(repo).set_automation_level(
                str(run["id"]),
                command=AutomationLevelCommand(
                    expected_version=int(current_run["version"]),
                    level="ai_kp",
                    reason="real four-player small-model validation",
                ),
                member_id=kp.member_id,
            )
            connection.commit()
            llm = RecordingLlm(
                OpenAICompatibleClient(
                    args.base_url,
                    args.api_key,
                    args.model,
                    timeout_seconds=args.timeout,
                    max_tokens=args.max_tokens,
                )
            )
            director = KpOrchestrator(
                connection,
                llm,
                call_registry=CampaignAiCallRegistry(),
            )
            workflow = ParallelActionWorkflowService(repo)
            prepared = await workflow.prepare(
                tuple(actions),
                kp,
                director,
                idempotency_key="realcase:small-model:four-player:v1",
                source_model=args.model,
                profile="small",
            )
            if prepared.batch is None:
                raise RuntimeError(
                    json.dumps(
                        {"result": prepared.as_dict(), "model_traces": llm.traces},
                        ensure_ascii=False,
                    )
                )
            batch = prepared.batch
            selections = [
                {
                    "action": action_text,
                    "operator": item["operator_id"],
                    "mode": item["adjudication"]["mode"],
                }
                for action_text, item in zip(action_texts, batch["items"], strict=True)
            ]
            identities_by_action = {
                str(action["id"]): identity
                for action, identity in zip(actions, identities, strict=True)
            }
            result = None
            for action in actions:
                batch = repo.get_parallel_action_batch(str(batch["id"]))
                item = next(
                    candidate
                    for candidate in batch["items"]
                    if candidate["action_id"] == action["id"]
                )
                result = workflow.confirm_item(
                    str(batch["id"]),
                    str(action["id"]),
                    expected_batch_version=int(batch["version"]),
                    expected_adjudication_version=int(
                        item["adjudication"]["version"]
                    ),
                    selected_skill_key=None,
                    identity=identities_by_action[str(action["id"])],
                )
            if result is None or result.batch is None:
                raise RuntimeError("Realcase confirmation barrier did not complete")
            if result.status == "ready":
                result = workflow.commit(
                    str(result.batch["id"]),
                    expected_version=int(result.batch["version"]),
                )
            output = {
                "model": args.model,
                "participants": len(actions),
                "planning_status": prepared.status,
                "selections": selections,
                "final_status": result.status,
                "batch_status": result.batch["status"] if result.batch else None,
                "state_version": repo.get_scenario_run_state(str(run["id"]))[
                    "state_version"
                ],
            }
            if output["final_status"] != "settled":
                raise RuntimeError(json.dumps(output, ensure_ascii=False))
            return output
        finally:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://192.168.1.97:8001/v1")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="gpt-oss-20b")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_realcase(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

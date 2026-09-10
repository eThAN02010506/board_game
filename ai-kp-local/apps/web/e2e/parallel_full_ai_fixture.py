"""Deterministic setup for the multiplayer browser acceptance test.

The browser test deliberately keeps this fixture outside the product runtime.
It replaces only model interpretation during test setup; player consent, checks,
queueing, the atomic kernel commit, and public projections all use production
services and HTTP routes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.application.parallel_kernel_service import ParallelKernelService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.resolution.semantic_adapter import (
    SemanticCandidate,
    SemanticSelection,
    SemanticSelectionResult,
)
from ai_kp.platform.resolution.tabletop_turn import (
    TabletopTurnFrame,
    TabletopTurnInterpretation,
)
from ai_kp.platform.sessions.models import AuthenticatedMember
from tests.scenario_contract_testkit import bind_payload_to_module
from tests.test_parallel_action_kernel import parallel_contract


class DeterministicParallelDirector:
    """Select fixture operators explicitly without making a model call."""

    def __init__(self, selections: dict[str, tuple[str, str | None]]):
        self.selections = selections

    async def interpret_tabletop_turn(
        self,
        *,
        campaign_id: str,
        contract: Any,
        snapshot: Any,
        player_action: str,
    ) -> TabletopTurnInterpretation:
        del campaign_id, contract, snapshot
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
        self,
        *,
        campaign_id: str,
        contract: Any,
        snapshot: Any,
        player_action: str,
        profile: str,
    ) -> SemanticSelectionResult:
        del campaign_id, snapshot, profile
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

    async def narrate_kernel_action(self, **kwargs: Any) -> Any:
        raise AssertionError(
            "The small-profile browser fixture must use deterministic narration"
        )


def _install_contract(args: argparse.Namespace) -> None:
    connection = connect(Path(args.db_path))
    try:
        init_db(connection)
        repo = Repository(connection)
        payload = parallel_contract().model_dump(mode="json")
        operators = {
            item["operator_id"]: item for item in payload["operators"]
        }
        operators["find-mark"].update(
            {
                "public_setup": "你辨认墙面上几乎看不见的暗记，结果要等检定后才能确定。",
                "narrative_cues": [
                    {
                        "outcome_key": "success",
                        "public_summary": "暗记的走向被确认，它确实指向机关面板的内部结构。",
                    },
                    {
                        "outcome_key": "failure",
                        "public_summary": "暗记仍无法被可靠辨认，本次尝试没有得到结论。",
                    },
                ],
            }
        )
        operators["open-panel"].update(
            {
                "public_setup": "你从另一侧扶住机关面板，等待同伴的观察一起结算。",
                "narrative_cues": [
                    {
                        "outcome_key": "success",
                        "public_summary": "机关面板被平稳打开，内部结构完整显露出来。",
                    }
                ],
            }
        )
        bound_payload = bind_payload_to_module(repo, args.module_id, payload)
        service = ScenarioContractService(repo)
        _, draft = service.compile_draft(
            args.module_id,
            bound_payload,
            created_by_member_id=args.kp_member_id,
        )
        if draft is None:
            raise RuntimeError("The generic parallel fixture did not compile")
        published = service.publish(
            str(draft["id"]),
            expected_row_version=int(draft["row_version"]),
            published_by_member_id=args.kp_member_id,
        )
        service.bind_run(args.run_id, str(published["id"]))
        ParallelKernelService(repo).initialize_run(args.run_id)
        connection.commit()
        print(json.dumps({"contract_version_id": published["id"]}))
    finally:
        connection.close()


async def _prepare_batch(args: argparse.Namespace) -> None:
    connection = connect(Path(args.db_path))
    try:
        init_db(connection)
        repo = Repository(connection)
        connection.commit()
        action_ids = tuple(json.loads(args.action_ids))
        selections = {
            args.check_action_text: ("find-mark", "spot_hidden"),
            args.direct_action_text: ("open-panel", None),
        }

        # The two UI submissions intentionally leave their normal delayed prepare
        # jobs behind.  This fixture supplies the model-independent preparation,
        # so cancel only those pre-commit jobs before the worker can claim them.
        for job in repo.list_auto_kp_jobs(args.campaign_id, limit=100):
            payload = dict(job.get("payload") or {})
            is_prepare = job["job_type"] == "player_action" or (
                job["job_type"] == "parallel_actions"
                and payload.get("phase", "prepare") == "prepare"
            )
            if job["status"] in {"queued", "retry_wait"} and is_prepare:
                repo.cancel_auto_kp_job(str(job["id"]))
        connection.commit()

        identity = AuthenticatedMember(
            member_id=args.kp_member_id,
            session_id=args.session_id,
            campaign_id=args.campaign_id,
            role="kp",
            display_name="Browser fixture KP",
            pc_id=None,
        )
        actions = tuple(repo.get_player_action(action_id) for action_id in action_ids)
        result = await ParallelActionWorkflowService(repo).prepare(
            actions,
            identity,
            DeterministicParallelDirector(selections),
            idempotency_key=f"playwright-parallel:{args.fixture_key}",
            source_model="fixture:deterministic",
            profile="small",
        )
        connection.commit()
        if result.status != "awaiting_confirmation" or result.batch is None:
            raise RuntimeError(f"Parallel fixture preparation failed: {result.message}")
        print(
            json.dumps(
                {"batch_id": result.batch["id"], "status": result.status},
                ensure_ascii=False,
            )
        )
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("install", "prepare"))
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--kp-member-id", required=True)
    parser.add_argument("--module-id")
    parser.add_argument("--run-id")
    parser.add_argument("--session-id")
    parser.add_argument("--action-ids")
    parser.add_argument("--check-action-text")
    parser.add_argument("--direct-action-text")
    parser.add_argument("--fixture-key")
    args = parser.parse_args()
    if args.mode == "install":
        if not args.module_id or not args.run_id:
            parser.error("install requires --module-id and --run-id")
        _install_contract(args)
        return
    required = (
        args.session_id,
        args.action_ids,
        args.check_action_text,
        args.direct_action_text,
        args.fixture_key,
    )
    if not all(required):
        parser.error("prepare requires session, actions, texts, and fixture key")
    asyncio.run(_prepare_batch(args))


if __name__ == "__main__":
    main()

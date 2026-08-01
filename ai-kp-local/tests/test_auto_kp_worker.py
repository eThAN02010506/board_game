import json
from pathlib import Path

from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.context_builder import ContextAssembly
from ai_kp.director.orchestrator import KpTurnResult
from ai_kp.director.turn_output import KpTurnOutput
from ai_kp.infrastructure import auto_kp_worker
from ai_kp.infrastructure.auto_kp_worker import process_claimed_auto_kp_job
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db


class FakeDirector:
    async def handle_player_action(self, **kwargs):
        return KpTurnResult(
            output=KpTurnOutput.model_validate(
                {
                    "public_narration": "自动 KP worker 推进了行动。",
                    "kp_notes": "fake worker",
                    "action_ruling": {
                        "goal": kwargs["player_action"],
                        "method": "worker fake",
                        "target": "当前场景",
                        "feasibility": "possible",
                        "resolution": "automatic",
                        "reason": "测试用稳定输出。",
                        "maximum_effect": "只写公开叙事。",
                        "alternative": "",
                    },
                    "proposed_checks": [],
                    "proposed_events": [],
                    "proposed_memories": [],
                    "proposed_npc_updates": [],
                    "proposed_map_moves": [],
                    "proposed_facts": [],
                }
            ),
            context=ContextAssembly(
                messages=[],
                included_sources=[],
                excluded_sources=[],
                token_estimate=0,
                visibility_scope="kp",
            ),
        )


def test_auto_kp_worker_processes_claimed_player_action_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-worker.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: FakeDirector(),
        )
        campaign = repo.create_campaign("Auto KP worker")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Ada")
        player_identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert player_identity is not None
        action = TurnService(repo).submit_player_action(
            player_identity,
            action_text="我检查窗户。",
            client_action_id="worker-action-0001",
        )
        repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            job_type="player_action",
            resource_id=action["id"],
            idempotency_key="worker-action-job-0001",
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="test-worker")
        assert claimed is not None
        connection.commit()

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_base_url="http://unused.invalid/v1",
                llm_model="fake",
            ),
        )

        saved = repo.get_auto_kp_job(claimed["id"])
        assert saved["status"] == "succeeded"
        assert saved["result"]["player_action"]["status"] == "resolved"
        assert repo.get_player_action(action["id"])["status"] == "resolved"
        event = connection.execute(
            "SELECT summary FROM events WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()
        assert "worker 推进" in event["summary"]
        assert json.dumps(saved["result"], ensure_ascii=False)
    finally:
        connection.close()

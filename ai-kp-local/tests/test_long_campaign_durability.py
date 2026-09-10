from ai_kp.evaluation.long_campaign_durability import LongCampaignDurabilityRunner


def test_twenty_session_state_scale_survives_restarts_and_index_rebuild(tmp_path) -> None:
    result = LongCampaignDurabilityRunner(tmp_path / "long-campaign.sqlite3").run()

    assert result["runner_version"] == "long-campaign-durability.v4"
    assert result["status"] == "passed"
    assert result["sessions"] == 20
    assert result["scripted_sessions"] == 10
    assert result["equivalent_table_minutes"] == 3000
    assert result["process_restarts"] == 20
    assert result["index_rebuild_passed"] is True
    assert result["model_switch_passed"] is True
    assert result["model_switch"] == {
        "session": 10,
        "from_model": "durability-model-a",
        "from_version": 1,
        "to_model": "durability-model-b",
        "to_version": 2,
        "authority_unchanged": True,
    }
    assert result["secret_leak_count"] == 0
    assert result["authority_fingerprint_stable"] is True
    assert result["counts"]["session_continuity_snapshots"] == 20
    assert result["counts"]["memories"] == 60
    assert result["counts"]["character_lifecycle_events"] == 8
    assert result["counts"]["character_lifecycle_requests"] == 6
    assert len(result["episodes"]) == 20
    assert len({item["episode_id"] for item in result["episodes"]}) == 20
    assert len({item["event_window_hash"] for item in result["episodes"]}) == 20
    assert result["episodes"][6]["milestone"] == "character_death"
    assert result["episodes"][9]["milestone"] == "long_term_recall"
    lifecycle = result["lifecycle"]
    assert lifecycle["passed"] is True
    assert lifecycle["member_count"] == 5
    assert lifecycle["late_join_count"] == 1
    assert lifecycle["action_counts"] == {
        "observe": 2,
        "replace": 2,
        "return": 1,
        "ruleset_state_sync": 2,
        "temporary_leave": 1,
    }
    assert len(lifecycle["dead_investigator_ids"]) == 2
    assert len(lifecycle["active_member_ids"]) == 5
    assert len(lifecycle["active_investigator_ids"]) == 5
    assert [item["action"] for item in lifecycle["evidence"]] == [
        "ruleset_state_sync",
        "observe",
        "replace",
        "temporary_leave",
        "return",
        "member_joined",
        "ruleset_state_sync",
        "observe",
        "replace",
    ]

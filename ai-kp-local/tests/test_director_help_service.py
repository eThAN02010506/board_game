from __future__ import annotations

import asyncio
import json

import pytest

from ai_kp.api.schemas import DirectorHelpResponse
from ai_kp.application.director_help_projection import (
    DIRECTOR_HELP_MODEL_REQUEST_JSON_BYTES,
)
from ai_kp.application.director_help_service import DirectorHelpService
from ai_kp.application.errors import ConflictError
from ai_kp.director.human_kp_help import DirectorHelpOutput
from ai_kp.platform.resolution.contracts import ScenarioContract


def _contract() -> ScenarioContract:
    source = {
        "source_block_id": "block-7",
        "document_id": "module-help",
        "page": 7,
    }
    return ScenarioContract.model_validate(
        {
            "contract_id": "help-contract",
            "source_version": 2,
            "ruleset_id": "coc7",
            "title": "Need Help fixture",
            "initial_scene_id": "study",
            "locations": [{"location_id": "study", "title": "书房", "source_refs": [source]}],
            "operators": [
                {
                    "operator_id": "search-desk",
                    "title": "搜索书桌",
                    "intent_hints": ["搜索书桌", "查看抽屉"],
                    "public_setup": "抽屉上积着灰尘。",
                    "policy": "required_check",
                    "skill_choices": [
                        {
                            "skill_key": "spot_hidden",
                            "reason": "找出夹层里的信件。",
                            "failure_stakes": "暂时没有找到夹层。",
                        }
                    ],
                    "success_commands": [
                        {
                            "kind": "set_fact",
                            "path": "facts.letter.found",
                            "value": True,
                        }
                    ],
                    "maximum_effect": "找到书桌内的信件，不推断寄信者身份。",
                    "source_refs": [source],
                }
            ],
        }
    )


def _large_contract() -> ScenarioContract:
    payload = _contract().model_dump(mode="json")
    operator = payload["operators"][0]
    operator["skill_choices"] = [
        {
            "skill_key": "spot_hidden" if index == 0 else f"skill_{index}",
            "reason": f"Candidate skill {index}",
            "failure_stakes": "No progress is made.",
        }
        for index in range(9)
    ]
    operator["success_commands"] = [
        {
            "kind": "set_fact",
            "path": f"facts.large.success_{index}",
            "value": True,
        }
        for index in range(33)
    ]
    operator["failure_commands"] = [
        {
            "kind": "set_fact",
            "path": f"facts.large.failure_{index}",
            "value": True,
        }
        for index in range(33)
    ]
    operator["source_refs"] = [
        {
            "source_block_id": f"block-{index}",
            "document_id": "module-help",
            "page": 7,
        }
        for index in range(17)
    ]
    return ScenarioContract.model_validate(payload)


def _extreme_contract() -> ScenarioContract:
    payload = _large_contract().model_dump(mode="json")
    operator = payload["operators"][0]
    long_detail = "深" * 20_000
    for choice in operator["skill_choices"]:
        choice["supporting_factors"] = [long_detail] * 8
        choice["automatic_information"] = [long_detail] * 8
        choice["failure_stakes"] = "险" * 1000
        choice["pushed_failure_stakes"] = "危" * 1000
    operator["automatic_information"] = [long_detail] * 12
    operator["maximum_effect"] = "界" * 1000
    operator["success_commands"] = [
        {
            "kind": "emit_event",
            "event_type": "extreme_success",
            "payload": {
                "description": long_detail,
                "nested": [
                    {"index": nested_index, "text": long_detail} for nested_index in range(20)
                ],
            },
        }
        for _index in range(33)
    ]
    operator["failure_commands"] = [
        {
            "kind": "apply_ruleset_effect",
            "event_type": "extreme_failure",
            "payload": {
                "description": long_detail,
                "nested": {f"field_{nested_index}": long_detail for nested_index in range(20)},
            },
        }
        for _index in range(33)
    ]
    return ScenarioContract.model_validate(payload)


class FakeDirectorHelpRepo:
    def __init__(self, *, mode: str = "human_kp", persisted_state: bool = False):
        self.contract = _contract()
        self.run = {
            "id": "run-help",
            "campaign_id": "campaign-help",
            "module_id": "module-help",
            "status": "active",
            "version": 3,
            "director_control_mode": mode,
            "director_control_reason": "测试",
            "active_spoiler_tags": [],
        }
        self.model_configuration = {"version": 4}
        self.state = (
            {
                "contract_version_id": "contract-version-help",
                "state_version": 0,
                "snapshot": self.contract.initial_snapshot("run-help"),
            }
            if persisted_state
            else None
        )

    def get_campaign_module_run(self, run_id: str) -> dict:
        assert run_id == self.run["id"]
        return dict(self.run)

    def get_active_campaign_module_run(self, campaign_id: str) -> dict | None:
        assert campaign_id == self.run["campaign_id"]
        return dict(self.run) if self.run["status"] == "active" else None

    def get_model_configuration(self) -> dict:
        return dict(self.model_configuration)

    def get_module_run_contract_binding(self, run_id: str) -> dict:
        assert run_id == self.run["id"]
        return {
            "run_id": run_id,
            "contract": self.contract,
            "contract_hash": "a" * 64,
            "contract_version_id": "contract-version-help",
        }

    def get_scenario_run_state(self, run_id: str) -> dict:
        assert run_id == self.run["id"]
        if self.state is None:
            raise KeyError("no state")
        return dict(self.state)

    def search_module(self, module_id: str, query: str, **_kwargs) -> list[dict]:
        assert module_id == self.run["module_id"]
        return [
            {
                "source_type": "chunk",
                "source_id": "chunk-7",
                "title": "书房",
                "text": "调查员可以检查书桌抽屉。",
                "source_locator": "page 7",
            }
        ]

    def find_rule_source(self, _ruleset_id: str) -> dict:
        raise KeyError("no rule source")

    def lexical_rule_chunks(self, *_args, **_kwargs) -> list[dict]:
        raise AssertionError("must not query chunks without a rule source")


class FakeHelpDirector:
    def __init__(self, repo: FakeDirectorHelpRepo, *, mutate_state: bool = False):
        self.repo = repo
        self.mutate_state = mutate_state
        self.calls = 0
        self.brief: dict | None = None

    async def advise_human_kp(self, *, campaign_id: str, brief: dict):
        assert campaign_id == "campaign-help"
        self.calls += 1
        self.brief = brief
        if self.mutate_state:
            assert self.repo.state is not None
            snapshot = self.repo.state["snapshot"]
            self.repo.state = {
                "contract_version_id": "contract-version-help",
                "state_version": 1,
                "snapshot": snapshot.model_copy(update={"run_version": 1}),
            }
        return DirectorHelpOutput(
            status="answered",
            answer="按契约，这是一项侦查检定。",
            suggested_response="请进行侦查检定；成功时你会找到抽屉里的线索。",
            candidate_id="search-desk",
            requested_skill_key="spot_hidden",
            next_steps=["先向玩家说明可见的灰尘。"],
            evidence_ids=["operator:search-desk"],
            confidence="high",
            uncertainty_reasons=[],
            assumptions=[],
            follow_up_question=None,
        )


def test_human_kp_help_reuses_kernel_authority_without_writes() -> None:
    repo = FakeDirectorHelpRepo(mode="human_kp", persisted_state=False)
    director = FakeHelpDirector(repo)

    result = asyncio.run(
        DirectorHelpService(repo).advise("run-help", "玩家搜索书桌，我该怎么裁定？", director)
    )

    assert director.calls == 1
    assert result["writes_performed"] is False
    assert result["can_execute"] is False
    assert result["suggested_response_audience"] == "kp_review_only"
    assert result["state_version"] == 0
    assert result["action"]["candidate_id"] == "search-desk"
    assert result["action"]["selected_skill_key"] == "spot_hidden"
    assert result["action"]["success_effects"] == ["设置事实 facts.letter.found = true"]
    assert result["citations"][0]["source_locator"] == "module-help · page 7"
    assert result["citations"][0]["visibility"] == "kp"
    assert director.brief is not None
    assert director.brief["offered_candidate_skills"] == {"search-desk": ["spot_hidden"]}
    assert {item["visibility"] for item in director.brief["evidence"]} == {"kp"}


def test_runtime_update_summary_uses_the_authoritative_command_payload() -> None:
    repo = FakeDirectorHelpRepo()
    payload = repo.contract.model_dump(mode="json")
    payload["entities"] = [
        {
            "entity_id": "archivist",
            "entity_type": "npc",
            "title": "档案员",
            "initial_location_id": "study",
        }
    ]
    payload["operators"][0]["success_commands"] = [
        {
            "kind": "update_entity_runtime",
            "entity_id": "archivist",
            "payload": {
                "emotional_state": "警觉",
                "short_term_goal": "保护档案",
            },
        }
    ]
    repo.contract = ScenarioContract.model_validate(payload)
    director = FakeHelpDirector(repo)

    result = asyncio.run(DirectorHelpService(repo).advise("run-help", "搜索书桌", director))

    assert director.brief is not None
    model_effects = director.brief["director_brief"]["candidates"][0]["success_effects"]
    assert model_effects == [
        '更新 archivist 的运行状态：{"emotional_state":"警觉","short_term_goal":"保护档案"}'
    ]
    assert result["action"]["success_effects"] == model_effects


def test_help_is_blocked_in_safety_pause_before_model_call() -> None:
    repo = FakeDirectorHelpRepo(mode="safety_paused")
    director = FakeHelpDirector(repo)

    with pytest.raises(ConflictError, match="safety_paused"):
        asyncio.run(DirectorHelpService(repo).advise("run-help", "现在怎么办？", director))

    assert director.calls == 0


def test_state_change_during_answer_discards_stale_advice() -> None:
    repo = FakeDirectorHelpRepo(mode="human_kp", persisted_state=True)
    director = FakeHelpDirector(repo, mutate_state=True)

    with pytest.raises(ConflictError, match="changed while Need Help"):
        asyncio.run(DirectorHelpService(repo).advise("run-help", "搜索书桌", director))


def test_candidate_skill_mismatch_fails_closed() -> None:
    repo = FakeDirectorHelpRepo()

    class MismatchedDirector(FakeHelpDirector):
        async def advise_human_kp(self, *, campaign_id: str, brief: dict):
            output = await super().advise_human_kp(
                campaign_id=campaign_id,
                brief=brief,
            )
            return output.model_copy(update={"requested_skill_key": "listen"})

    result = asyncio.run(
        DirectorHelpService(repo).advise(
            "run-help",
            "搜索书桌",
            MismatchedDirector(repo),
        )
    )

    assert result["status"] == "no_evidence"
    assert result["action"] is None
    assert result["citations"] == []


def test_candidate_requires_its_own_contract_citation() -> None:
    repo = FakeDirectorHelpRepo()

    class UnrelatedCitationDirector(FakeHelpDirector):
        async def advise_human_kp(self, *, campaign_id: str, brief: dict):
            output = await super().advise_human_kp(
                campaign_id=campaign_id,
                brief=brief,
            )
            return output.model_copy(update={"evidence_ids": ["module:chunk:chunk-7"]})

    result = asyncio.run(
        DirectorHelpService(repo).advise(
            "run-help",
            "搜索书桌",
            UnrelatedCitationDirector(repo),
        )
    )

    assert result["status"] == "no_evidence"
    assert result["action"] is None


def test_module_supplement_refs_only_identify_real_source_blocks() -> None:
    class SupplementalSourcesRepo(FakeDirectorHelpRepo):
        def search_module(
            self,
            module_id: str,
            query: str,
            **_kwargs,
        ) -> list[dict]:
            assert module_id == self.run["module_id"]
            assert query
            return [
                {
                    "source_type": "chunk",
                    "source_id": "chunk-7",
                    "module_id": module_id,
                    "visibility": "player",
                    "title": "书房原文",
                    "text": "调查员可以检查书桌抽屉。",
                    "source_locator": "page 7",
                },
                {
                    "source_type": "asset",
                    "source_id": "asset-7",
                    "module_id": module_id,
                    "visibility": "table",
                    "title": "书房图片",
                    "text": "图中的抽屉把手有新鲜划痕。",
                    "source_locator": "page 7 · image 2",
                },
                {
                    "source_type": "knowledge",
                    "source_id": "knowledge-7",
                    "module_id": module_id,
                    "visibility": "secret",
                    "title": "已审核书房知识",
                    "text": "书桌可能容纳调查线索。",
                    "source_locator": "knowledge:knowledge-7",
                },
            ]

    class SupplementalCitationDirector(FakeHelpDirector):
        async def advise_human_kp(self, *, campaign_id: str, brief: dict):
            assert campaign_id == "campaign-help"
            self.calls += 1
            self.brief = brief
            return DirectorHelpOutput(
                status="answered",
                answer="补充资料可以帮助 KP 描述当前场景。",
                suggested_response="你看到抽屉把手附近有一些划痕。",
                candidate_id=None,
                requested_skill_key=None,
                next_steps=[],
                evidence_ids=[
                    "module:chunk:chunk-7",
                    "module:asset:asset-7",
                    "module:knowledge:knowledge-7",
                ],
                confidence="medium",
                uncertainty_reasons=[],
                assumptions=[],
                follow_up_question=None,
            )

    repo = SupplementalSourcesRepo()
    result = asyncio.run(
        DirectorHelpService(repo).advise(
            "run-help",
            "我应该怎样描述书桌？",
            SupplementalCitationDirector(repo),
        )
    )
    citations = {item["source_type"]: item for item in result["citations"]}

    assert citations["module:chunk"]["source_refs"] == [
        {
            "source_block_id": "chunk-7",
            "document_id": "module-help",
            "page": None,
            "paragraph": None,
        }
    ]
    assert citations["module:chunk"]["source_refs_total_count"] == 1
    assert citations["module:chunk"]["visibility"] == "player"
    assert citations["module:asset"]["visibility"] == "table"
    assert citations["module:knowledge"]["visibility"] == "secret"
    for source_type in ("module:asset", "module:knowledge"):
        assert citations[source_type]["source_refs"] == []
        assert citations[source_type]["source_refs_total_count"] == 0
        assert citations[source_type]["source_refs_truncated"] is False
        assert citations[source_type]["authority"] == "source_context_only"
        assert citations[source_type]["source_locator"]

    assert citations["module:asset"]["evidence_id"] == "module:asset:asset-7"
    assert citations["module:knowledge"]["evidence_id"] == ("module:knowledge:knowledge-7")
    DirectorHelpResponse.model_validate(result)


def test_model_high_confidence_is_capped_for_source_context_only_answer() -> None:
    class SourceOnlyDirector(FakeHelpDirector):
        async def advise_human_kp(self, *, campaign_id: str, brief: dict):
            assert campaign_id == "campaign-help"
            self.calls += 1
            self.brief = brief
            return DirectorHelpOutput(
                status="answered",
                answer="检索原文提到书桌抽屉。",
                suggested_response="先检查这段描述是否含剧透。",
                candidate_id=None,
                requested_skill_key=None,
                next_steps=[],
                evidence_ids=["module:chunk:chunk-7"],
                confidence="high",
                uncertainty_reasons=[],
                assumptions=[],
                follow_up_question=None,
            )

    repo = FakeDirectorHelpRepo()
    result = asyncio.run(
        DirectorHelpService(repo).advise(
            "run-help",
            "书桌原文怎么写？",
            SourceOnlyDirector(repo),
        )
    )

    assert result["confidence"] == "medium"
    assert result["uncertainty_reasons"] == [
        "服务器已按可验证证据完整性将置信度上限设为中等。"
    ]
    assert result["citations"][0]["visibility"] == "kp"
    DirectorHelpResponse.model_validate(result)


def test_mismatched_persisted_snapshot_metadata_is_rejected_before_model() -> None:
    repo = FakeDirectorHelpRepo(persisted_state=True)
    assert repo.state is not None
    repo.state["snapshot"] = repo.state["snapshot"].model_copy(update={"run_id": "another-run"})
    director = FakeHelpDirector(repo)

    with pytest.raises(ConflictError, match="metadata does not match"):
        asyncio.run(DirectorHelpService(repo).advise("run-help", "搜索书桌", director))

    assert director.calls == 0


def test_final_control_fence_catches_pause_after_first_revalidation() -> None:
    class PauseAtFinalFenceRepo(FakeDirectorHelpRepo):
        def __init__(self):
            super().__init__(persisted_state=True)
            self.active_reads = 0

        def get_active_campaign_module_run(self, campaign_id: str) -> dict | None:
            self.active_reads += 1
            if self.active_reads == 3:
                self.run["director_control_mode"] = "safety_paused"
                self.run["version"] += 1
            return super().get_active_campaign_module_run(campaign_id)

    repo = PauseAtFinalFenceRepo()

    with pytest.raises(ConflictError, match="control changed"):
        asyncio.run(
            DirectorHelpService(repo).advise(
                "run-help",
                "搜索书桌",
                FakeHelpDirector(repo),
            )
        )


def test_large_valid_contract_is_bounded_for_model_and_http_response() -> None:
    repo = FakeDirectorHelpRepo()
    repo.contract = _large_contract()
    director = FakeHelpDirector(repo)

    result = asyncio.run(
        DirectorHelpService(repo).advise(
            "run-help",
            "搜索书桌",
            director,
        )
    )

    assert director.brief is not None
    candidate = director.brief["director_brief"]["candidates"][0]
    assert len(candidate["skill_choices"]) == 2
    assert candidate["skill_choices_total_count"] == 9
    assert candidate["skill_choices_truncated"] is True
    assert len(candidate["success_effects"]) == 4
    assert candidate["success_effects_total_count"] == 33
    assert candidate["success_effects_truncated"] is True
    assert len(candidate["failure_effects"]) == 4
    assert candidate["failure_effects_total_count"] == 33
    assert candidate["failure_effects_truncated"] is True
    assert len(director.brief["offered_candidate_skills"]["search-desk"]) == 2
    assert "skill_8" not in director.brief["offered_skill_keys"]

    citation = next(
        item for item in director.brief["evidence"] if item["evidence_id"] == "operator:search-desk"
    )
    assert "source_refs" not in citation
    assert citation["source_refs_total_count"] == 17
    assert citation["source_refs_truncated"] is True
    assert citation["source_refs_included_count"] == 0

    action = result["action"]
    assert len(action["skill_choices"]) == 8
    assert action["skill_choices_total_count"] == 9
    assert action["skill_choices_truncated"] is True
    assert len(action["success_effects"]) == 32
    assert action["success_effects_total_count"] == 33
    assert action["success_effects_truncated"] is True
    assert len(action["failure_effects"]) == 32
    assert action["failure_effects_total_count"] == 33
    assert action["failure_effects_truncated"] is True
    assert len(result["citations"][0]["source_refs"]) == 16
    assert result["citations"][0]["source_refs_total_count"] == 17
    assert result["citations"][0]["source_refs_truncated"] is True

    # This is the same validation FastAPI applies through response_model.
    DirectorHelpResponse.model_validate(result)


def test_skill_omitted_from_bounded_prompt_cannot_be_selected() -> None:
    repo = FakeDirectorHelpRepo()
    repo.contract = _large_contract()

    class OmittedSkillDirector(FakeHelpDirector):
        async def advise_human_kp(self, *, campaign_id: str, brief: dict):
            output = await super().advise_human_kp(
                campaign_id=campaign_id,
                brief=brief,
            )
            return output.model_copy(update={"requested_skill_key": "skill_8"})

    result = asyncio.run(
        DirectorHelpService(repo).advise(
            "run-help",
            "搜索书桌",
            OmittedSkillDirector(repo),
        )
    )

    assert result["status"] == "no_evidence"
    assert result["action"] is None


def test_extreme_external_sources_and_command_payloads_stay_bounded() -> None:
    class ExtremeRepo(FakeDirectorHelpRepo):
        def __init__(self):
            super().__init__()
            self.contract = _extreme_contract()

        def search_module(
            self,
            module_id: str,
            query: str,
            **_kwargs,
        ) -> list[dict]:
            assert module_id == self.run["module_id"]
            assert query
            return [
                {
                    "source_type": "chunk",
                    "source_id": f"chunk-{index}-" + ("源" * 500),
                    "module_id": "模" * 500,
                    "title": "标题" * 600,
                    "text": "正文" * 3000,
                    "source_locator": "定位" * 1200,
                }
                for index in range(10)
            ]

        def find_rule_source(self, _ruleset_id: str) -> dict:
            return {
                "id": "规则书" * 500,
                "title": "超长规则书标题" * 200,
            }

        def lexical_rule_chunks(self, *_args, **_kwargs) -> list[dict]:
            return [
                {
                    "id": f"rule-{index}-" + ("段" * 500),
                    "chapter": "章节" * 400,
                    "section": "小节" * 400,
                    "text": "规则原文" * 2000,
                    "page_start": 1,
                    "page_end": 2,
                }
                for index in range(10)
            ]

    class ExtremeDirector(FakeHelpDirector):
        async def advise_human_kp(self, *, campaign_id: str, brief: dict):
            assert campaign_id == "campaign-help"
            self.calls += 1
            self.brief = brief
            candidate = brief["director_brief"]["candidates"][0]
            supplementary_ids = [
                item["evidence_id"]
                for item in brief["evidence"]
                if item["authority"] == "source_context_only"
            ][:2]
            return DirectorHelpOutput(
                status="answered",
                answer="按契约裁定，并参考检索到的原文。",
                suggested_response="请进行侦查检定。",
                candidate_id=candidate["candidate_id"],
                requested_skill_key=candidate["skill_choices"][0]["skill_key"],
                next_steps=[],
                evidence_ids=[
                    candidate["evidence_ids"][0],
                    *supplementary_ids,
                ],
                confidence="medium",
                uncertainty_reasons=[],
                assumptions=[],
                follow_up_question=None,
            )

    repo = ExtremeRepo()
    director = ExtremeDirector(repo)
    question = "搜索书桌" + ("问" * 1996)
    result = asyncio.run(DirectorHelpService(repo).advise("run-help", question, director))

    assert director.brief is not None
    encoded_request = json.dumps(
        director.brief,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded_request) <= DIRECTOR_HELP_MODEL_REQUEST_JSON_BYTES
    assert len(director.brief["evidence"]) <= 12
    model_brief = director.brief["director_brief"]
    assert model_brief["evidence_included_count"] == 10
    assert model_brief["evidence_available_count"] == 10
    assert model_brief["evidence_total_count"] is None
    assert model_brief["evidence_total_count_lower_bound"] == 22
    assert model_brief["evidence_truncated"] is True
    for evidence in director.brief["evidence"]:
        assert "source_refs" not in evidence
        assert evidence["source_refs_included_count"] == 0
        assert evidence["source_refs_truncated"] is (evidence["source_refs_total_count"] > 0)

    model_candidate = director.brief["director_brief"]["candidates"][0]
    assert len(model_candidate["skill_choices"]) == 2
    assert model_candidate["skill_choices_total_count"] == 9
    assert model_candidate["skill_choices_truncated"] is True
    assert len(model_candidate["success_effects"]) == 4
    assert model_candidate["success_effects_total_count"] == 33
    assert model_candidate["success_effects_truncated"] is True
    assert len(model_candidate["failure_effects"]) == 4
    assert model_candidate["failure_effects_total_count"] == 33
    assert model_candidate["failure_effects_truncated"] is True

    action = result["action"]
    assert len(action["skill_choices"]) == 8
    assert action["skill_choices_total_count"] == 9
    assert action["skill_choices_truncated"] is True
    assert len(action["success_effects"]) == 32
    assert action["success_effects_total_count"] == 33
    assert action["success_effects_truncated"] is True
    assert len(action["failure_effects"]) == 32
    assert action["failure_effects_total_count"] == 33
    assert action["failure_effects_truncated"] is True
    assert all(
        len(item.encode("utf-8")) <= 1000
        for item in (*action["success_effects"], *action["failure_effects"])
    )
    assert all(len(item) <= 1000 for item in action["automatic_information"])

    external_citations = [
        citation
        for citation in result["citations"]
        if citation["authority"] == "source_context_only"
    ]
    assert external_citations
    for citation in external_citations:
        assert len(citation["evidence_id"]) <= 320
        assert len(citation["title"]) <= 500
        assert citation["source_locator"] is None or len(citation["source_locator"]) <= 1000
        assert citation["source_refs"] == []
        assert citation["source_refs_total_count"] == 1
        assert citation["source_refs_truncated"] is True

    DirectorHelpResponse.model_validate(result)

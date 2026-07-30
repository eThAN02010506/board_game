"""Allow-listed AI skills backed by existing structured director workflows."""

from __future__ import annotations

from ai_kp.director.skills.contracts import AiSkillManifest

_SKILLS = (
    AiSkillManifest(
        skill_id="platform.turn_proposal",
        version="1.0.0",
        display_name="行动理解与场景提案",
        category="action_understanding",
        description="理解玩家目标并提出检定、叙事和非权威世界效果候选。",
        input_schema_version="turn-context.v1",
        output_schema_version="kp-turn-output.v1",
        allowed_tools=("context.read", "rules.query", "proposal.create"),
        source_requirements=("campaign_context", "approved_character", "visible_module"),
    ),
    AiSkillManifest(
        skill_id="platform.check_consequence_narration",
        version="1.0.0",
        display_name="检定后果叙事",
        category="scene_direction",
        description="根据已验证的规则结果提出后果叙事，不重新解释或改写骰值。",
        input_schema_version="verified-check-batch.v1",
        output_schema_version="kp-turn-output.v1",
        allowed_tools=("context.read", "proposal.create"),
        source_requirements=("verified_check_result", "campaign_context"),
    ),
    AiSkillManifest(
        skill_id="platform.world_expansion",
        version="1.0.0",
        display_name="受约束世界补全",
        category="world_expansion",
        description="在 Canon 和现有事实边界内提出地点、NPC 或支线候选。",
        input_schema_version="world-gap-analysis.v1",
        output_schema_version="world-expansion-output.v1",
        allowed_tools=("context.read", "rules.query", "proposal.create"),
        source_requirements=("scene_analysis", "module_evidence", "world_fact_heads"),
    ),
    AiSkillManifest(
        skill_id="platform.session_recap",
        version="1.0.0",
        display_name="团后记忆提炼",
        category="memory_curation",
        description="从冻结事件窗口提取带来源的主要、支线、NPC 和线索记忆候选。",
        input_schema_version="session-recap-snapshot.v1",
        output_schema_version="session-recap-output.v1",
        allowed_tools=("context.read", "proposal.create"),
        source_requirements=("frozen_event_window",),
    ),
)

_BY_ID = {skill.skill_id: skill for skill in _SKILLS}


def get_ai_skill(skill_id: str) -> AiSkillManifest:
    try:
        return _BY_ID[skill_id]
    except KeyError as exc:
        raise ValueError(f"AI skill is not installed: {skill_id}") from exc


def list_ai_skills() -> list[dict]:
    return [skill.as_dict() for skill in _SKILLS]


__all__ = ["get_ai_skill", "list_ai_skills"]

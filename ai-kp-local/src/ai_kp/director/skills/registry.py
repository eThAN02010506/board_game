"""Allow-listed AI skills backed by existing structured director workflows."""

from __future__ import annotations

from typing import Literal

from ai_kp.director.skills.bundles import load_ai_skill_bundle
from ai_kp.director.skills.contracts import AiSkillManifest

AiSkillWorkflow = Literal[
    "player_action",
    "check_consequence",
    "world_expansion",
    "session_recap",
]

_SKILLS = (
    AiSkillManifest(
        skill_id="platform.module_scene_understanding",
        version="1.0.0",
        display_name="模组场景理解",
        category="module_understanding",
        description="解析可见场景、锚点、线索依赖、剧透边界与替代调查路径。",
        input_schema_version="turn-context.v1",
        output_schema_version="module-scene-interpretation.v1",
        allowed_tools=("context.read",),
        source_requirements=("visible_module", "confirmed_event_state"),
        bundle_name="understand-module-scene",
    ),
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
        bundle_name="understand-player-action",
    ),
    AiSkillManifest(
        skill_id="platform.npc_portrayal",
        version="1.0.0",
        display_name="NPC 表演",
        category="npc_portrayal",
        description="根据可见事实提出一致、不泄密的 NPC 对话与反应。",
        input_schema_version="turn-context.v1",
        output_schema_version="kp-turn-output.v1",
        allowed_tools=("context.read", "proposal.create"),
        source_requirements=("campaign_context", "visible_npc_evidence"),
        bundle_name="portray-npc",
    ),
    AiSkillManifest(
        skill_id="platform.scene_direction",
        version="1.0.0",
        display_name="场景导演",
        category="scene_direction",
        description="推进一个受约束场景拍点，保留玩家选择与锚点可达性。",
        input_schema_version="turn-context.v1",
        output_schema_version="kp-turn-output.v1",
        allowed_tools=("context.read", "proposal.create"),
        source_requirements=("campaign_context", "confirmed_event_state"),
        bundle_name="direct-scene",
    ),
    AiSkillManifest(
        skill_id="platform.output_safety_review",
        version="1.0.0",
        display_name="输出安全复核",
        category="safety_review",
        description="在结构校验前移除越权、泄密、时代冲突和预提交效果。",
        input_schema_version="candidate-draft.v1",
        output_schema_version="candidate-draft.v1",
        allowed_tools=("context.read",),
        source_requirements=("candidate_output", "visibility_policy"),
        bundle_name="review-output-safety",
    ),
    AiSkillManifest(
        skill_id="platform.check_consequence_narration",
        version="1.0.0",
        display_name="检定后果叙事",
        category="consequence_narration",
        description="根据已验证的规则结果提出后果叙事，不重新解释或改写骰值。",
        input_schema_version="verified-check-batch.v1",
        output_schema_version="kp-turn-output.v1",
        allowed_tools=("context.read", "proposal.create"),
        source_requirements=("verified_check_result", "campaign_context"),
        bundle_name="narrate-check-consequence",
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
        bundle_name="expand-world",
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
        bundle_name="curate-session-memory",
    ),
)

_BY_ID = {skill.skill_id: skill for skill in _SKILLS}

_COMPOSITIONS: dict[AiSkillWorkflow, tuple[str, ...]] = {
    "player_action": (
        "platform.turn_proposal",
        "platform.module_scene_understanding",
        "platform.npc_portrayal",
        "platform.scene_direction",
        "platform.output_safety_review",
    ),
    "check_consequence": (
        "platform.check_consequence_narration",
        "platform.output_safety_review",
    ),
    "world_expansion": (
        "platform.world_expansion",
        "platform.module_scene_understanding",
        "platform.output_safety_review",
    ),
    "session_recap": ("platform.session_recap",),
}

if len(_BY_ID) != len(_SKILLS):
    raise RuntimeError("AI skill registry contains duplicate skill IDs")
for workflow, skill_ids in _COMPOSITIONS.items():
    if len(skill_ids) != len(set(skill_ids)):
        raise RuntimeError(f"AI skill workflow contains duplicates: {workflow}")
    if unknown_ids := set(skill_ids).difference(_BY_ID):
        raise RuntimeError(
            f"AI skill workflow references unregistered skills: {workflow}: "
            f"{', '.join(sorted(unknown_ids))}"
        )


def get_ai_skill(skill_id: str) -> AiSkillManifest:
    try:
        return _BY_ID[skill_id]
    except KeyError as exc:
        raise ValueError(f"AI skill is not installed: {skill_id}") from exc


def list_ai_skills() -> list[dict]:
    results: list[dict] = []
    for skill in _SKILLS:
        item = skill.as_dict()
        if skill.bundle_name:
            item["bundle_hash"] = load_ai_skill_bundle(skill.bundle_name).content_hash
        results.append(item)
    return results


def compose_ai_skill_instructions(skills: tuple[AiSkillManifest, ...]) -> str:
    sections: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        bundle_name = skill.bundle_name
        if bundle_name is None or bundle_name in seen:
            continue
        bundle = load_ai_skill_bundle(bundle_name)
        seen.add(bundle_name)
        sections.append(
            f"[AI Skill: {skill.skill_id}@{skill.version}; "
            f"sha256={bundle.content_hash}]\n{bundle.instructions}"
        )
    return "\n\n".join(sections)


def resolve_ai_skills(skill_ids: tuple[str, ...]) -> tuple[AiSkillManifest, ...]:
    if not skill_ids or len(skill_ids) != len(set(skill_ids)):
        raise ValueError("AI skill composition requires unique skill IDs")
    return tuple(get_ai_skill(skill_id) for skill_id in skill_ids)


def resolve_ai_skill_composition(
    workflow: AiSkillWorkflow,
) -> tuple[AiSkillManifest, ...]:
    try:
        skill_ids = _COMPOSITIONS[workflow]
    except KeyError as exc:
        raise ValueError(f"Unknown AI skill workflow: {workflow}") from exc
    return resolve_ai_skills(skill_ids)


__all__ = [
    "AiSkillWorkflow",
    "compose_ai_skill_instructions",
    "get_ai_skill",
    "list_ai_skills",
    "resolve_ai_skill_composition",
    "resolve_ai_skills",
]

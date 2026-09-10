"""Canonical validated proposal output contract for the AI KP director."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_kp.platform.structured_json import StructuredJsonError, decode_json_object

Visibility = Literal["player", "table", "kp"]
AssertableFactType = Literal[
    "canonical_fact",
    "kp_secret",
    "character_belief",
    "rumor",
    "ai_hypothesis",
]
MemoryScope = Literal[
    "campaign_fact",
    "pc_major",
    "pc_side",
    "npc_interaction",
    "npc_relationship",
    "location_fact",
    "clue",
]
ActionFeasibility = Literal["possible", "partial", "impossible"]
ActionResolution = Literal["automatic", "check", "opposed", "no_roll"]
ForbiddenOutcomeClaim = Annotated[str, Field(min_length=2, max_length=160)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CheckCandidate(StrictModel):
    skill: str = Field(min_length=1, max_length=100)
    difficulty: Literal["regular", "hard", "extreme", "opposed", "automatic"] = "regular"
    reason: str = Field(min_length=1, max_length=500)
    pc_id: str | None = None
    hidden: bool = False
    bonus_dice: int = Field(default=0, ge=-2, le=2)
    allow_push: bool = True
    scope: str = Field(default="", max_length=500)
    supporting_factors: tuple[str, ...] = Field(default=(), max_length=8)
    automatic_information: tuple[str, ...] = Field(default=(), max_length=8)
    failure_stakes: str = Field(default="", max_length=1000)
    pushed_failure_stakes: str = Field(default="", max_length=1000)


class ActionRuling(StrictModel):
    """The declared goal, method, and effect ceiling decided before any roll."""

    goal: str = Field(min_length=1, max_length=500)
    method: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=300)
    feasibility: ActionFeasibility
    resolution: ActionResolution
    reason: str = Field(min_length=1, max_length=1000)
    maximum_effect: str = Field(min_length=1, max_length=1000)
    forbidden_outcome_claims: tuple[ForbiddenOutcomeClaim, ...] = Field(
        default=(), max_length=8
    )
    alternative: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def impossible_goals_do_not_roll(self) -> "ActionRuling":
        if self.feasibility == "impossible" and self.resolution != "no_roll":
            raise ValueError("An impossible action must not request a roll")
        if self.feasibility != "impossible" and self.resolution == "no_roll":
            raise ValueError("Only an impossible action may use no_roll")
        return self


class EventCandidate(StrictModel):
    event_type: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=2000)
    actor_type: Literal["system", "pc", "npc", "kp", "environment"] = "system"
    actor_id: str | None = None
    visibility: Visibility = "table"
    happened_at: str | None = None
    payload: dict = Field(default_factory=dict)


class MemoryCandidate(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    scope: MemoryScope = "campaign_fact"
    importance: int = Field(default=1, ge=1, le=5)
    visibility: Visibility = "table"
    pc_id: str | None = None
    npc_id: str | None = None
    happened_at: str | None = None


class NpcUpdateCandidate(StrictModel):
    npc_id: str = Field(min_length=1)
    appeared: bool = False
    relationship_delta: int = Field(default=0, ge=-10, le=10)
    last_seen_time: str | None = None
    note: str = Field(default="", max_length=1000)


class MapMoveCandidate(StrictModel):
    token_id: str = Field(min_length=1)
    to_location_name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    require_route: bool = True


class FactCandidate(StrictModel):
    fact_type: AssertableFactType
    subject: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=120)
    object_text: str = Field(min_length=1, max_length=4000)
    pc_id: str | None = Field(default=None, max_length=100)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=20)
    happened_at: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_character_scope(self) -> "FactCandidate":
        if self.fact_type == "character_belief" and self.pc_id is None:
            raise ValueError("character_belief requires pc_id")
        if self.fact_type != "character_belief" and self.pc_id is not None:
            raise ValueError("Only character_belief can target a PC")
        return self


class WorldEntityStateCandidate(StrictModel):
    """AI suggestion bound to an entity/version already present in its context."""

    entity_id: str = Field(min_length=1, max_length=160)
    expected_version: int = Field(default=0, ge=0)
    dimension: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: str | int | bool | None
    visibility: Literal["table", "kp", "secret"] = "table"
    note: str = Field(default="", max_length=2000)


class KpTurnOutput(StrictModel):
    public_narration: str = Field(min_length=1, max_length=12000)
    kp_notes: str = Field(default="", max_length=4000)
    action_ruling: ActionRuling
    proposed_checks: list[CheckCandidate] = Field(default_factory=list, max_length=8)
    proposed_events: list[EventCandidate] = Field(default_factory=list, max_length=12)
    proposed_memories: list[MemoryCandidate] = Field(default_factory=list, max_length=10)
    proposed_npc_updates: list[NpcUpdateCandidate] = Field(default_factory=list, max_length=8)
    proposed_map_moves: list[MapMoveCandidate] = Field(default_factory=list, max_length=12)
    proposed_facts: list[FactCandidate] = Field(default_factory=list, max_length=12)
    proposed_world_entity_states: list[WorldEntityStateCandidate] = Field(
        default_factory=list, max_length=12
    )

    @model_validator(mode="after")
    def unresolved_checks_cannot_commit_dependent_effects(self) -> "KpTurnOutput":
        entity_ids = [item.entity_id for item in self.proposed_world_entity_states]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError(
                "A proposal can change at most one state dimension per world entity"
            )
        if self.proposed_checks and any(
            (
                self.proposed_events,
                self.proposed_memories,
                self.proposed_npc_updates,
                self.proposed_map_moves,
                self.proposed_facts,
                self.proposed_world_entity_states,
            )
        ):
            raise ValueError(
                "A proposal requesting unresolved checks cannot also commit world effects"
            )
        has_effects = any(
            (
                self.proposed_events,
                self.proposed_memories,
                self.proposed_npc_updates,
                self.proposed_map_moves,
                self.proposed_facts,
                self.proposed_world_entity_states,
            )
        )
        if self.action_ruling.feasibility == "impossible" and (
            self.proposed_checks or has_effects
        ):
            raise ValueError(
                "An impossible action cannot request dice or commit world effects"
            )
        if self.action_ruling.resolution in {"check", "opposed"}:
            if not self.proposed_checks:
                raise ValueError("A rolled action ruling requires proposed checks")
            if self.action_ruling.resolution == "opposed" and any(
                item.difficulty != "opposed" for item in self.proposed_checks
            ):
                raise ValueError("An opposed ruling requires opposed checks")
            if self.action_ruling.resolution == "check" and any(
                item.difficulty in {"opposed", "automatic"}
                for item in self.proposed_checks
            ):
                raise ValueError(
                    "A check ruling requires regular, hard, or extreme checks"
                )
        elif self.proposed_checks:
            raise ValueError(
                "Automatic and no-roll rulings cannot request proposed checks"
            )
        return self


STRUCTURED_OUTPUT_INSTRUCTIONS = """只返回一个 JSON 对象，不要 Markdown 或额外文字。字段必须是：
{
  "public_narration": "玩家可见的场景反馈",
  "kp_notes": "仅 KP 可见的简短备注",
  "action_ruling": {"goal":"玩家希望达成什么","method":"使用的手段","target":"目标对象","feasibility":"possible|partial|impossible","resolution":"automatic|check|opposed|no_roll","reason":"为何这样裁定","maximum_effect":"即使大成功也不会超过的效果上限","forbidden_outcome_claims":["与效果上限直接冲突、不得出现在结果中的具体声称"],"alternative":"不可行或部分可行时可尝试的替代做法"},
  "proposed_checks": [{"skill":"技能名","difficulty":"regular|hard|extreme|opposed|automatic","reason":"原因","pc_id":null,"hidden":false,"bonus_dice":0,"allow_push":true,"scope":"该技能能验证什么","supporting_factors":[],"automatic_information":[],"failure_stakes":"普通失败风险","pushed_failure_stakes":"推动失败的严重后果"}],
  "proposed_events": [{"event_type":"类型","summary":"事实摘要","actor_type":"system|pc|npc|kp|environment","actor_id":null,"visibility":"player|table|kp","happened_at":null,"payload":{}}],
  "proposed_memories": [{"text":"值得长期记得的事实","scope":"campaign_fact|pc_major|pc_side|npc_interaction|npc_relationship|location_fact|clue","importance":1,"visibility":"player|table|kp","pc_id":null,"npc_id":null,"happened_at":null}],
  "proposed_npc_updates": [{"npc_id":"已知 NPC id","appeared":false,"relationship_delta":0,"last_seen_time":null,"note":""}],
  "proposed_map_moves": [{"token_id":"已知棋子 id","to_location_name":"目标地点","reason":"移动原因","require_route":true}],
  "proposed_facts": [{"fact_type":"canonical_fact|kp_secret|character_belief|rumor|ai_hypothesis","subject":"主体","predicate":"关系或属性","object_text":"值或陈述","pc_id":null,"evidence_event_ids":[],"happened_at":null}],
  "proposed_world_entity_states": [{"entity_id":"上下文中明确存在的世界实体 id","expected_version":0,"dimension":"该实体 state_dimensions 中的稳定 id","value":"字符串、整数、布尔值或 null","visibility":"table|kp|secret","note":"状态变化依据"}]
}
没有的候选项必须返回空数组。不得编造 NPC id、PC id、事件 id 或棋子 id。
先判断目标与手段是否在当前事实、目标能力和空间位置下可行，再决定是否掷骰。
检定必须归属于实际执行不确定行动的一方，并检验该行动者采用的手段。不得让玩家用自己的
技能替 NPC、环境或其他目标决定其能力、感知或反应；例如无遮挡的普通喊话是否传到目标处
通常直接成立，目标是否回应由其状态与动机决定。玩家若在观察目标的反应，只有反应细微、
受遮蔽或确有遗漏风险时，才可检定玩家的观察手段；不得把它倒置成玩家替目标检定听见与否。
骰子只能决定已声明可行目标的结果，01 或大成功不能突破 maximum_effect、赋予目标
不存在的交流或恐惧能力、跨越未连接地点或改写 module_canon。目标不可能时必须使用
feasibility=impossible、resolution=no_roll，解释原因并给出可选的目标重述；不得为了
“给玩家机会”虚构一次无意义检定。
行动陈述必须区分“角色采用的手段”与“权威世界事实是否成立”。影响目标认知或立场的成功
不能把行动者的主张写成 canonical_fact；对白或具体做法不足时，应在 alternative 中只询问
缺失字段。玩家选择的技能必须来自其角色卡和规则集目录。
每项检定必须形成玩家可见的 CheckPlan：说明该技能只能验证的 scope、已经自动获得的
automatic_information、奖励/惩罚骰依据、普通失败风险与推动失败风险。不得用可选检定
阻断 NPC 主动说出的基础内容；没有权威依据时 bonus_dice 必须为 0，风险字段保持空字符串。
玩家明确表示放弃、不再重复或不采用的旧手段，不得因为近期叙事或上一轮检定而再次写入
goal、method 或 proposed_checks。必须只裁定当前声明仍要执行的行动。可选线索的检定失败
不会阻断已经可见、可达且没有权威障碍的出口或地点；玩家正常步行进入这类地点默认直接
成立，检定只用于其另外声明的搜索、辨认、快速移动或危险操作。
固有危险行动可以尝试，但 maximum_effect 不得抹除与检定目标无关的必然后果；风险认知、
实际执行和后果结算必须按各自的规则能力与权威效果分别处理。当前世界不存在的能力、物品
或服务不能靠检定生成。
forbidden_outcome_claims 只列出与 maximum_effect 直接矛盾的具体结果声称；
没有可确定列举的矛盾声称时必须为空数组，不得填泛化词、动作类型或示例。
只有已经在当前场景中成立、并且值得作为长期真相区分检索的内容才进入 proposed_facts；
传闻必须标为 rumor，角色个人认知必须标为 character_belief，推测必须标为 ai_hypothesis。
proposed_facts 的 pc_id 只能用于 character_belief；canonical_fact、kp_secret、rumor、
ai_hypothesis 都不能带 pc_id，否则整份草稿会被拒绝。需要表达某 PC 的状态或认知时，
用 character_belief，或把该 PC 作为 subject 的文本，而不是给其他类别填 pc_id。
proposed_world_entity_states 只能引用 campaign_world_entity 上给出的 entity_id、state_version
和 state_dimensions；expected_version 可省略，由服务器绑定提示快照版本。每个实体每份提案至多
更新一个维度；没有明确、已成立的变化就返回空数组，不得从叙事猜测或编造状态维度。
如果 proposed_checks 非空，则 proposed_events、proposed_memories、proposed_npc_updates、
proposed_map_moves、proposed_facts 和 proposed_world_entity_states 必须全部为空；检定结果不得预写。
玩家观察或进入某位置时，public_narration 应描述该位置**当前实际存在的场景内容**
（来自模组大纲：那里有谁、有什么、正在发生什么），而不是只回放玩家行动的机械结果。
当模组与已提交状态明确说明某位置存在实体、环境变化或正在发生的事件时，
玩家观察该位置的叙述必须展示这些已建立内容；只有玩家声明具体操作时才聚焦其直接结果。
""".strip()


class StructuredOutputError(ValueError):
    pass


def parse_kp_turn_output(raw: str) -> KpTurnOutput:
    try:
        payload = decode_json_object(raw)
        _repair_text_slots(payload)
        _repair_resolution_slot(payload)
        return KpTurnOutput.model_validate(payload)
    except (ValidationError, StructuredJsonError) as exc:
        raise StructuredOutputError(str(exc)) from exc


def _repair_text_slots(payload: object) -> None:
    """Join pure string lists emitted for schema fields that are scalar prose."""

    if not isinstance(payload, dict):
        return

    def join_string_list(container: dict, key: str) -> None:
        value = container.get(key)
        if isinstance(value, list) and value and all(
            isinstance(item, str) and item.strip() for item in value
        ):
            container[key] = "；".join(item.strip() for item in value)

    for key in ("public_narration", "kp_notes"):
        join_string_list(payload, key)
    ruling = payload.get("action_ruling")
    if isinstance(ruling, dict):
        for key in (
            "goal",
            "method",
            "target",
            "reason",
            "maximum_effect",
            "alternative",
        ):
            join_string_list(ruling, key)


def _repair_resolution_slot(payload: object) -> None:
    """Repair one common small-model enum-slot mix-up without inventing a ruling.

    Models occasionally copy ``feasibility`` (possible/partial/impossible) into the
    adjacent ``resolution`` field.  The already emitted check list is sufficient to
    recover the intended resolution deterministically; when it is empty, a feasible
    action is automatic and an impossible action is no-roll.  Other unknown values
    still fail strict validation instead of being guessed.
    """

    if not isinstance(payload, dict):
        return
    ruling = payload.get("action_ruling")
    if not isinstance(ruling, dict):
        return
    resolution = ruling.get("resolution")
    feasibility = ruling.get("feasibility")
    # In ordinary language, small models often use ``no_roll`` to mean a
    # feasible action that needs no dice.  The canonical contract calls that
    # ``automatic`` and reserves ``no_roll`` for impossible goals.
    if resolution == "no_roll" and feasibility in {"possible", "partial"}:
        ruling["resolution"] = "automatic"
        return
    if resolution == "automatic" and feasibility == "impossible":
        ruling["resolution"] = "no_roll"
        return
    if resolution not in {"possible", "partial", "impossible"}:
        return
    checks = payload.get("proposed_checks")
    if isinstance(checks, list) and checks:
        ruling["resolution"] = (
            "opposed"
            if all(
                isinstance(check, dict) and check.get("difficulty") == "opposed"
                for check in checks
            )
            else "check"
        )
        return
    ruling["resolution"] = (
        "no_roll" if feasibility == "impossible" else "automatic"
    )


def dump_candidates(values: list[BaseModel | dict], model_type: type[BaseModel]) -> list[dict]:
    return [model_type.model_validate(value).model_dump(mode="json") for value in values]

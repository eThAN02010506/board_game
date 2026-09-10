"""Large-model authoring adapter that cannot self-assert authority metadata."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.resolution.action_intent_ir import (
    ActionIntentAudit,
    ActionIntentPlan,
    ActionIntentPolicyValidator,
    ActionIntentStatus,
    IntentEffectRef,
    IntentStep,
    world_command_for_intent_effect,
)
from ai_kp.platform.resolution.action_intent_protocol import (
    audit_messages,
    intent_messages,
    normalize_intent_transport,
)
from ai_kp.platform.resolution.check_catalog import ScenarioCheckCatalog
from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    OutcomeBranch,
    PlanStepSpec,
    ScenarioContract,
    ScenarioSnapshot,
    SkillChoice,
    TaskMethod,
    WorldCommand,
)
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.skill_authority import player_authorized_skill_keys
from ai_kp.platform.resolution.tabletop_turn import TabletopTurnFrame
from ai_kp.platform.resolution.world_expansion_contract import (
    ExpansionRecords,
    WorldExpansionContractProposal,
)
from ai_kp.platform.structured_json import decode_json_object

_RECORD_GROUPS = tuple(ExpansionRecords.model_fields)
_INTERNAL_AUDIT_QUESTION = re.compile(
    r"(?:set_fact|target_ref|command_kind|operator_id|fact\s+such\s+as|"
    r"[a-z]{2,}(?:_[a-z0-9]+){2,})",
    re.IGNORECASE,
)


class WorldExpansionDraft(BaseModel):
    """Untrusted model output; deliberately excludes all authority bindings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    confidence: Literal["low", "medium", "high"]
    assumptions: tuple[str, ...] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=2000)
    records: ExpansionRecords


class WorldExpansionAuthoringResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal: WorldExpansionContractProposal | None
    attempt_count: int = Field(ge=1, le=2)
    interpretation_attempt_count: int = Field(default=1, ge=1, le=3)
    audit_attempt_count: int = Field(default=0, ge=0, le=1)
    validation_errors: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    action_status: ActionIntentStatus = "executable"
    intent_plan: ActionIntentPlan | None = None


class ConstrainedWorldExpansionAuthoringAdapter:
    def __init__(
        self,
        llm: LlmClient,
        check_catalog: ScenarioCheckCatalog,
        effect_catalog: ScenarioEffectCatalog | None = None,
    ):
        self.llm = llm
        self.check_catalog = check_catalog
        self.effect_catalog = effect_catalog
        self.compiler = ScenarioContractCompiler(effect_catalog)
        self.intent_policy = ActionIntentPolicyValidator()

    async def author(
        self,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_intent: str,
        *,
        proposal_id: str,
        tabletop_frame: TabletopTurnFrame | None = None,
    ) -> WorldExpansionAuthoringResult:
        errors: list[str] = []
        allowed_skill_keys = player_authorized_skill_keys(
            self.check_catalog, player_intent
        )
        intent_plan, interpretation_attempts, intent_errors = await self._interpret(
            contract,
            snapshot,
            player_intent,
            proposal_id=proposal_id,
            allowed_skill_keys=allowed_skill_keys,
        )
        used_tabletop_bridge = False
        if intent_plan is None and tabletop_frame is not None:
            intent_plan = self._intent_plan_from_tabletop_frame(
                tabletop_frame,
                contract=contract,
                player_intent=player_intent,
                proposal_id=proposal_id,
            )
            if intent_plan is not None:
                used_tabletop_bridge = True
                intent_errors.append(
                    "Server reused the validated tabletop frame after the intent "
                    "Agent exhausted its structured-output attempts."
                )
        if intent_plan is None:
            return WorldExpansionAuthoringResult(
                proposal=None,
                attempt_count=1,
                interpretation_attempt_count=interpretation_attempts,
                validation_errors=(
                    (
                        "自动 KP 尚未能可靠理解这项行动的步骤和前置条件。请换一种方式说明"
                        "目标、实际手段以及各步骤所依赖的条件。"
                    ),
                ),
                diagnostics=tuple(intent_errors),
                action_status="possible_but_underspecified",
            )
        assessment = self.intent_policy.assess_requirements(
            intent_plan, contract, snapshot, player_intent=player_intent
        )
        if assessment.status != "executable":
            return WorldExpansionAuthoringResult(
                proposal=None,
                attempt_count=1,
                interpretation_attempt_count=interpretation_attempts,
                validation_errors=assessment.questions or (assessment.reason,),
                diagnostics=tuple(intent_errors),
                action_status=assessment.status,
                intent_plan=intent_plan,
            )
        if used_tabletop_bridge:
            audit = ActionIntentAudit(
                verdict="accept",
                reason=(
                    "Server bridge is restricted to validated entity IDs and "
                    "action-scoped boolean progress."
                ),
            )
            audit_attempts = 0
            audit_errors: list[str] = []
        else:
            audit, audit_attempts, audit_errors = await self._audit_intent(
                contract,
                snapshot,
                player_intent,
                allowed_skill_keys=allowed_skill_keys,
                intent_plan=intent_plan,
            )
        diagnostics = [*intent_errors, *audit_errors]
        if audit is None:
            return WorldExpansionAuthoringResult(
                proposal=None,
                attempt_count=1,
                interpretation_attempt_count=interpretation_attempts,
                audit_attempt_count=audit_attempts,
                validation_errors=(
                    (
                        "独立语义审核未能可靠完成。请重述玩家的目标、"
                        "现实做法和愿意承担的代价。"
                    ),
                ),
                diagnostics=tuple(diagnostics),
                action_status="possible_but_underspecified",
                intent_plan=intent_plan,
            )
        if audit.verdict != "accept":
            status: ActionIntentStatus = (
                "impossible"
                if audit.verdict == "impossible"
                else "possible_but_underspecified"
            )
            return WorldExpansionAuthoringResult(
                proposal=None,
                attempt_count=1,
                interpretation_attempt_count=interpretation_attempts,
                audit_attempt_count=audit_attempts,
                validation_errors=audit.questions or audit.issues or (audit.reason,),
                diagnostics=tuple(diagnostics),
                action_status=status,
                intent_plan=intent_plan,
            )
        for attempt in range(1, 3):
            raw = await self.llm.complete(
                self._messages(
                    contract,
                    snapshot,
                    player_intent,
                    proposal_id,
                    allowed_skill_keys,
                    intent_plan,
                    errors,
                ),
                temperature=0.2,
            )
            try:
                normalized = self._normalize_transport(decode_json_object(raw), prefix=(
                    f"expansion.{proposal_id}."
                ))
                draft, degradation = self._validate_with_record_degradation(
                    normalized
                )
                if degradation:
                    errors.append(degradation)
                draft, namespace_degradation = self._discard_repeated_catalog_records(
                    draft, contract=contract
                )
                if namespace_degradation:
                    errors.append(namespace_degradation)
                self.intent_policy.validate_bindings(
                    intent_plan,
                    draft.records.operators,
                    draft.records.task_methods,
                )
                self._validate_skill_authority(draft, allowed_skill_keys)
                self._validate_required_method(draft, allowed_skill_keys)
                self._validate_reachable_outcomes(draft)
                proposal = WorldExpansionContractProposal(
                    proposal_id=proposal_id,
                    base_contract_id=contract.contract_id,
                    base_source_version=contract.source_version,
                    base_contract_hash=self.compiler.contract_hash(contract),
                    base_state_version=snapshot.run_version,
                    confidence=draft.confidence,
                    assumptions=draft.assumptions,
                    rationale=draft.rationale,
                    records=draft.records,
                )
                return WorldExpansionAuthoringResult(
                    proposal=proposal,
                    attempt_count=attempt,
                    interpretation_attempt_count=interpretation_attempts,
                    audit_attempt_count=audit_attempts,
                    validation_errors=tuple(errors),
                    diagnostics=tuple(diagnostics),
                    intent_plan=intent_plan,
                )
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                errors.append(str(exc)[:800])
        fallback = self._deterministic_records(
            intent_plan,
            allowed_skill_keys=allowed_skill_keys,
            proposal_id=proposal_id,
        )
        if fallback is not None:
            proposal = WorldExpansionContractProposal(
                proposal_id=proposal_id,
                base_contract_id=contract.contract_id,
                base_source_version=contract.source_version,
                base_contract_hash=self.compiler.contract_hash(contract),
                base_state_version=snapshot.run_version,
                confidence="high",
                assumptions=(
                    "模型契约草稿无效；服务端仅按已审计意图生成最小有界记录。",
                ),
                rationale=(
                    "确定性降级只绑定意图计划已声明的步骤、技能与命名空间内效果。"
                ),
                records=fallback,
            )
            return WorldExpansionAuthoringResult(
                proposal=proposal,
                attempt_count=2,
                interpretation_attempt_count=interpretation_attempts,
                audit_attempt_count=audit_attempts,
                validation_errors=tuple(errors),
                diagnostics=tuple(diagnostics),
                action_status="executable",
                intent_plan=intent_plan,
            )
        return WorldExpansionAuthoringResult(
            proposal=None,
            attempt_count=2,
            interpretation_attempt_count=interpretation_attempts,
            audit_attempt_count=audit_attempts,
            validation_errors=tuple(errors),
            diagnostics=tuple(diagnostics),
            action_status="possible_but_underspecified",
            intent_plan=intent_plan,
        )

    @staticmethod
    def _deterministic_records(
        plan: ActionIntentPlan,
        *,
        allowed_skill_keys: tuple[str, ...],
        proposal_id: str,
    ) -> ExpansionRecords | None:
        """Compile exact audited boolean facts without inventing parameters."""

        prefix = f"expansion.{proposal_id}."
        operators: list[ActionOperator] = []
        for step in plan.steps:
            if not step.operator_id.startswith(prefix):
                return None
            commands: dict[str, list[WorldCommand]] = {
                "always": [],
                "success": [],
                "failure": [],
                "pushed_failure": [],
            }
            for effect in step.effects:
                command = world_command_for_intent_effect(effect)
                if (
                    command.kind != "set_fact"
                    or not str(command.path).startswith(prefix)
                    or command.value is not True
                    or command.payload
                ):
                    return None
                commands[effect.applies_on].append(command)
            failure_stakes = "；".join(
                dict.fromkeys(
                    effect.description
                    for effect in step.effects
                    if effect.applies_on == "failure"
                )
            )[:1000]
            choices = tuple(
                SkillChoice(
                    skill_key=skill_key,
                    difficulty="regular",
                    reason="该技能由玩家行动文本与已安装规则目录共同授权。",
                    # The deterministic fallback has no authorized severe
                    # consequence command, so pushing remains unavailable.
                    allow_push=False,
                    failure_stakes=failure_stakes,
                )
                for skill_key in allowed_skill_keys
            )
            hints = tuple(dict.fromkeys((step.goal, step.method, step.target)))
            checked = bool(choices)
            if commands["pushed_failure"]:
                # The conservative fallback intentionally disables pushing; it
                # therefore cannot expose an outcome the player can never reach.
                return None
            if checked and not commands["failure"]:
                return None
            if not checked and (
                commands["failure"] or commands["pushed_failure"]
            ):
                return None
            operators.append(
                ActionOperator(
                    operator_id=step.operator_id,
                    title=step.goal,
                    intent_hints=hints,
                    policy="required_check" if checked else "automatic",
                    skill_choices=choices,
                    always_commands=tuple(commands["always"]),
                    success_commands=tuple(commands["success"]),
                    failure_commands=tuple(commands["failure"]),
                    outcome_branches=(
                        OutcomeBranch(
                            outcome_key="pushed_failure",
                            commands=tuple(commands["pushed_failure"]),
                        ),
                    ) if commands["pushed_failure"] else (),
                    rationale="由已审计的玩家意图计划确定性生成。",
                    maximum_effect="仅写入本次世界扩展命名空间内的步骤进展。",
                )
            )
        methods: tuple[TaskMethod, ...] = ()
        if len(plan.steps) > 1:
            methods = (
                TaskMethod(
                    method_id=f"{prefix}method",
                    task_key=f"{prefix}task",
                    title=plan.goal,
                    intent_hints=(plan.goal,),
                    steps=tuple(
                        PlanStepSpec(
                            step_id=step.step_id,
                            operator_id=step.operator_id,
                            depends_on=step.depends_on,
                        )
                        for step in plan.steps
                    ),
                ),
            )
        records = ExpansionRecords(
            operators=tuple(operators),
            task_methods=methods,
        )
        ActionIntentPolicyValidator.validate_bindings(
            plan, records.operators, records.task_methods
        )
        return records

    @staticmethod
    def _intent_plan_from_tabletop_frame(
        frame: TabletopTurnFrame,
        *,
        contract: ScenarioContract,
        player_intent: str,
        proposal_id: str,
    ) -> ActionIntentPlan | None:
        """Bridge one validated Agent artifact into a bounded intent plan.

        The bridge records only action-scoped boolean progress. It cannot infer
        skills, costs, clues, NPC knowledge, or campaign facts. Those remain the
        responsibility of installed catalogs and the independent intent audit.
        """

        known_entity_ids = {entity.entity_id for entity in contract.entities}
        if (
            frame.kind not in {"action", "multi_step_action"}
            or frame.ambiguity is not None
            or not frame.goal.strip()
            or not set(frame.target_entity_ids) <= known_entity_ids
        ):
            return None
        raw_steps = frame.steps or (frame.goal,)
        prefix = f"expansion.{proposal_id}."
        target = ", ".join(frame.target_entity_ids) or "current_scene"
        method = frame.method.strip() or player_intent.strip()
        steps: list[IntentStep] = []
        for index, raw_step in enumerate(raw_steps[:8], start=1):
            step_id = f"step_{index:02d}"
            operator_id = f"{prefix}{step_id}"
            steps.append(IntentStep(
                step_id=step_id,
                operator_id=operator_id,
                goal=raw_step,
                method=method,
                target=target,
                depends_on=((f"step_{index - 1:02d}",) if index > 1 else ()),
                effects=(IntentEffectRef(
                    effect_id=f"progress_{index:02d}",
                    role="progress",
                    applies_on="success",
                    command_kind="set_fact",
                    target_ref=f"{operator_id}.completed",
                    value=True,
                    description="记录本次玩家行动中该有界步骤已完成。",
                ),),
            ))
        return ActionIntentPlan(goal=frame.goal, steps=tuple(steps))

    async def _interpret(
        self,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_intent: str,
        *,
        proposal_id: str,
        allowed_skill_keys: tuple[str, ...],
    ) -> tuple[ActionIntentPlan | None, int, list[str]]:
        errors: list[str] = []
        for attempt in range(1, 4):
            raw = await self.llm.complete(
                intent_messages(
                    contract,
                    snapshot,
                    player_intent,
                    proposal_id,
                    allowed_skill_keys,
                    self.effect_catalog,
                    errors,
                ),
                temperature=0.1,
            )
            try:
                payload = normalize_intent_transport(
                    decode_json_object(raw), prefix=f"expansion.{proposal_id}."
                )
                return ActionIntentPlan.model_validate(payload), attempt, errors
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                errors.append(str(exc)[:800])
        return None, 3, errors

    async def _audit_intent(
        self,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_intent: str,
        *,
        allowed_skill_keys: tuple[str, ...],
        intent_plan: ActionIntentPlan,
    ) -> tuple[ActionIntentAudit | None, int, list[str]]:
        errors: list[str] = []
        raw = await self.llm.complete(
            audit_messages(
                contract,
                snapshot,
                player_intent,
                allowed_skill_keys,
                self.effect_catalog,
                intent_plan,
                errors,
            ),
            temperature=0.0,
        )
        try:
            audit = ActionIntentAudit.model_validate(decode_json_object(raw))
            if (
                audit.verdict == "clarification"
                and audit.questions
                and all(_INTERNAL_AUDIT_QUESTION.search(item) for item in audit.questions)
            ):
                errors.append(
                    "intent audit asked the player to choose internal state representation; "
                    "the audit result is not player-safe"
                )
                return None, 1, errors
            return audit, 1, errors
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            errors.append("intent audit: " + str(exc)[:780])
            return None, 1, errors

    @staticmethod
    def _discard_repeated_catalog_records(
        draft: WorldExpansionDraft,
        *,
        contract: ScenarioContract,
    ) -> tuple[WorldExpansionDraft, str]:
        """Drop model-repeated catalog records; cross-reference validation stays strict."""

        catalog_ids = {
            "locations": (
                "location_id",
                {item.location_id for item in contract.locations},
            ),
            "entities": ("entity_id", {item.entity_id for item in contract.entities}),
            "clocks": ("clock_id", {item.clock_id for item in contract.clocks}),
            "resources": (
                "resource_id",
                {item.resource_id for item in contract.resources},
            ),
            "clues": ("clue_id", {item.clue_id for item in contract.clues}),
            "operators": (
                "operator_id",
                {item.operator_id for item in contract.operators},
            ),
            "task_methods": (
                "method_id",
                {item.method_id for item in contract.task_methods},
            ),
            "reactive_policies": (
                "policy_id",
                {item.policy_id for item in contract.reactive_policies},
            ),
            "consequence_signals": (
                "signal_id",
                {item.signal_id for item in contract.consequence_signals},
            ),
        }
        payload = draft.model_dump(mode="json")
        removed: list[str] = []
        for group, (id_field, existing_ids) in catalog_ids.items():
            kept = []
            for index, record in enumerate(payload["records"][group]):
                identifier = record.get(id_field)
                if identifier in existing_ids:
                    removed.append(f"{group}.{index}:{identifier}")
                else:
                    kept.append(record)
            payload["records"][group] = kept
        if not removed:
            return draft, ""
        return WorldExpansionDraft.model_validate(payload), (
            "Discarded model-repeated records from the existing catalog: "
            + ", ".join(removed)
        )

    @staticmethod
    def _validate_skill_authority(
        draft: WorldExpansionDraft,
        allowed_skill_keys: tuple[str, ...],
    ) -> None:
        allowed = set(allowed_skill_keys)
        selected = {
            choice.skill_key
            for operator in draft.records.operators
            for choice in operator.skill_choices
        }
        unknown = sorted(selected - allowed)
        if unknown:
            raise ValueError(
                "Expansion selected skills outside the player-authorized catalog: "
                f"{unknown}; allowed={sorted(allowed)}"
            )

    @staticmethod
    def _validate_required_method(
        draft: WorldExpansionDraft,
        allowed_skill_keys: tuple[str, ...],
    ) -> None:
        if not allowed_skill_keys:
            return
        if not any(operator.skill_choices for operator in draft.records.operators):
            raise ValueError(
                "The player supplied a ruleset-resolved method, but the expansion "
                "removed its check and made the action automatic."
            )

    @staticmethod
    def _validate_reachable_outcomes(draft: WorldExpansionDraft) -> None:
        unreachable = [
            operator.operator_id
            for operator in draft.records.operators
            if operator.policy in {"automatic", "choice"}
            and (operator.failure_commands or operator.outcome_branches)
        ]
        if unreachable:
            raise ValueError(
                "Automatic expansion actions cannot carry unreachable failure "
                f"branches: {unreachable}"
            )

    @staticmethod
    def _validate_with_record_degradation(
        payload: object,
    ) -> tuple[WorldExpansionDraft, str]:
        """Drop only precisely located malformed additive records, never repair them."""

        candidate = deepcopy(payload)
        removed: list[str] = []
        for _ in range(16):
            try:
                return WorldExpansionDraft.model_validate(candidate), (
                    "Discarded malformed optional expansion records: "
                    + ", ".join(removed)
                    if removed
                    else ""
                )
            except ValidationError as exc:
                if not isinstance(candidate, dict) or not isinstance(
                    candidate.get("records"), dict
                ):
                    raise
                targets: set[tuple[str, int]] = set()
                for issue in exc.errors():
                    location = issue.get("loc", ())
                    if (
                        len(location) >= 3
                        and location[0] == "records"
                        and location[1] in _RECORD_GROUPS
                        and isinstance(location[2], int)
                    ):
                        targets.add((str(location[1]), int(location[2])))
                    else:
                        raise
                if not targets:
                    raise
                for group, index in sorted(
                    targets, key=lambda item: (item[0], -item[1])
                ):
                    records = candidate["records"].get(group)
                    if not isinstance(records, list) or not 0 <= index < len(records):
                        raise
                    records.pop(index)
                    removed.append(f"{group}.{index}")
        raise ValueError("Too many malformed expansion records")

    @staticmethod
    def _normalize_transport(payload: object, *, prefix: str) -> object:
        """Normalize only an unambiguous list-of-tagged-records transport shape."""

        if not isinstance(payload, dict):
            return payload
        normalized = deepcopy(payload)
        if isinstance(normalized.get("records"), list):
            grouped = {name: [] for name in _RECORD_GROUPS}
            for item in normalized["records"]:
                if not isinstance(item, dict):
                    return payload
                group = item.get("group") or item.get("record_group")
                record = item.get("record")
                if group not in grouped or not isinstance(record, dict):
                    return payload
                grouped[group].append(record)
            normalized["records"] = grouped
        records = normalized.get("records")
        if not isinstance(records, dict):
            return normalized
        for operator in records.get("operators", []):
            if not isinstance(operator, dict):
                continue
            command_groups = (
                operator.get("always_commands", []),
                operator.get("success_commands", []),
                operator.get("failure_commands", []),
            )
            for commands in command_groups:
                for command in commands if isinstance(commands, list) else ():
                    path = command.get("path") if isinstance(command, dict) else None
                    if (
                        command.get("kind") == "set_fact"
                        and isinstance(path, str)
                        and path.startswith("facts." + prefix)
                    ):
                        command["path"] = path.removeprefix("facts.")
        return normalized

    @staticmethod
    def _messages(
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_intent: str,
        proposal_id: str,
        allowed_skill_keys: tuple[str, ...],
        intent_plan: ActionIntentPlan,
        errors: list[str],
    ) -> list[ChatMessage]:
        prefix = f"expansion.{proposal_id}."
        catalog = {
            "scene_id": snapshot.scene_id,
            "locations": [item.location_id for item in contract.locations],
            "entities": [item.entity_id for item in contract.entities],
            "clocks": [item.clock_id for item in contract.clocks],
            "resources": [item.resource_id for item in contract.resources],
            "operators": [item.operator_id for item in contract.operators],
            "consequence_signals": [
                item.signal_id for item in contract.consequence_signals
            ],
            "state": {
                "facts": snapshot.facts,
                "entities": snapshot.entities,
                "actor_locations": snapshot.actor_locations,
                "resources": snapshot.resources,
                "clocks": snapshot.clocks,
            },
        }
        correction = f"\n上次输出未通过结构校验：{errors[-1]}" if errors else ""
        skeleton = ConstrainedWorldExpansionAuthoringAdapter._authoring_skeleton(
            intent_plan, prefix=prefix, allowed_skill_keys=allowed_skill_keys
        )
        return [
            ChatMessage(
                role="system",
                content=(
                    "你只能提出新增的运行级契约记录，只返回 JSON。不得返回或猜测"
                    "base_contract_id、base_contract_hash、base_state_version、source_version、"
                    "结局或删除操作。所有新 ID 必须以指定 namespace 开头。每个新增行动的"
                    "每种结果必须显式消耗时间或资源；效果必须有界。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"玩家意图：{player_intent}\nnamespace：{prefix}\n当前只读目录："
                    f"{json.dumps(catalog, ensure_ascii=False)}\n"
                    "只允许使用以下玩家明确提出且由规则集解析的技能键："
                    f"{json.dumps(allowed_skill_keys, ensure_ascii=False)}。如果目录为空，"
                    "只能生成不含 skill_choices 的 automatic 行动，不能自行增加检定。"
                    "以下 intent_plan 已由独立阶段冻结，不得改变步骤、依赖、需求或效果："
                    f"{intent_plan.model_dump_json(exclude_none=True, exclude_defaults=True)}。"
                    "每个 intent step 必须一一生成同 ID 绑定的"
                    "operator；多步骤必须由唯一 task_method 原样保存 step_id、operator_id 和"
                    "depends_on。每项 effect 必须有匹配 command_kind 与 target_ref 的实际命令。"
                    "value、delta、actor_id 与 payload 已由 intent_plan 冻结，必须逐值保持；"
                    "不得添加 intent_plan 未声明的状态变更命令。"
                    "返回 {confidence,assumptions,rationale,records}；records 只能含 "
                    "locations,location_links,entities,clocks,resources,clues,operators,"
                    "task_methods,reactive_policies,consequence_signals。新增 operator 或 "
                    "task_method 应提供简短、多样且不重复的 intent_hints，供确定性语义检索。"
                    "records 必须是上述十个分组字段的对象，不是数组。"
                    "下列骨架已按冻结意图生成；保留 ID、依赖和命令的全部参数，"
                    "只补全可读文本与所需的有界目录记录："
                    f"{json.dumps(skeleton, ensure_ascii=False)}"
                    + correction
                ),
            ),
        ]

    @staticmethod
    def _authoring_skeleton(
        intent_plan: ActionIntentPlan,
        *,
        prefix: str,
        allowed_skill_keys: tuple[str, ...],
    ) -> dict[str, object]:
        """Build structural guidance from frozen IDs instead of a competing example."""

        operators: list[dict[str, object]] = []
        clock_ids: set[str] = set()
        for step in intent_plan.steps:
            always_commands: list[dict[str, object]] = []
            success_commands: list[dict[str, object]] = []
            failure_commands: list[dict[str, object]] = []
            pushed_failure_commands: list[dict[str, object]] = []
            for effect in step.effects:
                command = world_command_for_intent_effect(effect).model_dump(
                    mode="json",
                    exclude_defaults=True,
                    exclude_none=True,
                )
                if effect.command_kind == "advance_clock" and command.get("clock_id"):
                    clock_ids.add(str(command["clock_id"]))
                destination = {
                    "always": always_commands,
                    "success": success_commands,
                    "failure": failure_commands,
                    "pushed_failure": pushed_failure_commands,
                }[effect.applies_on]
                destination.append(command)
            operators.append({
                "operator_id": step.operator_id,
                "title": step.goal,
                "intent_hints": [step.method, step.target],
                "policy": "required_check" if allowed_skill_keys else "automatic",
                "skill_choices": ([{
                    "skill_key": allowed_skill_keys[0],
                    "difficulty": "regular",
                    "reason": "该技能与玩家明确选择的实际手段对应。",
                }] if allowed_skill_keys else []),
                "always_commands": always_commands,
                "success_commands": success_commands,
                "failure_commands": failure_commands,
                "outcome_branches": ([{
                    "outcome_key": "pushed_failure",
                    "commands": pushed_failure_commands,
                }] if pushed_failure_commands else []),
                "maximum_effect": "不超过冻结意图声明的有界效果。",
            })
        task_methods: list[dict[str, object]] = []
        if len(intent_plan.steps) > 1:
            task_methods.append({
                "method_id": prefix + "method",
                "task_key": "dynamic-intent",
                "title": intent_plan.goal,
                "steps": [{
                    "step_id": step.step_id,
                    "operator_id": step.operator_id,
                    "depends_on": list(step.depends_on),
                } for step in intent_plan.steps],
            })
        return {
            "confidence": "high",
            "assumptions": ["仅保留执行冻结意图所需的最小可核查假设。"],
            "rationale": "将冻结的意图步骤编译为有界契约记录。",
            "records": {
                "locations": [],
                "location_links": [],
                "entities": [],
                "clocks": [{
                    "clock_id": clock_id,
                    "title": "意图声明的时间成本",
                    "maximum_value": 4,
                } for clock_id in sorted(clock_ids) if clock_id.startswith(prefix)],
                "resources": [],
                "clues": [],
                "operators": operators,
                "task_methods": task_methods,
                "reactive_policies": [],
                "consequence_signals": [],
            },
        }

__all__ = [
    "ConstrainedWorldExpansionAuthoringAdapter",
    "WorldExpansionAuthoringResult",
    "WorldExpansionDraft",
]

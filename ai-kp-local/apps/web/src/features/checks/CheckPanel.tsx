import { Dice5, EyeOff, RefreshCw, RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  AuthIdentity,
  CreateOpposedCheckInput,
  CreateSkillCheckInput,
  OpposedCheck,
  SessionMember,
  SkillCheck
} from "../../api/types";
import { statusLabel } from "../../ui/statusLabels";
import { DiceRollResult } from "./DiceRollResult";

type Props = {
  identity: AuthIdentity | null;
  checks: SkillCheck[];
  opposedChecks: OpposedCheck[];
  members: SessionMember[];
  loading: boolean;
  onRefresh: () => void;
  onCreate: (input: CreateSkillCheckInput) => void;
  onCreateOpposed: (input: CreateOpposedCheckInput) => void;
  onResolveOpposed: (opposedCheckId: string) => void;
  onRerollOpposed: (opposedCheckId: string) => void;
  onResolveDigital: (checkId: string) => void;
  onResolvePhysical: (checkId: string, onesDigit: number, tensDigits: number[]) => void;
  onGenerateConsequence: (checkId: string) => void;
  onReplay: (checkId: string) => void;
  onOverride: (
    checkId: string,
    successLevel: NonNullable<SkillCheck["success_level"]>,
    passed: boolean,
    reason: string
  ) => void;
  onCancel: (checkId: string, reason: string) => void;
  onPush: (checkId: string, reason: string) => void;
};

export function CheckPanel(props: Props) {
  const { t } = useTranslation();
  const levelLabels: Record<NonNullable<SkillCheck["success_level"]>, string> = {
    fumble: t("gamedata.levels.fumble"),
    failure: t("gamedata.levels.failure"),
    regular: t("gamedata.levels.regular"),
    hard: t("gamedata.levels.hard"),
    extreme: t("gamedata.levels.extreme"),
    critical: t("gamedata.levels.critical")
  };
  const difficultyLabels: Record<SkillCheck["difficulty"], string> = {
    regular: t("gamedata.difficulties.regular"),
    hard: t("gamedata.difficulties.hard"),
    extreme: t("gamedata.difficulties.extreme")
  };
  const visibilityLabels: Record<SkillCheck["visibility"], string> = {
    public: t("gamedata.visibility.public"),
    private: t("gamedata.visibility.private"),
    blind: t("gamedata.visibility.blind")
  };
  const [skillName, setSkillName] = useState("侦查");
  const [difficulty, setDifficulty] = useState<SkillCheck["difficulty"]>("regular");
  const [bonusDice, setBonusDice] = useState(0);
  const [target, setTarget] = useState("50");
  const [rollerMemberId, setRollerMemberId] = useState("");
  const [visibility, setVisibility] = useState<SkillCheck["visibility"]>("public");
  const [allowPush, setAllowPush] = useState(true);
  const [physicalDrafts, setPhysicalDrafts] = useState<Record<string, { ones: string; tens: string }>>({});
  const [decisionReason, setDecisionReason] = useState("KP 根据现场裁定");
  const [overrideLevel, setOverrideLevel] = useState<NonNullable<SkillCheck["success_level"]>>("regular");
  const [opposedDraft, setOpposedDraft] = useState({
    leftSkill: "斗殴",
    leftTarget: "50",
    leftMemberId: "",
    rightSkill: "斗殴",
    rightTarget: "50",
    rightMemberId: ""
  });

  const playerMembers = useMemo(
    () => props.members.filter((member) => member.role === "player" && !member.revoked_at),
    [props.members]
  );
  const visibilityNeedsRoller = visibility === "private";

  function submitCheck() {
    const roller = playerMembers.find((member) => member.id === rollerMemberId);
    props.onCreate({
      skill_name: skillName,
      difficulty,
      bonus_dice: bonusDice,
      visibility,
      allow_push: allowPush,
      roller_member_id: roller?.id ?? null,
      pc_id: roller?.pc_id ?? null,
      target: target.trim() ? Number(target) : null
    });
  }

  function physicalDraft(check: SkillCheck) {
    return physicalDrafts[check.id] ?? { ones: "", tens: "" };
  }

  function resolvePhysical(check: SkillCheck) {
    const draft = physicalDraft(check);
    const onesDigit = Number(draft.ones);
    const tensDigits = draft.tens
      .split(/[,，\s]+/)
      .map((item) => Number(item))
      .filter((item) => Number.isInteger(item));
    props.onResolvePhysical(check.id, onesDigit, tensDigits);
  }

  return (
    <section className="tool-panel check-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">确定性 CoC7 规则</p>
          <h2>待检定与掷骰</h2>
        </div>
        <Dice5 size={19} />
      </div>
      <button className="ghost-button" onClick={props.onRefresh} type="button">
        <RefreshCw size={15} />刷新检定
      </button>

      {props.identity?.role === "kp" && (
        <details className="check-create" open={!props.checks.length}>
          <summary>创建检定</summary>
          <div className="check-create-grid">
            <label>技能或属性<input value={skillName} onChange={(event) => setSkillName(event.target.value)} /></label>
            <label>
              玩家
              <select value={rollerMemberId} onChange={(event) => setRollerMemberId(event.target.value)}>
                <option value="">KP / 暂不指定</option>
                {playerMembers.map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}
              </select>
            </label>
            <label>目标值<input max={100} min={0} type="number" value={target} onChange={(event) => setTarget(event.target.value)} /></label>
            <label>
              难度
              <select value={difficulty} onChange={(event) => setDifficulty(event.target.value as SkillCheck["difficulty"])}>
                <option value="regular">常规</option><option value="hard">困难</option><option value="extreme">极难</option>
              </select>
            </label>
            <label>
              奖惩骰
              <select value={bonusDice} onChange={(event) => setBonusDice(Number(event.target.value))}>
                <option value={2}>2 奖励骰</option><option value={1}>1 奖励骰</option><option value={0}>无</option><option value={-1}>1 惩罚骰</option><option value={-2}>2 惩罚骰</option>
              </select>
            </label>
          </div>
          <div className="check-options">
            <label>
              可见范围
              <select
                aria-label="可见范围"
                value={visibility}
                onChange={(event) => setVisibility(event.target.value as SkillCheck["visibility"])}
              >
                <option value="public">全桌公开</option>
                <option disabled={!rollerMemberId} value="private">掷骰者与 KP</option>
                <option value="blind">仅 KP（暗骰）</option>
              </select>
            </label>
            <label><input checked={allowPush} onChange={(event) => setAllowPush(event.target.checked)} type="checkbox" />允许孤注一掷</label>
          </div>
          {visibilityNeedsRoller && !rollerMemberId && <p className="form-hint">这个可见范围必须先指定一名玩家。</p>}
          <button className="primary-button" disabled={props.loading || !skillName.trim() || (visibilityNeedsRoller && !rollerMemberId)} onClick={submitCheck} type="button"><Dice5 size={15} />发布检定</button>
        </details>
      )}

      {props.identity?.role === "kp" && (
        <details className="check-create opposed-create">
          <summary>创建对抗检定</summary>
          <p className="form-hint">两边分别掷骰；服务器按成功等级、技能值、较低骰值依次裁决。完全相同才要求重掷。</p>
          <div className="check-create-grid">
            <label>左方技能<input value={opposedDraft.leftSkill} onChange={(event) => setOpposedDraft((value) => ({ ...value, leftSkill: event.target.value }))} /></label>
            <label>左方目标<input min={0} max={100} type="number" value={opposedDraft.leftTarget} onChange={(event) => setOpposedDraft((value) => ({ ...value, leftTarget: event.target.value }))} /></label>
            <label>左方玩家<select value={opposedDraft.leftMemberId} onChange={(event) => setOpposedDraft((value) => ({ ...value, leftMemberId: event.target.value }))}><option value="">NPC / KP</option>{playerMembers.map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}</select></label>
            <label>右方技能<input value={opposedDraft.rightSkill} onChange={(event) => setOpposedDraft((value) => ({ ...value, rightSkill: event.target.value }))} /></label>
            <label>右方目标<input min={0} max={100} type="number" value={opposedDraft.rightTarget} onChange={(event) => setOpposedDraft((value) => ({ ...value, rightTarget: event.target.value }))} /></label>
            <label>右方玩家<select value={opposedDraft.rightMemberId} onChange={(event) => setOpposedDraft((value) => ({ ...value, rightMemberId: event.target.value }))}><option value="">NPC / KP</option>{playerMembers.map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}</select></label>
          </div>
          <button
            className="primary-button"
            disabled={props.loading || !opposedDraft.leftSkill.trim() || !opposedDraft.rightSkill.trim()}
            onClick={() => {
              const side = (skill: string, targetValue: string, memberId: string) => {
                const member = playerMembers.find((item) => item.id === memberId);
                return {
                  skill_name: skill,
                  target: targetValue.trim() ? Number(targetValue) : null,
                  bonus_dice: 0,
                  visibility: "public" as const,
                  roller_member_id: member?.id ?? null,
                  pc_id: member?.pc_id ?? null
                };
              };
              props.onCreateOpposed({
                left: side(opposedDraft.leftSkill, opposedDraft.leftTarget, opposedDraft.leftMemberId),
                right: side(opposedDraft.rightSkill, opposedDraft.rightTarget, opposedDraft.rightMemberId)
              });
            }}
            type="button"
          ><Dice5 size={15} />发布双方检定</button>
        </details>
      )}

      {props.opposedChecks.length > 0 && (
        <div className="opposed-list" aria-label="对抗检定">
          <h3>对抗检定</h3>
          {props.opposedChecks.map((contest) => {
            const winner = contest.result?.winner_id === contest.left_check_id
              ? contest.left_check.skill_name
              : contest.result?.winner_id === contest.right_check_id
                ? contest.right_check.skill_name
                : null;
            const ready = [contest.left_check, contest.right_check].every((item) =>
              ["resolved", "overridden"].includes(item.status)
            );
            return <article className="check-card opposed-card" key={contest.id}>
              <div className="check-card-heading"><strong>{contest.left_check.skill_name} vs {contest.right_check.skill_name}</strong><span>{statusLabel(contest.status)}</span></div>
              <small>左 {contest.left_check.target} / {contest.left_check.selected_roll ?? "待掷"} · 右 {contest.right_check.target} / {contest.right_check.selected_roll ?? "待掷"}</small>
              <div className="opposed-roll-results">
                <DiceRollResult check={contest.left_check} compact />
                <DiceRollResult check={contest.right_check} compact />
              </div>
              {contest.result && <div className="check-result"><strong>{winner ? `${winner} 获胜` : "完全相同，双方重掷"}</strong><span>裁决依据：{contest.result.decided_by}</span></div>}
              {props.identity?.role === "kp" && contest.status === "pending" && <button className="primary-button" disabled={!ready || props.loading} onClick={() => props.onResolveOpposed(contest.id)} type="button">裁决对抗结果</button>}
              {props.identity?.role === "kp" && contest.status === "reroll_required" && <button className="primary-button" disabled={props.loading} onClick={() => props.onRerollOpposed(contest.id)} type="button">创建双方重掷</button>}
            </article>;
          })}
        </div>
      )}

      <div className="check-list">
        {props.checks.length ? props.checks.map((check) => {
          const draft = physicalDraft(check);
          const canResolve = check.status === "requested" && (
            props.identity?.role === "kp" || check.roller_member_id === props.identity?.member_id
          );
          const linkedChecks = check.player_action_id
            ? props.checks.filter((item) => item.player_action_id === check.player_action_id)
            : [];
          const consequenceReady = linkedChecks.length > 0
            && linkedChecks.every((item) =>
              ["resolved", "overridden", "cancelled"].includes(item.status))
            && !props.checks.some((item) => item.pushed_from_check_id === check.id);
          return (
            <article className={`check-card ${check.status} ${check.passed === true ? "passed" : check.passed === false ? "failed" : ""}`} key={check.id}>
              <div className="check-card-heading">
                <div><strong>{check.skill_name}</strong><small>{difficultyLabels[check.difficulty]} · 目标 {check.target} / 门槛 {check.status === "requested" ? "待掷骰" : check.threshold}</small></div>
                <span>
                  {check.visibility !== "public"
                    ? <><EyeOff size={13} /> {visibilityLabels[check.visibility]}</>
                    : statusLabel(check.status)}
                </span>
              </div>
              <div className="check-facts">
                <span>{check.bonus_dice > 0 ? `${check.bonus_dice} 奖励骰` : check.bonus_dice < 0 ? `${Math.abs(check.bonus_dice)} 惩罚骰` : "普通百分骰"}</span>
                <span>规则 {check.ruleset_version} · 书内 {check.source_reference.page_start}-{check.source_reference.page_end} 页</span>
              </div>

              {canResolve && (
                <>
                  <button className="primary-button" disabled={props.loading} onClick={() => props.onResolveDigital(check.id)} type="button"><Dice5 size={15} />数字骰</button>
                  <details className="physical-dice-entry">
                    <summary>录入实体骰</summary>
                    <div>
                      <label>个位<input max={9} min={0} type="number" value={draft.ones} onChange={(event) => setPhysicalDrafts((items) => ({ ...items, [check.id]: { ...draft, ones: event.target.value } }))} /></label>
                      <label>十位骰（{Math.abs(check.bonus_dice) + 1} 枚，以逗号分隔）<input placeholder={check.bonus_dice ? "例如 2, 4" : "例如 6"} value={draft.tens} onChange={(event) => setPhysicalDrafts((items) => ({ ...items, [check.id]: { ...draft, tens: event.target.value } }))} /></label>
                    </div>
                    <button className="secondary-button" onClick={() => resolvePhysical(check)} type="button">确认实体骰</button>
                  </details>
                </>
              )}

              {check.status !== "requested" && check.status !== "cancelled" && (
                <DiceRollResult check={check} />
              )}

              {check.status !== "requested" && check.status !== "cancelled" && (
                <button className="ghost-button" onClick={() => props.onReplay(check.id)} type="button"><RotateCcw size={14} />重放校验</button>
              )}

              {props.identity?.role === "kp"
                && check.player_action_id
                && consequenceReady && (
                <button
                  className="primary-button"
                  disabled={props.loading}
                  onClick={() => props.onGenerateConsequence(check.id)}
                  type="button"
                >
                  生成/打开检定后果草稿
                </button>
              )}

              {props.identity?.role === "kp" && (
                <details className="check-kp-tools">
                  <summary>KP 裁定与状态操作</summary>
                  <label>理由或后果<input value={decisionReason} onChange={(event) => setDecisionReason(event.target.value)} /></label>
                  {check.status === "requested" ? (
                    <button className="secondary-button" onClick={() => props.onCancel(check.id, decisionReason)} type="button">取消检定</button>
                  ) : (
                    <>
                      <label>覆盖结果<select value={overrideLevel} onChange={(event) => setOverrideLevel(event.target.value as NonNullable<SkillCheck["success_level"]>)}>{Object.entries(levelLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                      <button className="secondary-button" onClick={() => props.onOverride(check.id, overrideLevel, !["failure", "fumble"].includes(overrideLevel), decisionReason)} type="button">记录 KP 覆盖</button>
                      {!check.passed && check.allow_push && <button className="ghost-button" onClick={() => props.onPush(check.id, decisionReason)} type="button">创建孤注一掷</button>}
                    </>
                  )}
                </details>
              )}
            </article>
          );
        }) : <p className="empty-note">暂无待检定。AI 或 KP 创建后会在这里出现。</p>}
      </div>
    </section>
  );
}

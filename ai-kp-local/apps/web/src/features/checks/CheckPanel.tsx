import { Dice5, EyeOff, RefreshCw, RotateCcw, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";

import type {
  AuthIdentity,
  CreateSkillCheckInput,
  SessionMember,
  SkillCheck
} from "../../api/types";
import { statusLabel } from "../../ui/statusLabels";

type Props = {
  identity: AuthIdentity | null;
  checks: SkillCheck[];
  members: SessionMember[];
  loading: boolean;
  onRefresh: () => void;
  onCreate: (input: CreateSkillCheckInput) => void;
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

const levelLabels: Record<NonNullable<SkillCheck["success_level"]>, string> = {
  fumble: "大失败",
  failure: "失败",
  regular: "常规成功",
  hard: "困难成功",
  extreme: "极难成功",
  critical: "大成功"
};

const difficultyLabels: Record<SkillCheck["difficulty"], string> = {
  regular: "常规",
  hard: "困难",
  extreme: "极难"
};

export function CheckPanel(props: Props) {
  const [skillName, setSkillName] = useState("侦查");
  const [difficulty, setDifficulty] = useState<SkillCheck["difficulty"]>("regular");
  const [bonusDice, setBonusDice] = useState(0);
  const [target, setTarget] = useState("50");
  const [rollerMemberId, setRollerMemberId] = useState("");
  const [hidden, setHidden] = useState(false);
  const [allowPush, setAllowPush] = useState(true);
  const [physicalDrafts, setPhysicalDrafts] = useState<Record<string, { ones: string; tens: string }>>({});
  const [decisionReason, setDecisionReason] = useState("KP 根据现场裁定");
  const [overrideLevel, setOverrideLevel] = useState<NonNullable<SkillCheck["success_level"]>>("regular");

  const playerMembers = useMemo(
    () => props.members.filter((member) => member.role === "player" && !member.revoked_at),
    [props.members]
  );

  function submitCheck() {
    const roller = playerMembers.find((member) => member.id === rollerMemberId);
    props.onCreate({
      skill_name: skillName,
      difficulty,
      bonus_dice: bonusDice,
      hidden,
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
            <label><input checked={hidden} onChange={(event) => setHidden(event.target.checked)} type="checkbox" />暗骰</label>
            <label><input checked={allowPush} onChange={(event) => setAllowPush(event.target.checked)} type="checkbox" />允许孤注一掷</label>
          </div>
          <button className="primary-button" disabled={props.loading || !skillName.trim()} onClick={submitCheck} type="button"><Dice5 size={15} />发布检定</button>
        </details>
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
                  {check.hidden
                    ? <><EyeOff size={13} /> 暗骰</>
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
                <div className="check-result">
                  <strong>{check.success_level ? levelLabels[check.success_level] : "未知"}</strong>
                  <span>D100 = {check.selected_roll} · {check.passed ? "通过难度" : "未通过难度"}</span>
                  {check.raw_dice && <small>个位 {check.raw_dice.ones_digit} · 十位 {check.raw_dice.tens_digits.join(" / ")} · 候选 {check.raw_dice.candidates.join(" / ")}</small>}
                  {check.override_reason && <small><ShieldAlert size={12} /> KP 覆盖：{check.override_reason}</small>}
                </div>
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

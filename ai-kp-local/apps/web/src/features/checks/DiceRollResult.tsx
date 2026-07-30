import { ChevronDown, Eye, EyeOff } from "lucide-react";

import type { SkillCheck } from "../../api/types";

type Props = {
  check: SkillCheck;
  compact?: boolean;
};

const levelLabels: Record<NonNullable<SkillCheck["success_level"]>, string> = {
  fumble: "大失败",
  failure: "失败",
  regular: "常规成功",
  hard: "困难成功",
  extreme: "极难成功",
  critical: "大成功"
};

function percentileLabel(value: number) {
  return String(value * 10).padStart(2, "0");
}

function rollFormula(check: SkillCheck) {
  if (check.bonus_dice > 0) return `D100（${check.bonus_dice} 奖励骰）`;
  if (check.bonus_dice < 0) return `D100（${Math.abs(check.bonus_dice)} 惩罚骰）`;
  return "D100";
}

const visibilityLabels: Record<SkillCheck["visibility"], string> = {
  public: "全桌公开",
  private: "掷骰者与 KP",
  blind: "仅 KP 可见"
};

export function DiceRollResult({ check, compact = false }: Props) {
  if (
    check.status === "requested"
    || check.status === "cancelled"
    || check.selected_roll === null
  ) return null;

  const isPassed = check.passed === true;
  const resultLabel = check.success_level ? levelLabels[check.success_level] : "未知结果";

  return (
    <section
      aria-label={`${check.skill_name}投骰结果`}
      aria-live="polite"
      className={`dice-roll-result ${isPassed ? "passed" : "failed"} ${compact ? "compact" : ""}`}
    >
      <div className="dice-roll-summary">
        <div className="dice-roll-identity">
          <span className="dice-visibility">
            {check.visibility === "public" ? <Eye size={13} /> : <EyeOff size={13} />}
            {visibilityLabels[check.visibility]}
          </span>
          <strong>{rollFormula(check)}</strong>
        </div>
        <div className="dice-roll-total">
          <span>掷出</span>
          <b>{String(check.selected_roll).padStart(2, "0")}</b>
        </div>
        <div className="dice-roll-verdict">
          <strong>{resultLabel}</strong>
          <span>目标 {check.target} · 门槛 {check.threshold ?? "—"}</span>
        </div>
      </div>

      {!compact && check.raw_dice && (
        <details className="dice-roll-details">
          <summary>
            查看每颗骰子与计算过程
            <ChevronDown size={14} />
          </summary>
          <div className="dice-parts">
            {check.raw_dice.tens_digits.map((digit, index) => (
              <span className="percentile-die" key={`${digit}-${index}`}>
                <small>{index === 0 ? "十位" : check.bonus_dice > 0 ? "奖励" : "惩罚"}</small>
                <b>{percentileLabel(digit)}</b>
              </span>
            ))}
            <span className="percentile-die ones">
              <small>个位</small>
              <b>{check.raw_dice.ones_digit}</b>
            </span>
          </div>
          <p>
            候选值 {check.raw_dice.candidates.map((value) => String(value).padStart(2, "0")).join(" / ")}
            {" · "}
            {check.bonus_dice > 0
              ? "奖励骰取较小值"
              : check.bonus_dice < 0
                ? "惩罚骰取较大值"
                : "采用唯一结果"}
          </p>
          <small>
            {check.input_method === "physical" ? "实体骰录入" : "服务器数字骰"}
            {" · "}
            {check.resolved_at ? new Date(check.resolved_at).toLocaleString("zh-CN") : "时间未记录"}
          </small>
        </details>
      )}

      {check.override_reason && (
        <p className="dice-roll-override">KP 覆盖：{check.override_reason}</p>
      )}
    </section>
  );
}

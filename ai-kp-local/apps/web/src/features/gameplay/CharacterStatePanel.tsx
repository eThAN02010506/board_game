import { HeartPulse, Sparkles } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";

import type {
  CharacterSheet,
  Coc7CharacterGameplayState
} from "../../api/types";

export type CharacterCommand =
  | "damage"
  | "major_wound_con"
  | "dying_con"
  | "first_aid"
  | "medicine"
  | "natural_healing"
  | "sanity"
  | "end_bout"
  | "reset_san_day"
  | "development";

export type CharacterDraft = {
  amount: string;
  passed: boolean;
  sanityRoll: string;
  successLoss: string;
  failureLoss: string;
  intelligenceRoll: string;
  skillKey: string;
  developmentRoll: string;
  increaseRoll: string;
  periodId: string;
  conSuccessLevel: string;
};

type Props = {
  state: Coc7CharacterGameplayState;
  isKeeper: boolean;
  busy: boolean;
  command: CharacterCommand;
  setCommand: Dispatch<SetStateAction<CharacterCommand>>;
  draft: CharacterDraft;
  setDraft: Dispatch<SetStateAction<CharacterDraft>>;
  growthSkills: CharacterSheet["skills"];
  onExecute: () => void;
};

const CONDITION_LABELS: Record<string, string> = {
  major_wound: "重伤",
  unconscious: "昏迷",
  dying: "濒死",
  dead: "死亡",
  temporary_insanity: "临时疯狂",
  indefinite_insanity: "不定期疯狂",
  permanent_insanity: "永久疯狂",
  bout_of_madness: "疯狂发作",
  development_pending: "成长待确认",
  skill_growth_mark: "技能成长标记",
  first_aid_applied: "本次伤势已急救",
  medicine_applied: "本次伤势已医学治疗",
  natural_healing_period: "自然恢复已结算"
};

function conditionLabel(condition: Record<string, unknown>) {
  const type = String(condition.type ?? "状态");
  return CONDITION_LABELS[type] ?? type;
}

export function CharacterStatePanel({
  state,
  isKeeper,
  busy,
  command,
  setCommand,
  draft,
  setDraft,
  growthSkills,
  onExecute
}: Props) {
  return (
    <article className="gameplay-character-state">
      <div>
        <HeartPulse size={17} />
        <strong>
          HP {state.state.current_hp} · SAN {state.state.current_san} · MP{" "}
          {state.state.current_mp}
        </strong>
        <small>版本 {state.state.state_version}</small>
      </div>
      <div className="condition-chips">
        {state.state.conditions.length ? (
          state.state.conditions.map((condition, index) => (
            <span key={`${String(condition.type)}-${index}`}>
              {conditionLabel(condition)}
            </span>
          ))
        ) : (
          <span className="quiet">无活动状态</span>
        )}
      </div>
      {isKeeper && (
        <details>
          <summary>伤害、治疗、理智与成长</summary>
          <div className="gameplay-command-grid">
            <label>
              操作
              <select
                value={command}
                onChange={(event) =>
                  setCommand(event.target.value as CharacterCommand)
                }
              >
                <option value="damage">承受伤害</option>
                <option value="major_wound_con">重伤 CON</option>
                <option value="dying_con">濒死 CON</option>
                <option value="first_aid">急救</option>
                <option value="medicine">医学治疗</option>
                <option value="natural_healing">自然恢复</option>
                <option value="sanity">理智检定</option>
                <option value="end_bout">结束疯狂发作</option>
                <option value="reset_san_day">开始新的 SAN 日</option>
                <option value="development">成长检定</option>
              </select>
            </label>
            {(command === "damage" ||
              command === "medicine" ||
              command === "natural_healing") && (
              <label>
                {command === "damage"
                  ? "伤害"
                  : command === "medicine"
                    ? "恢复量"
                    : "经过天数"}
                <input
                  min={0}
                  type="number"
                  value={draft.amount}
                  onChange={(event) =>
                    setDraft((value) => ({
                      ...value,
                      amount: event.target.value
                    }))
                  }
                />
              </label>
            )}
            {command === "natural_healing" && (
              <>
                <label>
                  游戏内日期 / 周期
                  <input
                    value={draft.periodId}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        periodId: event.target.value
                      }))
                    }
                  />
                </label>
                <label>
                  重伤 CON 结果
                  <select
                    value={draft.conSuccessLevel}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        conSuccessLevel: event.target.value
                      }))
                    }
                  >
                    <option value="failure">失败</option>
                    <option value="regular">常规成功</option>
                    <option value="hard">困难成功</option>
                    <option value="extreme">极难成功</option>
                    <option value="critical">大成功</option>
                  </select>
                </label>
              </>
            )}
            {(["major_wound_con", "dying_con", "first_aid", "medicine"] as const).includes(
              command as "major_wound_con"
            ) && (
              <label className="inline-check">
                <input
                  checked={draft.passed}
                  onChange={(event) =>
                    setDraft((value) => ({
                      ...value,
                      passed: event.target.checked
                    }))
                  }
                  type="checkbox"
                />
                检定成功
              </label>
            )}
            {command === "sanity" && (
              <>
                {[
                  ["SAN 骰", "sanityRoll", 1, 100],
                  ["成功损失", "successLoss", 0, undefined],
                  ["失败损失", "failureLoss", 0, undefined],
                  ["INT 骰", "intelligenceRoll", 1, 100]
                ].map(([label, key, min, max]) => (
                  <label key={String(key)}>
                    {label}
                    <input
                      min={Number(min)}
                      max={max === undefined ? undefined : Number(max)}
                      type="number"
                      value={String(draft[key as keyof CharacterDraft])}
                      onChange={(event) =>
                        setDraft((value) => ({
                          ...value,
                          [String(key)]: event.target.value
                        }))
                      }
                    />
                  </label>
                ))}
              </>
            )}
            {command === "development" && (
              <>
                <label>
                  已标记技能
                  <select
                    value={draft.skillKey}
                    onChange={(event) =>
                      setDraft((value) => ({
                        ...value,
                        skillKey: event.target.value
                      }))
                    }
                  >
                    <option value="">选择技能</option>
                    {growthSkills.map((skill) => (
                      <option
                        key={`${skill.skill_key}-${skill.specialization ?? ""}`}
                        value={skill.skill_key}
                      >
                        {skill.display_name}
                        {skill.specialization ? `（${skill.specialization}）` : ""}
                      </option>
                    ))}
                  </select>
                </label>
                {[
                  ["成长 D100", "developmentRoll", 100],
                  ["增长 D10", "increaseRoll", 10]
                ].map(([label, key, max]) => (
                  <label key={String(key)}>
                    {label}
                    <input
                      min={1}
                      max={Number(max)}
                      type="number"
                      value={String(draft[key as keyof CharacterDraft])}
                      onChange={(event) =>
                        setDraft((value) => ({
                          ...value,
                          [String(key)]: event.target.value
                        }))
                      }
                    />
                  </label>
                ))}
              </>
            )}
          </div>
          <button
            className="primary-button"
            disabled={busy}
            onClick={onExecute}
            type="button"
          >
            <Sparkles size={14} />执行并记录
          </button>
        </details>
      )}
    </article>
  );
}

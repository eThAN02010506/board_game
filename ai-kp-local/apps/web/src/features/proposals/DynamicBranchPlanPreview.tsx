import { GitBranch, ShieldCheck } from "lucide-react";

import type { DynamicBranchPlan } from "../../api/types";

type Props = {
  plan: DynamicBranchPlan;
};

export function DynamicBranchPlanPreview({ plan }: Props) {
  return (
    <section className="branch-plan-preview" aria-label="动态支线计划">
      <header>
        <GitBranch size={16} />
        <span>
          <strong>动态支线计划</strong>
          <small>批准后等待玩家实际接触才会启动</small>
        </span>
      </header>
      <p>{plan.goal}</p>
      <ol>
        {plan.beats.map((beat) => (
          <li key={beat.beat_id}>
            <strong>{beat.title}</strong>
            <span>{beat.action}</span>
            <small>失败策略：{beat.failure_policy}</small>
          </li>
        ))}
      </ol>
      {!!plan.anchor_guards.length && (
        <details>
          <summary><ShieldCheck size={14} />锚点保护（{plan.anchor_guards.length}）</summary>
          <ul>
            {plan.anchor_guards.map((guard) => (
              <li key={guard.anchor_entity_id}>
                <strong>{guard.invariant}</strong>
                <span>恢复：{guard.recovery}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <small>
        节拍中的效果均为候选；World Fact、NPC 与地图仍需在实际接触后单独确认。
      </small>
    </section>
  );
}

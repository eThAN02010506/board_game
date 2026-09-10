import { Clock3, Dice5, Users } from "lucide-react";

import type { ParallelActionPlayerBatch } from "../../api/types";

type Props = {
  batch: ParallelActionPlayerBatch;
  error?: string;
  onOpenChecks?: () => void;
};

const phaseLabels: Record<ParallelActionPlayerBatch["self_phase"], string> = {
  awaiting_confirmation: "等待你确认裁定",
  waiting_for_others: "你的裁定已确认，等待其他玩家",
  awaiting_check: "等待你完成检定",
  awaiting_push_decision: "等待你决定是否孤注一掷",
  waiting_for_checks: "你的步骤已完成，等待其他检定",
  ready: "所有行动已就绪",
  settling: "AI KP 正在统一结算",
  settled: "本轮多人行动已结算",
  needs_attention: "本轮需要 KP 处理",
  superseded: "本轮已由新行动替代"
};

export function ParallelActionBatchStatus({ batch, error, onOpenChecks }: Props) {
  const canOpenChecks = ["awaiting_check", "awaiting_push_decision"].includes(
    batch.self_phase
  );
  return (
    <section
      aria-label="多人并行动作状态"
      className={`parallel-action-status ${batch.self_phase}`}
    >
      <div aria-label="多人并行动作状态" aria-live="polite" role="status">
        <div className="parallel-action-status-heading">
          <div>
            <small>多人并行动作</small>
            <strong>{phaseLabels[batch.self_phase]}</strong>
          </div>
          <Users aria-hidden="true" size={20} />
        </div>
        <p>{batch.public_message}</p>
        <dl className="parallel-action-metrics">
          <div>
            <dt>参与</dt>
            <dd>{batch.participant_count} 人</dd>
          </div>
          <div>
            <dt>已确认</dt>
            <dd>{batch.confirmed_count} 人</dd>
          </div>
          <div>
            <dt>待确认</dt>
            <dd>{batch.waiting_count} 人</dd>
          </div>
        </dl>
        {error && (
          <small className="parallel-action-refresh-note">
            <Clock3 aria-hidden="true" size={13} />状态更新暂时中断，正在保留上次结果并重试。
          </small>
        )}
      </div>
      {canOpenChecks && onOpenChecks && (
        <button className="primary-button" onClick={onOpenChecks} type="button">
          <Dice5 aria-hidden="true" size={16} />前往检定
        </button>
      )}
    </section>
  );
}

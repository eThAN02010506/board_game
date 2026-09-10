import { Clock3, UsersRound } from "lucide-react";

import type { ParallelActionPlayerRegather } from "../../api/types";

type Props = {
  regather: ParallelActionPlayerRegather;
  error?: string;
};

const labels: Record<ParallelActionPlayerRegather["self_phase"], string> = {
  awaiting_submission: "小队正在重新集结，等待你的新行动",
  waiting_for_others: "你的新行动已保存，等待其他玩家",
  processing: "全员已到齐，AI KP 正在重新规划"
};

export function ParallelActionRegatherStatus({ regather, error }: Props) {
  return (
    <section
      aria-label="多人行动重新集结状态"
      className={`parallel-action-status regather ${regather.self_phase}`}
    >
      <div aria-live="polite" role="status">
        <div className="parallel-action-status-heading">
          <div>
            <small>多人行动重新集结</small>
            <strong>{labels[regather.self_phase]}</strong>
          </div>
          <UsersRound aria-hidden="true" size={20} />
        </div>
        <p>{regather.public_message}</p>
        <dl className="parallel-action-metrics">
          <div><dt>参与</dt><dd>{regather.participant_count} 人</dd></div>
          <div><dt>已提交</dt><dd>{regather.submitted_count} 人</dd></div>
          <div><dt>待提交</dt><dd>{regather.waiting_count} 人</dd></div>
        </dl>
        {error && (
          <small className="parallel-action-refresh-note">
            <Clock3 aria-hidden="true" size={13} />状态更新暂时中断，正在安全重试。
          </small>
        )}
      </div>
    </section>
  );
}

import { Brain, MessageSquare, RefreshCw, Send } from "lucide-react";
import { useEffect, useState } from "react";
import type { ActionAdjudication, AuthIdentity, AutoKpJob, PlayerActionRecord } from "../../api/types";
import { statusLabel } from "../../ui/statusLabels";

type Props = {
  identity: AuthIdentity | null;
  loading: boolean;
  playerAction: string;
  proposalText: string;
  playerActions: PlayerActionRecord[];
  selectedPlayerActionId: string;
  autoKpEnabled: boolean;
  autoKpJobs: AutoKpJob[];
  adjudication: ActionAdjudication | null;
  onPlayerActionChange: (value: string) => void;
  onProposalTextChange: (value: string) => void;
  onAutoKpEnabledChange: (value: boolean) => void;
  onSelectPlayerAction: (action: PlayerActionRecord) => void;
  onSearchMemory: () => void;
  onSubmitPlayerAction: () => void;
  onRefreshPlayerActions: () => void;
  onRetryAutoKpJob: (jobId: string) => void;
  onCreateProposal: () => void;
  onGenerateAiProposal: () => void;
  onConfirmAdjudication: (selectedSkill: string | null) => void;
  onReviseAdjudication: () => void;
};

export function ActionPanel(props: Props) {
  const [selectedSkill, setSelectedSkill] = useState("");
  useEffect(() => {
    setSelectedSkill(props.adjudication?.selected_skill ?? "");
  }, [props.adjudication?.id, props.adjudication?.selected_skill]);
  const selectedOption = props.adjudication?.skill_options.find(
    (option) => option.skill_name === selectedSkill
  );
  return (
    <section className="tool-panel action-panel" id="memory-section">
      <div className="panel-heading">
        <h2>行动与桌面聊天</h2>
        <Brain size={18} />
      </div>
      <label>
        玩家行动
        <textarea
          value={props.playerAction}
          onChange={(event) => props.onPlayerActionChange(event.target.value)}
        />
      </label>
      <button
        className="secondary-button"
        disabled={!props.identity}
        onClick={props.onSearchMemory}
        type="button"
      >
        <Brain size={16} />
        检索记忆
      </button>
      {props.identity?.role === "player" && (
        <>
          <label className="checkbox-row">
            <input
              checked={props.autoKpEnabled}
              onChange={(event) => props.onAutoKpEnabledChange(event.target.checked)}
              type="checkbox"
            />
            AI KP 自动推进
          </label>
          <button
            className="primary-button"
            disabled={props.loading}
            onClick={props.onSubmitPlayerAction}
            type="button"
          >
            <Send size={16} />
            {props.autoKpEnabled ? "提交并后台推进" : "提交给 KP"}
          </button>
          {props.adjudication?.status === "pending" && (
            <article className={`action-ruling-card ${props.adjudication.mode}`}>
              <div className="action-ruling-heading">
                <strong>{modeLabel(props.adjudication.mode)}</strong>
                <small>AI 初步裁定 · 尚未执行</small>
              </div>
              <p>{props.adjudication.reason}</p>
              {(props.adjudication.ruling.goal || props.adjudication.ruling.method) && (
                <dl className="action-ruling-details">
                  {props.adjudication.ruling.goal && <><dt>目标</dt><dd>{props.adjudication.ruling.goal}</dd></>}
                  {props.adjudication.ruling.method && <><dt>做法</dt><dd>{props.adjudication.ruling.method}</dd></>}
                  {props.adjudication.ruling.target && <><dt>对象</dt><dd>{props.adjudication.ruling.target}</dd></>}
                  {props.adjudication.ruling.maximum_effect && <><dt>成功上限</dt><dd>{props.adjudication.ruling.maximum_effect}</dd></>}
                </dl>
              )}
              {props.adjudication.source_error && (
                <p className="permission-hint">模型输出未通过校验，系统没有自动放行。</p>
              )}
              {props.adjudication.mode === "skill_check" && (
                <label>
                  选择本次判定技能
                  <select
                    aria-label="选择本次判定技能"
                    value={selectedSkill}
                    onChange={(event) => setSelectedSkill(event.target.value)}
                  >
                    {props.adjudication.skill_options.map((option) => (
                      <option key={option.skill_key} value={option.skill_name}>
                        {option.skill_name} · {option.target} · {difficultyLabel(option.difficulty)}
                      </option>
                    ))}
                  </select>
                  {selectedOption && <small>{selectedOption.reason}</small>}
                </label>
              )}
              {props.adjudication.prompt && <p>{props.adjudication.prompt}</p>}
              {props.adjudication.mode === "roleplay_or_clarification" && (
                <p className="permission-hint">可在上方直接输入角色台词，也可概述想采取的做法；不要求现实口才表演。</p>
              )}
              <div className="action-ruling-actions">
                {props.adjudication.mode !== "roleplay_or_clarification" && (
                  <button
                    className="primary-button"
                    disabled={props.loading || (props.adjudication.mode === "skill_check" && !selectedSkill)}
                    onClick={() => props.onConfirmAdjudication(selectedSkill || null)}
                    type="button"
                  >
                    确认此裁定
                  </button>
                )}
                <button className="secondary-button" disabled={props.loading} onClick={props.onReviseAdjudication} type="button">
                  修改行动并重新裁定
                </button>
              </div>
            </article>
          )}
          {props.autoKpJobs.length > 0 && (
            <div aria-label="自动 KP 任务" className="auto-kp-player-status">
              <strong>自动 KP 状态</strong>
              <ul className="module-job-list">
                {props.autoKpJobs.slice(0, 3).map((job) => (
                  <li className={`module-job ${job.status}`} key={job.id}>
                    <div>
                      <strong>{job.job_type === "check_consequence" ? "检定后果" : "玩家行动"}</strong>
                      <span>{job.status} · {job.stage}</span>
                    </div>
                    <small>尝试 {job.attempt_count}/{job.max_attempts}</small>
                    {job.status === "failed" && (
                      <button
                        className="ghost-button"
                        disabled={props.loading}
                        onClick={() => props.onRetryAutoKpJob(job.id)}
                        type="button"
                      >
                        <RefreshCw size={14} />重新尝试
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {props.identity?.role === "kp" && (
        <>
          <div className="queue-heading">
            <strong>玩家行动队列</strong>
            <button className="ghost-button" onClick={props.onRefreshPlayerActions} type="button">
              <RefreshCw size={15} />
              刷新
            </button>
          </div>
          <div className="list-stack compact-list">
            {props.playerActions.length ? (
              props.playerActions.map((action) => (
                <button
                  className={`record-button ${action.id === props.selectedPlayerActionId ? "selected" : ""}`}
                  disabled={action.status !== "submitted"}
                  key={action.id}
                  onClick={() => props.onSelectPlayerAction(action)}
                  type="button"
                >
                  <span>
                    {action.display_name ?? action.pc_id ?? "未绑定玩家"} ·{" "}
                    {statusLabel(action.status)}
                  </span>
                  <small>{action.action_text}</small>
                </button>
              ))
            ) : (
              <small>暂无玩家提交。</small>
            )}
          </div>
          <label>
            AI 草稿公开描述
            <textarea
              value={props.proposalText}
              onChange={(event) => props.onProposalTextChange(event.target.value)}
            />
          </label>
          <button className="primary-button" onClick={props.onCreateProposal} type="button">
            <MessageSquare size={16} />
            创建手工草稿
          </button>
          <button
            className="primary-button"
            disabled={props.loading}
            onClick={props.onGenerateAiProposal}
            type="button"
          >
            <Send size={16} />
            调用本地 AI
          </button>
        </>
      )}
      {!props.identity && <p className="permission-hint">先开启 KP 会话或使用加入码进入。</p>}
    </section>
  );
}

function modeLabel(mode: ActionAdjudication["mode"]) {
  if (mode === "direct_resolution") return "直接结算";
  if (mode === "skill_check") return "技能检定";
  return "需要 RP / 补充说明";
}

function difficultyLabel(value: string) {
  return { regular: "常规", hard: "困难", extreme: "极难", opposed: "对抗" }[value] ?? value;
}

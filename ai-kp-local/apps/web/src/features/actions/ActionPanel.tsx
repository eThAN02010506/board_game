import { Brain, MessageSquare, RefreshCw, Send } from "lucide-react";
import type { AuthIdentity, PlayerActionRecord } from "../../api/types";
import { statusLabel } from "../../ui/statusLabels";

type Props = {
  identity: AuthIdentity | null;
  loading: boolean;
  playerAction: string;
  proposalText: string;
  playerActions: PlayerActionRecord[];
  selectedPlayerActionId: string;
  autoKpEnabled: boolean;
  onPlayerActionChange: (value: string) => void;
  onProposalTextChange: (value: string) => void;
  onAutoKpEnabledChange: (value: boolean) => void;
  onSelectPlayerAction: (action: PlayerActionRecord) => void;
  onSearchMemory: () => void;
  onSubmitPlayerAction: () => void;
  onRefreshPlayerActions: () => void;
  onCreateProposal: () => void;
  onGenerateAiProposal: () => void;
};

export function ActionPanel(props: Props) {
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
            {props.autoKpEnabled ? "提交并自动推进" : "提交给 KP"}
          </button>
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

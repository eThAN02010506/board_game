import { Brain, Check, RefreshCw } from "lucide-react";
import type { ContextAssembly, TurnProposal } from "../../api/types";
import { ProposalEffectList } from "../../shared/ProposalEffectList";

type Props = {
  proposals: TurnProposal[];
  activeProposal: TurnProposal | null;
  proposalContext: ContextAssembly | null;
  overrideText: string;
  loading: boolean;
  onOverrideTextChange: (value: string) => void;
  onSelectProposal: (proposalId: string) => void;
  onRefresh: () => void;
  onInspectContext: () => void;
  onApprove: () => void;
  onReject: () => void;
};

export function ProposalPanel(props: Props) {
  return (
    <section className="tool-panel proposal-panel">
      <div className="panel-heading">
        <h2>人类 KP 审批</h2>
        <Check size={18} />
      </div>
      <button className="ghost-button" onClick={props.onRefresh} type="button">
        <RefreshCw size={16} />
        刷新草稿
      </button>
      <div className="list-stack">
        {props.proposals.map((proposal) => (
          <button
            className={`record-button ${proposal.id === props.activeProposal?.id ? "selected" : ""}`}
            key={proposal.id}
            onClick={() => props.onSelectProposal(proposal.id)}
            type="button"
          >
            <span>{proposal.status}</span>
            <small>{proposal.player_action}</small>
          </button>
        ))}
      </div>
      <div className="proposal-card">
        {props.activeProposal ? (
          <>
            <span className={`proposal-status ${props.activeProposal.status}`}>
              {props.activeProposal.status}
            </span>
            <p>{props.activeProposal.public_narration}</p>
            <small>{props.activeProposal.kp_notes}</small>
            <div className="effect-list">
              <ProposalEffectList title="待检定" items={props.activeProposal.proposed_checks} />
              <ProposalEffectList title="事件" items={props.activeProposal.proposed_events} />
              <ProposalEffectList title="长期记忆" items={props.activeProposal.proposed_memories} />
              <ProposalEffectList title="NPC 变更" items={props.activeProposal.proposed_npc_updates} />
              <ProposalEffectList title="地图移动" items={props.activeProposal.proposed_map_moves} />
            </div>
          </>
        ) : (
          <p>暂无草稿。</p>
        )}
      </div>
      <label>
        覆写公开描述
        <textarea
          value={props.overrideText}
          onChange={(event) => props.onOverrideTextChange(event.target.value)}
        />
      </label>
      <div className="approval-actions">
        <button className="ghost-button" onClick={props.onInspectContext} type="button">
          <Brain size={16} />
          检查 AI 上下文
        </button>
        <button
          className="primary-button"
          disabled={props.loading || props.activeProposal?.status !== "draft"}
          onClick={props.onApprove}
          type="button"
        >
          <Check size={16} />
          批准落库
        </button>
        <button
          className="secondary-button"
          disabled={props.loading || props.activeProposal?.status !== "draft"}
          onClick={props.onReject}
          type="button"
        >
          拒绝
        </button>
      </div>
      {props.proposalContext &&
        props.proposalContext.proposal_id === props.activeProposal?.id && (
          <div className="context-summary">
            <strong>上下文快照</strong>
            <small>
              约 {props.proposalContext.token_estimate} tokens · 纳入{" "}
              {props.proposalContext.included_sources.length} 项 · 排除{" "}
              {props.proposalContext.excluded_sources.length} 项
            </small>
            <details>
              <summary>最终提示词</summary>
              <pre>{JSON.stringify(props.proposalContext.final_prompt, null, 2)}</pre>
            </details>
            <details open>
              <summary>纳入来源</summary>
              <ul>
                {props.proposalContext.included_sources.map((source) => (
                  <li key={`included-${source.id}`}>
                    <strong>{source.kind}</strong> · {source.label} · {source.visibility}
                    <p>{source.content}</p>
                  </li>
                ))}
              </ul>
            </details>
            <details>
              <summary>排除来源</summary>
              <ul>
                {props.proposalContext.excluded_sources.map((source) => (
                  <li key={`excluded-${source.id}-${source.excluded_reason}`}>
                    {source.kind} · {source.label} · {source.excluded_reason ?? "unknown"}
                  </li>
                ))}
              </ul>
            </details>
          </div>
        )}
    </section>
  );
}

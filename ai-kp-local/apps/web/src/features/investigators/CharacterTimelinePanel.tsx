import { Plus } from "lucide-react";

import type {
  InvestigatorCharacterTimeline,
  InvestigatorPermanentChange
} from "../../api/types";

export type PermanentChangeDraft = {
  kind: InvestigatorPermanentChange["kind"];
  summary: string;
  text: string;
  targetKey: string;
  newValue: string;
  sourceEventId: string;
  rationale: string;
};

export const EMPTY_PERMANENT_CHANGE: PermanentChangeDraft = {
  kind: "scar",
  summary: "",
  text: "",
  targetKey: "",
  newValue: "",
  sourceEventId: "",
  rationale: ""
};

export const PERMANENT_CHANGE_LABELS: Record<InvestigatorPermanentChange["kind"], string> = {
  major_experience: "重要经历",
  scar: "永久伤痕",
  relationship: "重要关系",
  spell: "法术",
  characteristic: "属性变化",
  skill: "技能成长"
};

type OwnerProps = {
  investigatorName: string;
  timeline: InvestigatorCharacterTimeline;
  proposals: InvestigatorPermanentChange[];
  branchId: string;
  branchLabel: string;
  decisionReasons: Record<string, string>;
  busy: boolean;
  onBranchChange: (branchId: string) => void;
  onBranchLabelChange: (label: string) => void;
  onCreateBranch: () => void;
  onDecisionReasonChange: (proposalId: string, reason: string) => void;
  onDecision: (
    proposal: InvestigatorPermanentChange,
    action: "accepted" | "rejected"
  ) => void;
};

export function CharacterTimelinePanel({
  investigatorName,
  timeline,
  proposals,
  branchId,
  branchLabel,
  decisionReasons,
  busy,
  onBranchChange,
  onBranchLabelChange,
  onCreateBranch,
  onDecisionReasonChange,
  onDecision
}: OwnerProps) {
  return <section className="character-timeline-panel">
    <div className="timeline-heading">
      <div>
        <strong>角色连续性</strong>
        <small>团内 HP、SAN、MP 与 Luck 不跨团继承</small>
      </div>
      <span>{timeline.participations.length} 段团经历</span>
    </div>
    <label>本次提交使用的时间线
      <select value={branchId} onChange={(event) => onBranchChange(event.target.value)}>
        {timeline.branches.filter((branch) => branch.status === "active").map((branch) => (
          <option key={branch.id} value={branch.id}>
            {branch.label}{branch.is_primary ? "（主线）" : "（平行线）"}
          </option>
        ))}
      </select>
    </label>
    <div className="timeline-branch-create">
      <input
        aria-label={`${investigatorName}新时间线名称`}
        placeholder="例如：平行世界 B"
        value={branchLabel}
        onChange={(event) => onBranchLabelChange(event.target.value)}
      />
      <button
        className="ghost-button"
        disabled={busy || !branchLabel.trim()}
        onClick={onCreateBranch}
        type="button"
      ><Plus size={14} />新建平行线</button>
    </div>
    <div className="timeline-columns">
      <TimelineList
        empty="还没有参团履历。"
        items={timeline.participations.map((item) => ({
          id: item.id,
          title: `${item.campaign_title} / ${item.session_title}`,
          detail: `${item.status === "active" ? "进行中" : "已结束"} · ${item.world_started_at || item.started_at}`
        }))}
        title="参团履历"
      />
      <TimelineList
        empty="还没有可继承记忆。"
        items={timeline.memories.slice(0, 8).map((item) => ({
          id: item.id,
          title: item.text,
          detail: `${item.campaign_title || "旧团"} · ${item.classification}`
        }))}
        title="主要记忆"
      />
      <TimelineList
        empty="还没有可继承 NPC 记录。"
        items={timeline.npc_encounters.slice(0, 8).map((item) => ({
          id: item.id,
          title: item.npc_name,
          detail: `${item.campaign_title} · ${item.interaction_summary}`
        }))}
        title="故人记录"
      />
    </div>
    {proposals.filter((item) => item.status === "proposed").map((proposal) => (
      <div className="permanent-change-decision" key={proposal.id}>
        <div>
          <strong>{PERMANENT_CHANGE_LABELS[proposal.kind]}：{proposal.summary}</strong>
          <small>来源：{proposal.campaign_title} · 接受后生成新的永久角色卡版本</small>
        </div>
        <label>玩家决定理由
          <input
            value={decisionReasons[proposal.id] || ""}
            onChange={(event) => onDecisionReasonChange(proposal.id, event.target.value)}
          />
        </label>
        <div className="inline-actions">
          <button className="primary-button" disabled={busy} onClick={() => onDecision(proposal, "accepted")} type="button">接受变化</button>
          <button className="secondary-button" disabled={busy} onClick={() => onDecision(proposal, "rejected")} type="button">拒绝变化</button>
        </div>
      </div>
    ))}
  </section>;
}

function TimelineList({
  title,
  empty,
  items
}: {
  title: string;
  empty: string;
  items: Array<{ id: string; title: string; detail: string }>;
}) {
  return <div>
    <strong>{title}</strong>
    {items.length ? <ul>{items.map((item) => <li key={item.id}>
      <span>{item.title}</span><small>{item.detail}</small>
    </li>)}</ul> : <p className="empty-copy">{empty}</p>}
  </div>;
}

type KpProps = {
  timeline?: InvestigatorCharacterTimeline;
  draft: PermanentChangeDraft;
  busy: boolean;
  onLoad: () => void;
  onDraftChange: (changes: Partial<PermanentChangeDraft>) => void;
  onPropose: () => void;
};

export function KpTimelineTools({
  timeline,
  draft,
  busy,
  onLoad,
  onDraftChange,
  onPropose
}: KpProps) {
  const numeric = draft.kind === "characteristic" || draft.kind === "skill";
  const ready = Boolean(draft.summary.trim()
    && draft.sourceEventId.trim()
    && draft.rationale.trim()
    && (numeric ? draft.targetKey.trim() && draft.newValue : draft.text.trim()));
  return <div className="timeline-kp-tools">
    <button className="ghost-button" disabled={busy} onClick={onLoad} type="button">查看玩家可知的跨团履历</button>
    {timeline && <div className="timeline-kp-preview">
      <strong>继承背景</strong>
      <span>{timeline.participations.length} 段参团履历 · {timeline.memories.length} 条可见记忆 · {timeline.npc_encounters.length} 名故人</span>
      <small>旧团秘密、KP 笔记与隐藏记忆不会出现在这里。</small>
    </div>}
    <details className="permanent-change-form">
      <summary>提出永久角色变化</summary>
      <div className="permanent-change-fields">
        <label>变化类型
          <select value={draft.kind} onChange={(event) => onDraftChange({ kind: event.target.value as InvestigatorPermanentChange["kind"] })}>
            {Object.entries(PERMANENT_CHANGE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>简短摘要<input value={draft.summary} onChange={(event) => onDraftChange({ summary: event.target.value })} /></label>
        {numeric ? <>
          <label>{draft.kind === "skill" ? "技能 key" : "属性缩写"}<input placeholder={draft.kind === "skill" ? "coc7.library_use" : "con"} value={draft.targetKey} onChange={(event) => onDraftChange({ targetKey: event.target.value })} /></label>
          <label>永久新值<input min="0" type="number" value={draft.newValue} onChange={(event) => onDraftChange({ newValue: event.target.value })} /></label>
        </> : <label>写入角色卡的内容<textarea rows={2} value={draft.text} onChange={(event) => onDraftChange({ text: event.target.value })} /></label>}
        <label>来源事件 ID<input placeholder="必须来自当前团已落地事件" value={draft.sourceEventId} onChange={(event) => onDraftChange({ sourceEventId: event.target.value })} /></label>
        <label>给玩家的依据<textarea rows={2} value={draft.rationale} onChange={(event) => onDraftChange({ rationale: event.target.value })} /></label>
        <button className="secondary-button" disabled={busy || !ready} onClick={onPropose} type="button">提交给玩家决定</button>
      </div>
    </details>
  </div>;
}

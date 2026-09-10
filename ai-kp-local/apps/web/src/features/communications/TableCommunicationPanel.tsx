import { MessageCircle, RefreshCw, Send } from "lucide-react";
import { useMemo, useState } from "react";

import type {
  AuthIdentity,
  Campaign,
  TableMessageAudience
} from "../../api/types";
import { useTableMessages } from "../../app/hooks/useTableMessages";

type Props = {
  campaign: Campaign | null;
  identity: AuthIdentity;
  refreshKey?: string;
};

const audienceLabels: Record<TableMessageAudience, string> = {
  table: "全桌",
  party: "队伍",
  announcement: "公告",
  direct: "私信"
};

export function TableCommunicationPanel({ campaign, identity, refreshKey = "" }: Props) {
  const ledger = useTableMessages(campaign?.id ?? "", identity, refreshKey);
  const kpMembers = useMemo(
    () => ledger.members.filter((member) => member.role === "kp"),
    [ledger.members]
  );
  const [audience, setAudience] = useState<TableMessageAudience>(
    identity.role === "observer" ? "direct" : "table"
  );
  const [recipientId, setRecipientId] = useState("");
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");

  const directTargets = identity.role === "observer"
    ? kpMembers
    : ledger.members.filter((member) => member.id !== identity.member_id);
  const effectiveRecipient = audience === "direct"
    ? recipientId || directTargets[0]?.id || ""
    : null;

  async function submit() {
    if (!content.trim() || (audience === "direct" && !effectiveRecipient)) return;
    setSending(true);
    setSendError("");
    try {
      await ledger.send(audience, content, effectiveRecipient);
      setContent("");
    } catch (cause) {
      setSendError(cause instanceof Error ? cause.message : "发送失败");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="tool-panel table-communication-panel">
      <div className="panel-heading">
        <div><p className="eyebrow">桌内通信</p><h2>消息与私密边界</h2></div>
        <button className="icon-button" onClick={() => void ledger.refresh()} title="刷新消息" type="button">
          <RefreshCw size={15} />
        </button>
      </div>
      <div className="table-message-list" aria-live="polite">
        {ledger.messages.length ? ledger.messages.map((message) => (
          <article className={`table-message ${message.audience}`} key={message.id}>
            <header>
              <strong>{message.sender.display_name}</strong>
              <span>{audienceLabels[message.audience]}</span>
              {message.recipient && <small>→ {message.recipient.display_name}</small>}
            </header>
            <p>{message.content}</p>
          </article>
        )) : <p className="empty-note"><MessageCircle size={16} /> 暂无当前身份可见的消息。</p>}
      </div>
      <div className="table-message-composer">
        <label>
          发送范围
          <select
            disabled={identity.role === "observer"}
            onChange={(event) => setAudience(event.target.value as TableMessageAudience)}
            value={audience}
          >
            {identity.role !== "observer" && <option value="table">全桌发言</option>}
            {identity.role !== "observer" && <option value="party">队伍频道</option>}
            {identity.role === "kp" && <option value="announcement">KP 公告</option>}
            <option value="direct">私信</option>
          </select>
        </label>
        {audience === "direct" && (
          <label>
            收件人
            <select value={effectiveRecipient ?? ""} onChange={(event) => setRecipientId(event.target.value)}>
              {directTargets.map((member) => (
                <option key={member.id} value={member.id}>{member.display_name} · {member.role}</option>
              ))}
            </select>
          </label>
        )}
        <label>
          {identity.role === "observer" ? "向 KP 私信" : "消息"}
          <textarea
            maxLength={4000}
            onChange={(event) => setContent(event.target.value)}
            placeholder={identity.role === "observer" ? "观战者只能私信 KP，不会打断桌面。" : "OOC、角色台词或桌内协调……"}
            value={content}
          />
        </label>
        {(ledger.error || sendError) && <p className="error-note">{sendError || ledger.error}</p>}
        <button
          className="primary-button"
          disabled={sending || !content.trim() || (audience === "direct" && !effectiveRecipient)}
          onClick={() => void submit()}
          type="button"
        >
          <Send size={15} /> {sending ? "发送中……" : "发送"}
        </button>
      </div>
    </section>
  );
}

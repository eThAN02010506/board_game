import { BookOpenCheck, Pin, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { requestJson } from "../../api/client";
import type { AuthIdentity, Campaign, Handout, HandoutLinkOption } from "../../api/types";

type Props = { campaign: Campaign | null; identity: AuthIdentity | null };

export function HandoutWorkspace({ campaign, identity }: Props) {
  const [items, setItems] = useState<Handout[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [linkOptions, setLinkOptions] = useState<HandoutLinkOption[]>([]);
  const [selectedLink, setSelectedLink] = useState("");
  const [message, setMessage] = useState("选择团会话后读取线索。");

  async function load() {
    if (!campaign || !identity) return;
    try {
      const loaded = await requestJson<Handout[]>(`/campaigns/${campaign.id}/handouts`);
      setItems(loaded);
      if (identity.role === "kp") {
        setLinkOptions(
          await requestJson(`/campaigns/${campaign.id}/handout-link-options`)
        );
      }
      setMessage("手册与线索已同步。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  useEffect(() => { void load(); }, [campaign?.id, identity?.member_id]);

  async function create() {
    if (!campaign) return;
    const [linkType, linkId] = selectedLink
      ? JSON.parse(selectedLink) as [HandoutLinkOption["type"], string]
      : [null, null];
    await requestJson(`/campaigns/${campaign.id}/handouts`, {
      method: "POST",
      body: JSON.stringify({
        title,
        body,
        kind: "clue",
        link_type: linkType || null,
        link_id: linkId || null
      })
    });
    setTitle(""); setBody(""); setSelectedLink(""); await load();
  }

  async function update(item: Handout, changes: Partial<Pick<Handout, "status" | "pinned">>) {
    await requestJson(`/handouts/${item.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_version: item.version,
        status: changes.status ?? item.status,
        pinned: changes.pinned ?? item.pinned,
        link_type: item.link_type,
        link_id: item.link_id
      })
    });
    await load();
  }

  async function markRead(item: Handout) {
    await requestJson(`/handouts/${item.id}/read`, { method: "PUT" });
    await load();
  }

  if (!campaign || !identity) return <section className="page-card"><h2>玩家手册与线索</h2><p>请先进入团会话。</p></section>;
  return <section className="page-card handout-workspace">
    <header className="panel-heading"><div><p className="eyebrow">逐席已读 · 服务端揭示</p><h2>玩家手册与线索</h2></div><button className="ghost-button" onClick={() => void load()}><RefreshCw size={15} />刷新</button></header>
    <p className="form-hint">{message}</p>
    {identity.role === "kp" && <section className="handout-create">
      <label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label>内容<textarea rows={4} value={body} onChange={(event) => setBody(event.target.value)} /></label>
      <label>关联事实 / NPC / 地图 / 地点 / 模组实体
        <select value={selectedLink} onChange={(event) => setSelectedLink(event.target.value)}>
          <option value="">不关联</option>
          {linkOptions.map((option) => (
            <option key={`${option.type}:${option.id}`} value={JSON.stringify([option.type, option.id])}>
              {option.type} · {option.label}
            </option>
          ))}
        </select>
      </label>
      <button className="primary-button" disabled={!title.trim() || !body.trim()} onClick={() => void create()}><BookOpenCheck size={15} />保存草稿</button>
    </section>}
    <div className="handout-grid">{items.map((item) => <article className={`handout-card ${item.status}`} key={item.id}>
      <header><strong>{item.pinned && <Pin size={13} />} {item.title}</strong><small>{item.status === "draft" ? "草稿" : item.status === "revealed" ? "已揭示" : "已撤回"}</small></header>
      <p>{item.body}</p>
      {item.link_type && <small>关联 {item.link_type} · {item.link_id}</small>}
      {identity.role === "kp" ? <div className="handout-actions">
        <button onClick={() => void update(item, { status: item.status === "revealed" ? "withdrawn" : "revealed" })}>{item.status === "revealed" ? "撤回" : "向玩家揭示"}</button>
        <button onClick={() => void update(item, { pinned: !item.pinned })}>{item.pinned ? "取消固定" : "固定"}</button>
        <small>已读 {item.read_receipts?.length ?? 0} 席</small>
      </div> : <button disabled={item.read} onClick={() => void markRead(item)}>{item.read ? "已读" : "标记已读"}</button>}
    </article>)}</div>
  </section>;
}

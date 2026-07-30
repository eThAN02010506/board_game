import {
  BookOpenCheck,
  Brain,
  Eye,
  EyeOff,
  Filter,
  RefreshCw,
  Save,
  Search
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { curateMemory, listMemoryTimeline } from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  MemoryClassification,
  MemoryTimelineItem,
  PlayerCharacter
} from "../../api/types";

type Props = {
  campaign: Campaign | null;
  identity: AuthIdentity | null;
  pcs: PlayerCharacter[];
};

const labels: Record<MemoryClassification, string> = {
  major: "主要事件",
  side: "支线事件",
  npc: "人物关系",
  clue: "线索",
  other: "其他"
};

function displayTime(value: string | null): string {
  if (!value) return "时间未记录";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? value.replace("T", " ")
    : new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "medium",
        timeStyle: "short"
      }).format(parsed);
}

export function MemoryWorkspace({ campaign, identity, pcs }: Props) {
  const [items, setItems] = useState<MemoryTimelineItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [pcId, setPcId] = useState("");
  const [classification, setClassification] = useState<MemoryClassification | "">("");
  const [query, setQuery] = useState("");
  const [includeHidden, setIncludeHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [editClassification, setEditClassification] =
    useState<MemoryClassification>("other");
  const [importance, setImportance] = useState(1);
  const [hidden, setHidden] = useState(false);
  const [reason, setReason] = useState("");

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId]
  );

  async function refresh(resetFilters = false) {
    if (!campaign || !identity) return;
    setBusy(true);
    setMessage("");
    try {
      const next = await listMemoryTimeline(campaign.id, {
        pcId: identity.role === "kp" && !resetFilters ? pcId || undefined : undefined,
        classification: resetFilters ? undefined : classification || undefined,
        query: resetFilters ? "" : query,
        includeHidden: identity.role === "kp" && !resetFilters && includeHidden
      });
      setItems(next);
      setSelectedId((current) =>
        next.some((item) => item.id === current) ? current : next[0]?.id ?? ""
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    setItems([]);
    setSelectedId("");
    setPcId("");
    setQuery("");
    setClassification("");
    setIncludeHidden(false);
    if (campaign && identity) void refresh(true);
    // Refresh is intentionally keyed to the authenticated campaign boundary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign?.id, identity?.member_id]);

  useEffect(() => {
    if (!selected) return;
    setEditClassification(selected.classification);
    setImportance(selected.importance);
    setHidden(selected.hidden);
    setReason("");
  }, [selected]);

  async function submitFilters(event: FormEvent) {
    event.preventDefault();
    await refresh();
  }

  async function saveCuration(event: FormEvent) {
    event.preventDefault();
    if (!campaign || !selected || identity?.role !== "kp" || !reason.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      await curateMemory(campaign.id, selected.id, {
        classification: editClassification,
        importance,
        hidden,
        reason: reason.trim(),
        expected_head_action_id: selected.curation_head_id ?? null
      });
      setMessage("校正动作已追加，原记忆与来源事件保持不变。");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  if (!campaign) {
    return <section className="page-card empty-state">请先在“团与权限”中选择一个团。</section>;
  }
  if (!identity) {
    return <section className="page-card empty-state">请先加入当前团，再读取角色记忆。</section>;
  }

  return (
    <div className="memory-workspace">
      <section className="page-card memory-hero">
        <div>
          <p className="eyebrow">来源可追溯 · 玩家隔离</p>
          <h2>调查员时间线</h2>
          <p>这里展示事件派生的角色记忆；KP 校正只改变投影，不改写历史。</p>
        </div>
        <Brain size={30} aria-hidden="true" />
      </section>

      <form className="page-card memory-filters" onSubmit={submitFilters}>
        <div className="panel-heading">
          <h3><Filter size={17} />筛选</h3>
          <button className="ghost-button" disabled={busy} type="button" onClick={() => void refresh()}>
            <RefreshCw size={15} />刷新
          </button>
        </div>
        <div className="memory-filter-grid">
          {identity.role === "kp" && (
            <label>调查员
              <select value={pcId} onChange={(event) => setPcId(event.target.value)}>
                <option value="">全团</option>
                {pcs.map((pc) => <option key={pc.id} value={pc.id}>{pc.name}</option>)}
              </select>
            </label>
          )}
          <label>类别
            <select
              value={classification}
              onChange={(event) => setClassification(event.target.value as MemoryClassification | "")}
            >
              <option value="">全部类别</option>
              {Object.entries(labels).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label className="memory-query">内容或来源
            <span className="input-with-icon"><Search size={15} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="人物、地点、线索……" />
            </span>
          </label>
          {identity.role === "kp" && (
            <label className="check-label">
              <input checked={includeHidden} type="checkbox" onChange={(event) => setIncludeHidden(event.target.checked)} />
              包含已隐藏
            </label>
          )}
          <button className="primary-button" disabled={busy} type="submit">应用筛选</button>
        </div>
        {message && <p className="memory-message" role="status">{message}</p>}
      </form>

      <div className="memory-layout">
        <section className="page-card memory-timeline-panel">
          <div className="panel-heading">
            <h3>时间线</h3>
            <span>{items.length} 条</span>
          </div>
          {busy && !items.length ? <p className="empty-state">正在读取记忆……</p> : null}
          {!busy && !items.length ? <p className="empty-state">当前筛选下没有可见记忆。</p> : null}
          <ol className="memory-timeline">
            {items.map((item) => (
              <li key={item.id}>
                <article
                  className={`memory-card ${item.id === selectedId ? "selected" : ""} ${item.hidden ? "is-hidden" : ""}`}
                >
                  <button type="button" onClick={() => setSelectedId(item.id)}>
                    <span className="memory-card-topline">
                      <span className={`memory-kind memory-kind-${item.classification}`}>{labels[item.classification]}</span>
                      <time>{displayTime(item.effective_time)}</time>
                    </span>
                    <strong>{item.text}</strong>
                    <span className="memory-card-meta">
                      {item.investigator_name ?? item.pc_name ?? "全团记忆"}
                      {item.npc_name ? ` · ${item.npc_name}` : ""}
                      {" · "}重要性 {item.importance}/5
                      {item.hidden ? " · 已隐藏" : ""}
                    </span>
                  </button>
                </article>
              </li>
            ))}
          </ol>
        </section>

        <aside className="page-card memory-evidence">
          {!selected ? <p className="empty-state">选择一条记忆查看证据链。</p> : (
            <>
              <div className="panel-heading">
                <h3><BookOpenCheck size={17} />记忆与来源</h3>
                {selected.hidden ? <EyeOff size={17} /> : <Eye size={17} />}
              </div>
              <p className="memory-detail-text">{selected.text}</p>
              <dl className="memory-provenance">
                <div><dt>调查员</dt><dd>{selected.investigator_name ?? selected.pc_name ?? "全团"}</dd></div>
                <div><dt>游戏时间</dt><dd>{displayTime(selected.happened_at)}</dd></div>
                <div><dt>原始范围</dt><dd>{selected.scope}</dd></div>
                <div><dt>可见性</dt><dd>{selected.visibility}</dd></div>
              </dl>
              <section className="memory-source">
                <p className="eyebrow">来源事件</p>
                {selected.source_event_summary ? (
                  <>
                    <strong>{selected.source_event_summary}</strong>
                    <small>{selected.source_event_type} · {displayTime(selected.source_event_happened_at)}</small>
                  </>
                ) : <p>没有玩家可见的来源摘要。</p>}
              </section>
              {identity.role === "kp" && (
                <form className="memory-curation" onSubmit={saveCuration}>
                  <p className="eyebrow">追加 KP 校正</p>
                  <div className="memory-curation-row">
                    <label>类别
                      <select value={editClassification} onChange={(event) => setEditClassification(event.target.value as MemoryClassification)}>
                        {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </label>
                    <label>重要性
                      <input min={1} max={5} type="number" value={importance} onChange={(event) => setImportance(Number(event.target.value))} />
                    </label>
                  </div>
                  <label className="check-label">
                    <input checked={hidden} type="checkbox" onChange={(event) => setHidden(event.target.checked)} />
                    从玩家时间线隐藏
                  </label>
                  <label>校正理由
                    <textarea required maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明为什么更改分类、重要性或可见状态" />
                  </label>
                  <button className="primary-button" disabled={busy || !reason.trim()} type="submit">
                    <Save size={15} />追加校正
                  </button>
                  {selected.curation_reason && <small>最近一次：{selected.curation_reason}</small>}
                </form>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

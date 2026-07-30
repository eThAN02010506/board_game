import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  History,
  Plus,
  RefreshCw,
  RotateCcw
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  createWorldFact,
  listWorldFacts,
  retconWorldFact
} from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  PlayerCharacter,
  WorldFactCreateInput,
  WorldFactEntry,
  WorldFactType
} from "../../api/types";

type Props = {
  campaign: Campaign | null;
  identity: AuthIdentity | null;
  pcs: PlayerCharacter[];
};

const factLabels: Record<WorldFactType, string> = {
  canonical_fact: "公开事实",
  kp_secret: "KP 秘密",
  character_belief: "角色认知",
  rumor: "传闻",
  ai_hypothesis: "AI 假设",
  retconned: "已撤回事实"
};

const createTypes: WorldFactCreateInput["fact_type"][] = [
  "canonical_fact",
  "kp_secret",
  "character_belief",
  "rumor",
  "ai_hypothesis"
];

function visibleTime(value: string | null): string {
  return value?.replace("T", " ") ?? "未记录世界时间";
}

export function FactWorkspace({ campaign, identity, pcs }: Props) {
  const [facts, setFacts] = useState<WorldFactEntry[]>([]);
  const [selectedEventId, setSelectedEventId] = useState("");
  const [factType, setFactType] = useState<WorldFactType | "">("");
  const [includeHistory, setIncludeHistory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const [createType, setCreateType] =
    useState<WorldFactCreateInput["fact_type"]>("canonical_fact");
  const [subject, setSubject] = useState("");
  const [predicate, setPredicate] = useState("");
  const [objectText, setObjectText] = useState("");
  const [pcId, setPcId] = useState("");
  const [happenedAt, setHappenedAt] = useState("");
  const [retconReason, setRetconReason] = useState("");

  const selected = useMemo(
    () => facts.find((item) => item.event_id === selectedEventId) ?? facts[0] ?? null,
    [facts, selectedEventId]
  );

  async function refresh() {
    if (!campaign || !identity) return;
    setBusy(true);
    setMessage("");
    try {
      const next = await listWorldFacts(campaign.id, {
        includeHistory: identity.role === "kp" && includeHistory,
        factType
      });
      setFacts(next);
      setSelectedEventId((current) =>
        next.some((item) => item.event_id === current)
          ? current
          : next[0]?.event_id ?? ""
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    setFacts([]);
    setSelectedEventId("");
    setFactType("");
    setIncludeHistory(false);
    setMessage("");
    if (campaign && identity) void refresh();
    // Refresh is intentionally scoped to the authenticated campaign.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign?.id, identity?.member_id]);

  async function submitFact(event: FormEvent) {
    event.preventDefault();
    if (!campaign || identity?.role !== "kp") return;
    setBusy(true);
    setMessage("");
    try {
      const created = await createWorldFact(campaign.id, {
        fact_type: createType,
        subject,
        predicate,
        object_text: objectText,
        pc_id: createType === "character_belief" ? pcId || null : null,
        happened_at: happenedAt || null,
        source_reference: { kind: "fact_workspace" }
      });
      setSubject("");
      setPredicate("");
      setObjectText("");
      setRetconReason("");
      setSelectedEventId(created.event_id);
      setMessage("已追加事实；原事件和来源保持不变。");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function submitRetcon(event: FormEvent) {
    event.preventDefault();
    if (!campaign || identity?.role !== "kp" || !selected?.active) return;
    setBusy(true);
    setMessage("");
    try {
      await retconWorldFact(campaign.id, selected.fact_key, {
        expected_head_event_id: selected.event_id,
        reason: retconReason,
        source_reference: { kind: "fact_workspace" },
        happened_at: happenedAt || null
      });
      setRetconReason("");
      setMessage("已追加撤回 revision；旧事实未被覆盖。");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  if (!campaign || !identity) {
    return (
      <section className="page-card empty-state">
        <History size={24} />
        <h2>世界事实</h2>
        <p>请先选择团并加入当前会话。事实权限来自服务端身份，而不是页面筛选器。</p>
      </section>
    );
  }

  return (
    <div className={`fact-workspace ${identity.role}`}>
      <section className="page-card fact-list-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Append-only ledger</span>
            <h2>世界事实</h2>
          </div>
          <button
            aria-label="刷新世界事实"
            className="icon-button"
            disabled={busy}
            onClick={() => void refresh()}
            type="button"
          >
            <RefreshCw size={17} />
          </button>
        </div>

        <div className="fact-filters">
          <label>
            类型
            <select
              value={factType}
              onChange={(event) => setFactType(event.target.value as WorldFactType | "")}
            >
              <option value="">全部当前事实</option>
              {Object.entries(factLabels).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          {identity.role === "kp" && (
            <label className="inline-check">
              <input
                checked={includeHistory}
                onChange={(event) => setIncludeHistory(event.target.checked)}
                type="checkbox"
              />
              包含历史 revision
            </label>
          )}
          <button className="ghost-button" disabled={busy} onClick={() => void refresh()} type="button">
            应用筛选
          </button>
        </div>

        <div className="fact-records">
          {facts.map((item) => (
            <button
              className={`fact-record ${item.event_id === selected?.event_id ? "selected" : ""}`}
              key={`${item.event_id}:${item.revision}`}
              onClick={() => setSelectedEventId(item.event_id)}
              type="button"
            >
              <span className={`fact-kind fact-kind-${item.fact.category}`}>
                {item.fact.visibility === "kp" ? <EyeOff size={13} /> : <Eye size={13} />}
                {factLabels[item.fact.category]}
              </span>
              <strong>{item.fact.subject} · {item.fact.predicate}</strong>
              <small>revision {item.revision} · {visibleTime(item.happened_at)}</small>
            </button>
          ))}
          {!facts.length && <p className="empty-copy">当前视角没有可见事实。</p>}
        </div>
      </section>

      <section className="page-card fact-detail-panel">
        {selected ? (
          <>
            <div className="panel-heading">
              <div>
                <span className={`fact-kind fact-kind-${selected.fact.category}`}>
                  {factLabels[selected.fact.category]}
                </span>
                <h2>{selected.fact.subject}</h2>
              </div>
              <span className="revision-pill">v{selected.revision}</span>
            </div>
            <dl className="fact-definition">
              <div><dt>关系/属性</dt><dd>{selected.fact.predicate}</dd></div>
              <div><dt>内容</dt><dd>{selected.fact.object_text}</dd></div>
              <div><dt>世界时间</dt><dd>{visibleTime(selected.happened_at)}</dd></div>
              <div><dt>记录时间</dt><dd>{visibleTime(selected.created_at)}</dd></div>
              {identity.role === "kp" && (
                <>
                  <div><dt>事实键</dt><dd><code>{selected.fact_key}</code></dd></div>
                  <div><dt>事件</dt><dd><code>{selected.event_id}</code></dd></div>
                  <div>
                    <dt>来源</dt>
                    <dd><code>{JSON.stringify(selected.source_reference ?? {})}</code></dd>
                  </div>
                </>
              )}
            </dl>

            {identity.role === "kp" && selected.active && (
              <form className="fact-retcon-form" onSubmit={submitRetcon}>
                <div className="warning-note">
                  <AlertTriangle size={16} />
                  更正会追加一个撤回 revision，不会修改或删除旧事件。
                </div>
                <label>
                  撤回原因
                  <textarea
                    maxLength={4000}
                    onChange={(event) => setRetconReason(event.target.value)}
                    required
                    value={retconReason}
                  />
                </label>
                <button className="ghost-button danger" disabled={busy || !retconReason.trim()} type="submit">
                  <RotateCcw size={16} />
                  追加撤回
                </button>
              </form>
            )}
          </>
        ) : (
          <div className="empty-state">
            <CheckCircle2 size={24} />
            <h2>没有选中的事实</h2>
            <p>玩家只会收到服务端过滤后的公开事实和本人角色认知。</p>
          </div>
        )}
      </section>

      {identity.role === "kp" && (
        <section className="page-card fact-create-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Human-reviewed assertion</span>
              <h2>追加事实</h2>
            </div>
            <Plus size={18} />
          </div>
          <form onSubmit={submitFact}>
            <label>
              类型
              <select
                value={createType}
                onChange={(event) => {
                  const next = event.target.value as WorldFactCreateInput["fact_type"];
                  setCreateType(next);
                  if (next !== "character_belief") setPcId("");
                }}
              >
                {createTypes.map((value) => (
                  <option key={value} value={value}>{factLabels[value]}</option>
                ))}
              </select>
            </label>
            {createType === "character_belief" && (
              <label>
                目标角色
                <select required value={pcId} onChange={(event) => setPcId(event.target.value)}>
                  <option value="">选择调查员</option>
                  {pcs.map((pc) => <option key={pc.id} value={pc.id}>{pc.name}</option>)}
                </select>
              </label>
            )}
            <label>
              主体
              <input maxLength={200} required value={subject} onChange={(event) => setSubject(event.target.value)} />
            </label>
            <label>
              关系或属性
              <input maxLength={120} required value={predicate} onChange={(event) => setPredicate(event.target.value)} />
            </label>
            <label>
              内容
              <textarea maxLength={4000} required value={objectText} onChange={(event) => setObjectText(event.target.value)} />
            </label>
            <label>
              世界时间（可选）
              <input maxLength={120} value={happenedAt} onChange={(event) => setHappenedAt(event.target.value)} />
            </label>
            <button className="primary-button" disabled={busy} type="submit">
              <Plus size={16} />
              追加到事实账本
            </button>
          </form>
        </section>
      )}

      {message && <p className="workspace-message" role="status">{message}</p>}
    </div>
  );
}

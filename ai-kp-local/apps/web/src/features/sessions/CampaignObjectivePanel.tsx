import { RefreshCw, Target } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  createCampaignObjective,
  listCampaignObjectives,
  updateCampaignObjective
} from "../../api/client";
import type { AuthIdentity, CampaignObjective } from "../../api/types";
import { useIdentityRequestScope } from "../../app/hooks/useIdentityRequestScope";

const ACTIVE_STATUSES = new Set(["open", "blocked"]);

function commandId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function CampaignObjectivePanel(props: {
  campaignId: string;
  identity: AuthIdentity;
  refreshKey?: string;
}) {
  const { enabled, generation, key: scopeKey, trackerRef } =
    useIdentityRequestScope(props.campaignId, props.identity);
  const [objectives, setObjectives] = useState<CampaignObjective[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [progress, setProgress] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const requestVersion = useRef(0);

  const refresh = useCallback(async (silent = false) => {
    const version = ++requestVersion.current;
    const requested = { generation, scopeKey };
    if (!enabled) {
      setObjectives([]);
      return;
    }
    if (!silent) setError("");
    try {
      const next = await listCampaignObjectives(props.campaignId);
      const current = trackerRef.current;
      if (
        version === requestVersion.current
        && current.key === requested.scopeKey
        && current.generation === requested.generation
      ) setObjectives(next);
    } catch (cause) {
      const current = trackerRef.current;
      if (current.key === requested.scopeKey && current.generation === requested.generation) {
        setError(cause instanceof Error ? cause.message : "任务账本暂时不可用");
      }
    }
  }, [enabled, generation, props.campaignId, scopeKey, trackerRef]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => void refresh(true), 5000);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);
  useEffect(() => { if (props.refreshKey) void refresh(true); }, [props.refreshKey, refresh]);

  async function create() {
    setBusy("create");
    setError("");
    try {
      await createCampaignObjective(props.campaignId, {
        command_id: commandId("objective-create"),
        title,
        public_description: description,
        kp_notes: "",
        visibility: "table",
        source_refs: []
      });
      setTitle("");
      setDescription("");
      await refresh(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务创建失败");
    } finally {
      setBusy("");
    }
  }

  async function update(objective: CampaignObjective, status: CampaignObjective["status"]) {
    setBusy(objective.id);
    setError("");
    try {
      await updateCampaignObjective(objective.id, {
        command_id: commandId("objective-update"),
        expected_version: objective.version,
        status,
        public_progress: progress[objective.id] ?? "",
        kp_notes: "",
        source_refs: []
      });
      setProgress((current) => ({ ...current, [objective.id]: "" }));
      await refresh(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务更新失败");
    } finally {
      setBusy("");
    }
  }

  const active = objectives.filter((item) => ACTIVE_STATUSES.has(item.status));
  const resolved = objectives.filter((item) => !ACTIVE_STATUSES.has(item.status));
  return (
    <section aria-label="Campaign 任务" className="tool-panel campaign-objective-panel">
      <div className="panel-heading">
        <div><p className="eyebrow">确定性进度</p><h2>Campaign 任务</h2></div>
        <button aria-label="刷新任务" className="icon-button" onClick={() => void refresh()} type="button"><RefreshCw size={15} /></button>
      </div>
      <p className="panel-hint">任务状态来自权威账本；AI 可以提案，但不能靠叙述完成任务。</p>
      {props.identity.role === "kp" && (
        <div className="gameplay-command-stack">
          <label>任务标题<input aria-label="任务标题" onChange={(event) => setTitle(event.target.value)} value={title} /></label>
          <label>公开目标<textarea aria-label="公开目标" onChange={(event) => setDescription(event.target.value)} value={description} /></label>
          <button className="primary-button" disabled={busy === "create" || !title.trim()} onClick={() => void create()} type="button">建立任务</button>
        </div>
      )}
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="objective-list">
        {active.map((objective) => (
          <article className="objective-card" key={objective.id}>
            <div><Target size={16} /><strong>{objective.title}</strong><span>{objective.status === "blocked" ? "受阻" : "进行中"}</span></div>
            {objective.public_description && <p>{objective.public_description}</p>}
            {(objective.events ?? objective.progress ?? []).filter((event) => event.public_progress).slice(-3).map((event) => (
              <small key={event.id}>{event.public_progress}</small>
            ))}
            {props.identity.role === "kp" && (
              <div className="gameplay-command-stack">
                <input aria-label={`${objective.title}进展`} onChange={(event) => setProgress((current) => ({ ...current, [objective.id]: event.target.value }))} placeholder="本次进展或受阻原因" value={progress[objective.id] ?? ""} />
                <div className="compact-actions">
                  <button disabled={busy === objective.id} onClick={() => void update(objective, "open")} type="button">继续</button>
                  <button disabled={busy === objective.id} onClick={() => void update(objective, "blocked")} type="button">标记受阻</button>
                  <button disabled={busy === objective.id} onClick={() => void update(objective, "completed")} type="button">完成</button>
                  <button disabled={busy === objective.id} onClick={() => void update(objective, "failed")} type="button">失败</button>
                </div>
              </div>
            )}
          </article>
        ))}
        {!active.length && <p className="empty-note">当前没有进行中的公开任务。</p>}
      </div>
      {resolved.length > 0 && <details><summary>已结束任务（{resolved.length}）</summary><ul>{resolved.map((item) => <li key={item.id}>{item.title} · {item.status}</li>)}</ul></details>}
    </section>
  );
}

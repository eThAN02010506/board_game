import { Activity, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { requestJson } from "../../api/client";
import type { AuthIdentity } from "../../api/types";

type SignalProjection = {
  signal_id: string;
  title: string;
  display_mode: "exact" | "stage" | "narrative";
  severity: number;
  label: string;
  description: string;
  current_value: unknown;
  source_path: string | null;
  band_id: string | null;
  active: boolean;
};

type SignalEvent = {
  batch_id: string;
  result_version: number;
  signal_id: string;
  from: SignalProjection | null;
  to: SignalProjection | null;
  created_at: string;
};

type SignalView = {
  run_id: string | null;
  state_version: number | null;
  signals: SignalProjection[];
  events: SignalEvent[];
};

type Props = {
  campaignId: string;
  refreshKey: string;
  role: AuthIdentity["role"];
};

function displaySignalValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value !== null && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function ConsequenceSignalPanel({ campaignId, refreshKey, role }: Props) {
  const [view, setView] = useState<SignalView | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const requestEpoch = useRef(0);

  const load = useCallback(async () => {
    const epoch = ++requestEpoch.current;
    if (!campaignId) {
      setView(null);
      setMessage("");
      return;
    }
    setLoading(true);
    setView(null);
    setMessage("");
    try {
      const loaded = await requestJson<SignalView>(
        `/campaigns/${campaignId}/consequence-signals`
      );
      if (epoch !== requestEpoch.current) return;
      setView(loaded);
    } catch (error) {
      if (epoch !== requestEpoch.current) return;
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (epoch === requestEpoch.current) setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => {
    void load();
    return () => {
      requestEpoch.current += 1;
    };
  }, [load, refreshKey]);

  if (!view?.run_id && !message) return null;
  if (role === "player" && !view?.signals.length && !message) return null;

  return (
    <section className="consequence-signal-panel" aria-label="世界动态">
      <div className="consequence-signal-heading">
        <div>
          <Activity size={17} />
          <strong>世界动态</strong>
          {view?.state_version !== null && role === "kp" && (
            <small>状态 v{view?.state_version}</small>
          )}
        </div>
        <button
          aria-label="刷新世界动态"
          className="icon-button"
          disabled={loading}
          onClick={() => void load()}
          type="button"
        >
          <RefreshCw size={14} />
        </button>
      </div>
      {message && <p className="inline-message">{message}</p>}
      {view?.signals.length ? (
        <div className="consequence-signal-list">
          {view.signals.map((signal) => (
            <article
              className={`consequence-signal severity-${signal.severity}`}
              key={signal.signal_id}
            >
              <div>
                <strong>{signal.title}</strong>
                <span>{signal.active ? signal.label : "未触发"}</span>
              </div>
              {signal.display_mode === "exact" && signal.current_value !== null && (
                <b>{displaySignalValue(signal.current_value)}</b>
              )}
              {signal.description && <p>{signal.description}</p>}
              {role === "kp" && signal.source_path && (
                <small>{signal.source_path} · {signal.band_id ?? "inactive"}</small>
              )}
            </article>
          ))}
        </div>
      ) : (
        role === "kp" && <small>当前契约没有已配置的世界后果信号。</small>
      )}
      {!!view?.events.length && (
        <details className="consequence-signal-history">
          <summary>最近变化（{view.events.length}）</summary>
          <ul>
            {view.events.slice(-5).reverse().map((event) => (
              <li key={`${event.batch_id}:${event.signal_id}`}>
                v{event.result_version} · {event.to?.title ?? event.from?.title} · {event.to?.label ?? "不再公开"}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

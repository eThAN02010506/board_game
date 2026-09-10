import { Pause, Play, RefreshCw, Square } from "lucide-react";

import type { AuthIdentity, SessionContinuityView } from "../../api/types";

type Props = {
  identity: AuthIdentity;
  view: SessionContinuityView | null;
  error: string;
  busy: boolean;
  onRefresh: () => void;
  onEnd: () => void;
  onContinue: () => void;
  onTransition: (target: "paused" | "in_progress", expectedVersion: number) => void;
};

const statusLabels = {
  prepared: "准备中",
  in_progress: "进行中",
  paused: "已暂停",
  ended: "已结束"
};

export function SessionContinuityPanel(props: Props) {
  const episode = props.view?.current_episode;
  const projection = props.view?.latest_snapshot?.projection;
  return (
    <section className="tool-panel session-continuity-panel">
      <div className="panel-heading">
        <div><p className="eyebrow">Campaign 连续性</p><h2>Session 与继续游戏</h2></div>
        <button className="icon-button" onClick={props.onRefresh} title="刷新连续性" type="button"><RefreshCw size={15} /></button>
      </div>
      {episode && (
        <div className="session-episode-status">
          <strong>第 {episode.sequence_no} 次 Session</strong>
          <span>{statusLabels[episode.status]}</span>
        </div>
      )}
      {projection && (
        <article className="previously-on">
          <h3>Previously on…</h3>
          <p>{projection.summary_text}</p>
          {projection.module?.current_scene_title && <small>当前位置：{projection.module.current_scene_title}</small>}
          {projection.party && <small>Party 状态已恢复：{projection.party.length} 名角色</small>}
          {projection.current_objectives && projection.current_objectives.length > 0 && (
            <div className="continuity-summary-list"><strong>当前任务</strong><ul>{projection.current_objectives.map((item) => <li key={item.id}>{item.title} · {item.status === "blocked" ? "受阻" : "进行中"}</li>)}</ul></div>
          )}
          {projection.unresolved_questions && projection.unresolved_questions.length > 0 && <small>未解决问题：{projection.unresolved_questions.map((item) => item.title).join("、")}</small>}
          {projection.known_npcs && projection.known_npcs.length > 0 && <small>重要 NPC：{projection.known_npcs.map((item) => item.name).join("、")}</small>}
          {projection.visible_inventory && projection.visible_inventory.length > 0 && <small>关键物品：{projection.visible_inventory.map((item) => item.public_name).join("、")}</small>}
          {props.identity.role === "kp" && projection.private_events && (
            <details className="continuity-kp-notes">
              <summary>KP 私密回顾（{projection.private_event_count ?? projection.private_events.length}）</summary>
              <ul>
                {projection.private_events.map((event) => (
                  <li key={String(event.id)}>{String(event.summary ?? event.event_type ?? "未命名事件")}</li>
                ))}
              </ul>
            </details>
          )}
        </article>
      )}
      {props.identity.role === "kp" && episode && (
        <div className="inline-actions">
          {episode.status === "in_progress" && <button className="ghost-button" disabled={props.busy} onClick={() => props.onTransition("paused", episode.version)} type="button"><Pause size={14} />暂停 Session</button>}
          {episode.status === "paused" && <button className="ghost-button" disabled={props.busy} onClick={() => props.onTransition("in_progress", episode.version)} type="button"><Play size={14} />恢复 Session</button>}
          {episode.status !== "ended" && <button className="secondary-button" disabled={props.busy} onClick={props.onEnd} type="button"><Square size={14} />Session End</button>}
          {episode.status === "ended" && <button className="primary-button" disabled={props.busy} onClick={props.onContinue} type="button"><Play size={14} />Continue Campaign</button>}
        </div>
      )}
      {episode?.status === "ended" && props.identity.role !== "kp" && (
        <p className="permission-hint">本次 Session 已结束，等待 KP 开始下一次游戏。</p>
      )}
      {props.error && <p className="error-note">{props.error}</p>}
    </section>
  );
}

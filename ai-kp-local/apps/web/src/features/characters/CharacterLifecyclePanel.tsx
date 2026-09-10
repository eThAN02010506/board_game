import { RefreshCw, UserRoundCog } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  decideCharacterLifecycle,
  getCharacterLifecycle,
  proposeCharacterLifecycle
} from "../../api/client";
import type { AuthIdentity, CharacterLifecycleView } from "../../api/types";
import { useIdentityRequestScope } from "../../app/hooks/useIdentityRequestScope";

const POLL_INTERVAL_MS = 5000;

const stateLabels: Record<string, string> = {
  active: "行动中",
  incapacitated: "失去行动能力",
  dead: "死亡",
  retired: "退役",
  departed: "离队",
  npc_controlled: "主持方代管",
  observing: "观战",
  temporarily_absent: "暂时离席"
};

export function playerLifecycleBlockReason(
  view: CharacterLifecycleView,
  memberId: string
): string {
  const presence = view.presence.find((item) => item.member_id === memberId);
  if (!presence) return "正在确认当前角色是否可以行动。";
  if (presence.state !== "active") {
    return {
      observing: "你当前正在观战；请先确认回归或换入已审核角色。",
      temporarily_absent: "你当前暂时离席，回归后才能提交行动。",
      departed: "你已离开当前冒险，请先确认回归或换角。",
      npc_controlled: "当前角色由主持方代管，交还控制权后才能行动。"
    }[presence.state] ?? "当前席位不能提交行动。";
  }
  const character = view.characters.find(
    (item) => item.investigator_id === presence.investigator_id
  );
  if (!character || character.state === "active") return "";
  return {
    incapacitated: "当前调查员已失去行动能力，恢复前不能提交行动。",
    dead: "当前调查员已经死亡；请先观战或确认换入后备角色。",
    retired: "当前调查员已经退役；请先确认换入后备角色。",
    departed: "当前调查员已离开当前冒险；请先确认回归或换角。",
    npc_controlled: "当前调查员由主持方代管，交还控制权后才能行动。"
  }[character.state] ?? "当前调查员不能提交行动。";
}

export function CharacterLifecyclePanel(props: {
  campaignId: string;
  identity: AuthIdentity;
  refreshKey?: string;
  onIdentityChanged?: () => void | Promise<void>;
  onPlayerActionBlockChanged?: (reason: string) => void;
}) {
  const { enabled, generation, key: scopeKey, trackerRef } =
    useIdentityRequestScope(props.campaignId, props.identity);
  const [view, setView] = useState<CharacterLifecycleView | null>(null);
  const [memberId, setMemberId] = useState("");
  const [action, setAction] = useState<"observe" | "replace" | "retire" | "temporary_leave" | "npc_control" | "return">("observe");
  const [replacementId, setReplacementId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);

  const refresh = useCallback(async (silent = false) => {
    const version = ++requestVersion.current;
    const requested = { generation, scopeKey };
    if (!enabled) {
      setView(null);
      return;
    }
    if (!silent) setError("");
    try {
      const next = await getCharacterLifecycle(props.campaignId);
      const current = trackerRef.current;
      if (version === requestVersion.current && current.key === requested.scopeKey && current.generation === requested.generation) {
        setView(next);
        setMemberId((value) => value || next.presence[0]?.member_id || "");
        if (props.identity.role === "player") {
          props.onPlayerActionBlockChanged?.(
            playerLifecycleBlockReason(next, props.identity.member_id)
          );
        }
      }
    } catch (cause) {
      const current = trackerRef.current;
      if (current.key === requested.scopeKey && current.generation === requested.generation) {
        setError(cause instanceof Error ? cause.message : "角色生命周期暂时不可用");
      }
    }
  }, [enabled, generation, props.campaignId, props.identity.member_id, props.identity.role, props.onPlayerActionBlockChanged, scopeKey, trackerRef]);

  const act = useCallback(async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
      await props.onIdentityChanged?.();
      await refresh(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "生命周期操作失败");
    } finally {
      setBusy(false);
    }
  }, [props.onIdentityChanged, refresh]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => void refresh(true), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);
  useEffect(() => { if (props.refreshKey) void refresh(true); }, [props.refreshKey, refresh]);

  const pending = view?.requests.filter((request) => request.status === "awaiting_player") ?? [];
  return (
    <section className="tool-panel lifecycle-panel" aria-label="角色生命周期">
      <div className="panel-heading">
        <div><p className="eyebrow">可恢复角色状态</p><h2>角色生命周期</h2></div>
        <button aria-label="刷新角色生命周期" className="icon-button" onClick={() => void refresh()} type="button"><RefreshCw size={17} /></button>
      </div>
      <p className="panel-hint">死亡不会结束 Campaign；换角、退役和回归必须留下事件，并由受影响玩家确认。</p>
      {error && <p className="error-text" role="alert">{error}</p>}
      {!view ? <p className="empty-note">正在读取角色状态…</p> : (
        <>
          <div className="lifecycle-character-grid">
            {view.characters.map((character) => (
              <span className={`lifecycle-state lifecycle-${character.state}`} key={character.investigator_id}>
                <strong>{character.investigator_name || "未命名调查员"}</strong>
                <small>{stateLabels[character.state] ?? character.state}</small>
              </span>
            ))}
          </div>
          {!view.capabilities.resurrection && <p className="empty-note">{view.capabilities.resurrection_reason}</p>}

          {props.identity.role === "kp" && (
            <div className="lifecycle-proposal-form">
              <h3>提出变更</h3>
              <select aria-label="选择桌员" onChange={(event) => setMemberId(event.target.value)} value={memberId}>
                <option value="">选择桌员</option>
                {view.presence.map((presence) => <option key={presence.member_id} value={presence.member_id}>{presence.display_name} · {stateLabels[presence.state] ?? presence.state}</option>)}
              </select>
              <select aria-label="选择生命周期动作" onChange={(event) => setAction(event.target.value as typeof action)} value={action}>
                <option value="observe">转为观战</option>
                <option value="replace">换入备用角色</option>
                <option value="retire">角色退役</option>
                <option value="temporary_leave">暂时离席</option>
                <option value="npc_control">主持方临时代管</option>
                <option value="return">玩家回归</option>
              </select>
              {(action === "replace" || action === "return") && (
                <select aria-label="选择进入游戏的角色" onChange={(event) => setReplacementId(event.target.value)} value={replacementId}>
                  <option value="">选择已审核角色</option>
                  {view.characters.map((character) => <option key={character.investigator_id} value={character.investigator_id}>{character.investigator_name || character.investigator_id}</option>)}
                </select>
              )}
              <textarea aria-label="生命周期变更原因" onChange={(event) => setReason(event.target.value)} placeholder="说明离席、退役、换角或代管原因" value={reason} />
              <button className="primary-button" disabled={busy || !memberId || !reason.trim() || ((action === "replace" || action === "return") && !replacementId)} onClick={() => void act(() => proposeCharacterLifecycle(props.campaignId, { member_id: memberId, action, reason: reason.trim(), ...(replacementId ? { replacement_investigator_id: replacementId } : {}) }))} type="button">提交给玩家确认</button>
            </div>
          )}

          {pending.length > 0 && (
            <div className="lifecycle-requests">
              <h3>待确认变更</h3>
              {pending.map((request) => (
                <article key={request.id}>
                  <UserRoundCog size={18} />
                  <div><strong>{request.action}</strong><p>{request.reason}</p></div>
                  {(request.member_id === props.identity.member_id || (props.identity.role === "kp" && ["temporary_leave", "npc_control"].includes(request.action))) && (
                    <div className="compact-actions">
                      <button disabled={busy} onClick={() => void act(() => decideCharacterLifecycle(request.id, { action: "accept", reason: "确认执行此生命周期变更", expected_version: request.version }))} type="button">确认</button>
                      {request.member_id === props.identity.member_id && <button disabled={busy} onClick={() => void act(() => decideCharacterLifecycle(request.id, { action: "reject", reason: "玩家拒绝此次变更", expected_version: request.version }))} type="button">拒绝</button>}
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
          {view.events.length > 0 && <details><summary>公开生命周期记录（{view.events.length}）</summary>{view.events.slice(-8).map((event) => <p key={event.id}>{event.public_summary}</p>)}</details>}
        </>
      )}
    </section>
  );
}

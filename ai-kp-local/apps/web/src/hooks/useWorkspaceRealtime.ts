import { useEffect, useRef, useState } from "react";

import { apiBase } from "../api/client";
import type { AuthIdentity, Campaign, SessionInfo } from "../api/types";
import { connectRealtime, type RealtimeEvent, type RealtimeStatus } from "../realtime";
import { readCampaignToken } from "../session/session-storage";

type RefreshKind = "identity" | "members" | "pcs" | "actions" | "proposals" | "maps";

type RealtimeCallbacks = {
  refreshIdentity: () => void;
  refreshMembers: () => void;
  refreshPcs: () => void;
  refreshActions: () => void;
  refreshProposals: () => void;
  refreshMaps: () => void;
  expireSession: (reason: string) => void;
};

type WorkspaceRealtimeOptions = {
  campaign: Campaign | null;
  session: SessionInfo | null;
  identity: AuthIdentity | null;
  callbacks: RealtimeCallbacks;
};

type RealtimeScope = {
  campaignId: string;
  sessionId: string;
  memberId: string;
  role: AuthIdentity["role"] | "";
};

function sameScope(left: RealtimeScope | null, right: RealtimeScope) {
  return (
    left?.campaignId === right.campaignId &&
    left.sessionId === right.sessionId &&
    left.memberId === right.memberId
  );
}

function refreshKindsForEvent(event: RealtimeEvent): RefreshKind[] {
  if (event.event_type === "session.identity_updated") return ["identity", "pcs", "maps"];
  if (event.event_type.startsWith("session.member_")) return ["members"];
  if (event.event_type.startsWith("pc.")) return ["pcs"];
  if (event.event_type.startsWith("player_action.")) return ["actions"];
  if (event.event_type.startsWith("proposal.")) return ["proposals", "actions"];
  if (event.event_type.startsWith("map.")) return ["maps"];
  if (event.event_type === "world.updated") return ["maps", "actions", "proposals"];
  return [];
}

function expirationNote(reason: string) {
  return reason === "closed"
    ? "团会话已关闭，请重新开启或加入"
    : "会话凭证失效，请重新加入";
}

export function useWorkspaceRealtime(options: WorkspaceRealtimeOptions) {
  const [status, setStatus] = useState<RealtimeStatus>("offline");
  const [note, setNote] = useState("未连接");
  const callbacksRef = useRef(options.callbacks);
  const scopeRef = useRef<RealtimeScope>({
    campaignId: "",
    sessionId: "",
    memberId: "",
    role: ""
  });
  const refreshKindsRef = useRef(new Set<RefreshKind>());
  const refreshScopeRef = useRef<RealtimeScope | null>(null);
  const refreshTimerRef = useRef<number | null>(null);

  callbacksRef.current = options.callbacks;
  scopeRef.current = {
    campaignId: options.campaign?.id ?? "",
    sessionId: options.session?.id ?? "",
    memberId: options.identity?.member_id ?? "",
    role: options.identity?.role ?? ""
  };

  function clearRefreshQueue() {
    if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = null;
    refreshKindsRef.current.clear();
    refreshScopeRef.current = null;
  }

  function queueRefresh(scope: RealtimeScope, ...kinds: RefreshKind[]) {
    if (!sameScope(refreshScopeRef.current, scope)) {
      clearRefreshQueue();
      refreshScopeRef.current = scope;
    }
    kinds.forEach((kind) => refreshKindsRef.current.add(kind));
    if (refreshTimerRef.current !== null) return;
    refreshTimerRef.current = window.setTimeout(() => {
      const queuedScope = refreshScopeRef.current;
      const queuedKinds = new Set(refreshKindsRef.current);
      refreshTimerRef.current = null;
      refreshKindsRef.current.clear();
      refreshScopeRef.current = null;
      if (!queuedScope || !sameScope(scopeRef.current, queuedScope)) return;

      const callbacks = callbacksRef.current;
      const role = scopeRef.current.role;
      if (queuedKinds.has("identity")) callbacks.refreshIdentity();
      if (queuedKinds.has("members") && role === "kp") callbacks.refreshMembers();
      if (queuedKinds.has("pcs")) callbacks.refreshPcs();
      if (queuedKinds.has("actions") && role === "kp") callbacks.refreshActions();
      if (queuedKinds.has("proposals") && role === "kp") callbacks.refreshProposals();
      if (queuedKinds.has("maps")) callbacks.refreshMaps();
    }, 100);
  }

  function expireCurrentScope(reason: string, expectedScope = scopeRef.current) {
    if (!sameScope(scopeRef.current, expectedScope)) return;
    clearRefreshQueue();
    callbacksRef.current.expireSession(reason);
    setStatus("offline");
    setNote(expirationNote(reason));
  }

  function reset() {
    clearRefreshQueue();
    scopeRef.current = { campaignId: "", sessionId: "", memberId: "", role: "" };
    setStatus("offline");
    setNote("未连接");
  }

  function reportRefreshFailure(label: string) {
    setNote(`${label}失败，可手动刷新`);
  }

  const campaignId = options.campaign?.id ?? "";
  const sessionId = options.session?.id ?? "";
  const sessionStatus = options.session?.status ?? "";
  const memberId = options.identity?.member_id ?? "";

  useEffect(() => {
    if (!memberId || !sessionId || !campaignId || sessionStatus !== "active") {
      clearRefreshQueue();
      setStatus("offline");
      return;
    }

    const expectedScope: RealtimeScope = {
      campaignId,
      sessionId,
      memberId,
      role: scopeRef.current.role
    };
    const accessToken = readCampaignToken(campaignId);
    if (!accessToken) {
      expireCurrentScope("credential_missing", expectedScope);
      return;
    }

    const disconnect = connectRealtime({
      apiBase,
      accessToken,
      sessionId,
      campaignId,
      memberId,
      onStatus: (nextStatus, nextNote) => {
        if (!sameScope(scopeRef.current, expectedScope)) return;
        setStatus(nextStatus);
        setNote(nextNote);
      },
      onReady: (resyncRequired) => {
        if (!sameScope(scopeRef.current, expectedScope)) return;
        setNote(resyncRequired ? "游标已过期，正在安全地完整同步" : "实时同步已连接");
        const commonKinds: RefreshKind[] = ["identity", "pcs", "maps"];
        if (scopeRef.current.role === "kp") {
          commonKinds.push("members", "actions", "proposals");
        }
        queueRefresh(expectedScope, ...commonKinds);
      },
      onEvent: (event) => {
        if (
          !sameScope(scopeRef.current, expectedScope) ||
          event.session_id !== expectedScope.sessionId ||
          event.campaign_id !== expectedScope.campaignId
        ) return;
        if (event.event_type === "session.closed") {
          expireCurrentScope("closed", expectedScope);
          return;
        }
        const refreshKinds = refreshKindsForEvent(event);
        if (refreshKinds.length) queueRefresh(expectedScope, ...refreshKinds);
      },
      onExpired: (reason) => expireCurrentScope(reason, expectedScope)
    });

    return () => {
      disconnect();
      clearRefreshQueue();
    };
  }, [campaignId, memberId, sessionId, sessionStatus]);

  return {
    status,
    note,
    reset,
    reportRefreshFailure,
    expireCurrentScope
  };
}

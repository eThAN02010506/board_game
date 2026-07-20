export type RealtimeStatus = "offline" | "connecting" | "live" | "retrying";

export type RealtimeEvent = {
  cursor: string;
  session_id: string;
  campaign_id: string;
  event_type: string;
  resource_type: string | null;
  resource_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

type RealtimeConnectionOptions = {
  apiBase: string;
  accessToken: string;
  sessionId: string;
  campaignId: string;
  memberId: string;
  onStatus: (status: RealtimeStatus, note: string) => void;
  onReady: (resyncRequired: boolean) => void;
  onEvent: (event: RealtimeEvent) => void;
  onExpired: (reason: string) => void;
};

type ReadyMessage = {
  type: "realtime.ready";
  session_id: string;
  campaign_id: string;
  member_id: string;
  resync_required: boolean;
};

type EventMessage = {
  type: "realtime.event";
  event: RealtimeEvent;
};

class RealtimeTicketError extends Error {
  constructor(readonly status: number) {
    super(`Realtime ticket failed: ${status}`);
  }
}

async function requestRealtimeTicket(apiBase: string, accessToken: string): Promise<string> {
  const response = await fetch(`${apiBase}/realtime/tickets`, {
    method: "POST",
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json"
    }
  });
  if (!response.ok) throw new RealtimeTicketError(response.status);
  const result = (await response.json()) as { ticket?: unknown };
  if (typeof result.ticket !== "string" || !result.ticket) {
    throw new Error("Realtime ticket response is invalid");
  }
  return result.ticket;
}

function websocketUrl(apiBase: string) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${apiBase}/ws`;
}

function parseMessage(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function connectRealtime(options: RealtimeConnectionOptions): () => void {
  const cursorKey = `ai-kp-realtime-cursor:${options.sessionId}:${options.memberId}`;
  let disposed = false;
  let terminal = false;
  let attempts = 0;
  let reconnectTimer: number | null = null;
  let socket: WebSocket | null = null;
  let opening = false;

  const clearReconnectTimer = () => {
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  };

  const scheduleReconnect = () => {
    if (disposed || terminal || reconnectTimer !== null) return;
    const delays = [1000, 2000, 4000, 8000, 15000];
    const baseDelay = delays[Math.min(attempts, delays.length - 1)];
    const delay = baseDelay + Math.floor(Math.random() * 350);
    attempts += 1;
    options.onStatus("retrying", `连接中断，${Math.ceil(delay / 1000)} 秒后重试`);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      void openSocket();
    }, delay);
  };

  const expire = (reason: string) => {
    if (terminal || disposed) return;
    terminal = true;
    clearReconnectTimer();
    options.onStatus("offline", "会话凭证已失效");
    options.onExpired(reason);
  };

  const openSocket = async () => {
    if (disposed || terminal || opening || (socket && socket.readyState < WebSocket.CLOSING)) return;
    opening = true;
    options.onStatus(attempts ? "retrying" : "connecting", attempts ? "正在重新连接" : "正在连接");
    let ticket = "";
    try {
      ticket = await requestRealtimeTicket(options.apiBase, options.accessToken);
    } catch (error) {
      opening = false;
      if (error instanceof RealtimeTicketError && [401, 403, 404].includes(error.status)) {
        expire("credential_invalid");
        return;
      }
      scheduleReconnect();
      return;
    }
    if (disposed || terminal) {
      opening = false;
      return;
    }

    let candidate: WebSocket;
    try {
      candidate = new WebSocket(websocketUrl(options.apiBase));
    } catch {
      opening = false;
      scheduleReconnect();
      return;
    }
    opening = false;
    socket = candidate;
    candidate.onopen = () => {
      if (disposed || terminal || candidate !== socket) return;
      candidate.send(
        JSON.stringify({
          type: "authenticate",
          ticket,
          after_cursor: sessionStorage.getItem(cursorKey) ?? ""
        })
      );
      ticket = "";
    };
    candidate.onmessage = (messageEvent) => {
      if (disposed || terminal || candidate !== socket || typeof messageEvent.data !== "string") return;
      const message = parseMessage(messageEvent.data);
      if (!message || typeof message !== "object" || !("type" in message)) return;
      const messageType = (message as { type?: unknown }).type;
      if (messageType === "realtime.ping") {
        if (candidate.readyState === WebSocket.OPEN) candidate.send(JSON.stringify({ type: "pong" }));
        return;
      }
      if (messageType === "realtime.auth_expired") {
        const reason = (message as { reason?: unknown }).reason;
        expire(typeof reason === "string" ? reason : "credential_invalid");
        candidate.close(1000);
        return;
      }
      if (messageType === "realtime.ready") {
        const ready = message as ReadyMessage;
        if (
          ready.session_id !== options.sessionId ||
          ready.campaign_id !== options.campaignId ||
          ready.member_id !== options.memberId
        ) {
          expire("scope_mismatch");
          candidate.close(1000);
          return;
        }
        attempts = 0;
        options.onStatus("live", "实时同步已连接");
        options.onReady(Boolean(ready.resync_required));
        return;
      }
      if (messageType === "realtime.event") {
        const event = (message as EventMessage).event;
        if (
          !event ||
          event.session_id !== options.sessionId ||
          event.campaign_id !== options.campaignId ||
          typeof event.cursor !== "string"
        ) return;
        sessionStorage.setItem(cursorKey, event.cursor);
        options.onEvent(event);
      }
    };
    candidate.onerror = () => {
      if (!disposed && !terminal && candidate === socket) {
        options.onStatus("retrying", "实时连接异常");
      }
    };
    candidate.onclose = (closeEvent) => {
      if (candidate === socket) socket = null;
      if (disposed || terminal) return;
      if ([4401, 4403, 4404].includes(closeEvent.code)) {
        expire(`websocket_${closeEvent.code}`);
        return;
      }
      scheduleReconnect();
    };
  };

  const handleOnline = () => {
    if (disposed || terminal) return;
    clearReconnectTimer();
    if (!socket || socket.readyState === WebSocket.CLOSED) void openSocket();
  };

  window.addEventListener("online", handleOnline);
  void openSocket();

  return () => {
    disposed = true;
    clearReconnectTimer();
    window.removeEventListener("online", handleOnline);
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, "scope changed");
    socket = null;
  };
}

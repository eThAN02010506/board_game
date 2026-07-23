import { CircleDot, RefreshCw, Wifi, WifiOff } from "lucide-react";
import type { ReactNode } from "react";

import type { AuthIdentity } from "../../api/types";
import type { PageId, WorkspaceRoute } from "../router";
import { workspaceRoutes } from "../router";

type RealtimeStatus = "connecting" | "live" | "retrying" | "offline";

type Props = {
  activePage: PageId;
  campaignTitle: string;
  children: ReactNode;
  currentRoute: WorkspaceRoute;
  identity: AuthIdentity | null;
  loading: boolean;
  onNavigate: (page: PageId) => void;
  onRefresh: () => void;
  realtimeNote: string;
  realtimeStatus: RealtimeStatus;
};

export function AppLayout({
  activePage,
  campaignTitle,
  children,
  currentRoute,
  identity,
  loading,
  onNavigate,
  onRefresh,
  realtimeNote,
  realtimeStatus
}: Props) {
  return (
    <main className={`app-shell ${activePage === "investigators" ? "investigator-shell" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <CircleDot size={18} />
          AI KP Local
        </div>
        <nav>
          {workspaceRoutes.map((item) => {
            const Icon = item.icon;
            return (
              <button
                aria-pressed={activePage === item.id}
                className={`nav-item ${activePage === item.id ? "active" : ""}`}
                key={item.id}
                onClick={() => onNavigate(item.id)}
                type="button"
              >
                <Icon size={17} />
                {item.label}
                {item.planned && <small>规划</small>}
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">{currentRoute.label}</p>
            <h1>{campaignTitle}</h1>
          </div>
          <div className="topbar-actions">
            <button className="ghost-button" onClick={onRefresh} type="button">
              <RefreshCw size={16} />
              连接后端
            </button>
            {identity && (
              <span className={`realtime-pill ${realtimeStatus}`} title={realtimeNote}>
                {realtimeStatus === "live" ? <Wifi size={14} /> : <WifiOff size={14} />}
                {realtimeStatus === "live"
                  ? "实时同步"
                  : realtimeStatus === "connecting"
                    ? "正在连接"
                    : realtimeStatus === "retrying"
                      ? "重新连接"
                      : "同步离线"}
              </span>
            )}
            <span className={`status-pill ${loading ? "busy" : "ready"}`}>
              {loading
                ? "请求中"
                : identity
                  ? `${identity.role === "kp" ? "KP" : "玩家"} · ${identity.display_name}`
                  : "未加入会话"}
            </span>
          </div>
        </header>

        {children}
      </section>
    </main>
  );
}

import { CircleDot, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

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
  const headingRef = useRef<HTMLHeadingElement>(null);
  const initialRoute = useRef(true);

  useEffect(() => {
    if (initialRoute.current) {
      initialRoute.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [activePage]);

  return (
    <main className={`app-shell ${activePage === "investigators" ? "investigator-shell" : ""}`}>
      <a className="skip-link" href="#main-workspace">
        跳到主内容
      </a>
      <aside className="sidebar">
        <div className="brand">
          <CircleDot size={18} />
          AI KP Local
        </div>
        <nav>
          {workspaceRoutes.map((item) => {
            const Icon = item.icon;
            return (
              <a
                aria-current={activePage === item.id ? "page" : undefined}
                className={`nav-item ${activePage === item.id ? "active" : ""}`}
                href={item.path}
                key={item.id}
                onClick={(event) => {
                  if (
                    event.button !== 0 ||
                    event.metaKey ||
                    event.ctrlKey ||
                    event.shiftKey ||
                    event.altKey
                  ) {
                    return;
                  }
                  event.preventDefault();
                  onNavigate(item.id);
                }}
              >
                <Icon size={17} />
                {item.label}
                {item.planned && <small>规划</small>}
              </a>
            );
          })}
        </nav>
      </aside>

      <section className="workspace" id="main-workspace" tabIndex={-1}>
        <header className="topbar">
          <div>
            <p className="eyebrow">{currentRoute.label}</p>
            <h1 ref={headingRef} tabIndex={-1}>
              {campaignTitle}
            </h1>
            <span aria-live="polite" className="sr-only" role="status">
              已打开{currentRoute.label}
            </span>
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

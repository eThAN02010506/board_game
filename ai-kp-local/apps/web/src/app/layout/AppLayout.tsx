import { CircleDot, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import type { AuthIdentity } from "../../api/types";
import type { PageId, WorkspaceRoute } from "../router";
import { visibleWorkspaceRoutes } from "../router";

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
  const { t } = useTranslation();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const initialRoute = useRef(true);
  const role = identity?.role ?? "guest";
  const visibleRoutes = visibleWorkspaceRoutes(identity?.role);
  const workspaceLabel =
    role === "kp"
      ? t("common.workspace.kp")
      : role === "player"
        ? t("common.workspace.player")
        : role === "observer"
          ? "观战工作台"
          : t("common.workspace.local");

  useEffect(() => {
    if (initialRoute.current) {
      initialRoute.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [activePage]);

  return (
    <main className={`app-shell role-${role} ${activePage === "investigators" ? "investigator-shell" : ""}`}>
      <a className="skip-link" href="#main-workspace">
        {t("common.skipToContent")}
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <CircleDot size={19} />
          </span>
          <span className="brand-copy">
            <strong>AI KP Local</strong>
            <small>{workspaceLabel}</small>
          </span>
        </div>
        <nav aria-label={t("common.navLabel")}>
          {visibleRoutes.map((item) => {
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
                {t(`common.nav.${item.id}`)}
                {item.planned && <small>{t("common.planned")}</small>}
              </a>
            );
          })}
        </nav>
      </aside>

      <section className="workspace" id="main-workspace" tabIndex={-1}>
        <header className="topbar">
          <div>
            <p className="eyebrow">{workspaceLabel} · {t(`common.nav.${currentRoute.id}`)}</p>
            <h1 ref={headingRef} tabIndex={-1}>
              {campaignTitle}
            </h1>
            <span aria-live="polite" className="sr-only" role="status">
              {t("common.opened")}{t(`common.nav.${currentRoute.id}`)}
            </span>
          </div>
          <div className="topbar-actions">
            <button className="ghost-button" onClick={onRefresh} type="button">
              <RefreshCw size={16} />
              {t("common.connect")}
            </button>
            {identity && (
              <span className={`realtime-pill ${realtimeStatus}`} title={realtimeNote}>
                {realtimeStatus === "live" ? <Wifi size={14} /> : <WifiOff size={14} />}
                {realtimeStatus === "live"
                  ? t("common.realtime.live")
                  : realtimeStatus === "connecting"
                    ? t("common.realtime.connecting")
                    : realtimeStatus === "retrying"
                      ? t("common.realtime.retrying")
                      : t("common.realtime.offline")}
              </span>
            )}
            <span className={`status-pill ${loading ? "busy" : "ready"}`}>
              {loading
                ? t("common.loading")
                : identity
                  ? `${identity.role === "kp" ? "KP" : t("common.playerRole")} · ${identity.display_name}`
                  : t("common.noSession")}
            </span>
          </div>
        </header>

        {children}
      </section>
    </main>
  );
}

import {
  Brain,
  BookOpenCheck,
  Cpu,
  Dice5,
  FlaskConical,
  LayoutDashboard,
  Library,
  ListChecks,
  Map,
  ScrollText,
  UserRound,
  Users
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

export const workspaceRoutes = [
  { id: "play", label: "游玩桌面", icon: LayoutDashboard, path: "/play", planned: false },
  { id: "campaigns", label: "团与权限", icon: Users, path: "/campaigns", planned: false },
  { id: "investigators", label: "调查员", icon: UserRound, path: "/investigators", planned: false },
  { id: "maps", label: "地图棋子", icon: Map, path: "/maps", planned: false },
  { id: "memory", label: "角色记忆", icon: Brain, path: "/memory", planned: false },
  { id: "facts", label: "世界事实", icon: ScrollText, path: "/facts", planned: false },
  { id: "handouts", label: "手册线索", icon: BookOpenCheck, path: "/handouts", planned: false },
  { id: "npcs", label: "NPC", icon: Users, path: "/npcs", planned: false },
  { id: "rules", label: "规则知识", icon: Dice5, path: "/rules", planned: false },
  { id: "modules", label: "KP 本", icon: Library, path: "/modules", planned: false },
  { id: "models", label: "模型设置", icon: Cpu, path: "/models", planned: false },
  { id: "evaluations", label: "模拟团评测", icon: FlaskConical, path: "/evaluations", planned: false },
  { id: "planning", label: "功能规划", icon: ListChecks, path: "/planning", planned: true }
] as const;

export type PageId = (typeof workspaceRoutes)[number]["id"];
export type WorkspaceRoute = (typeof workspaceRoutes)[number];
export type WorkspaceRole = "kp" | "player" | null | undefined;

const playerAllowedPages = new Set<PageId>([
  "play",
  "campaigns",
  "investigators",
  "maps",
  "handouts"
]);

export function routeByPage(page: PageId): WorkspaceRoute {
  return workspaceRoutes.find((item) => item.id === page) ?? workspaceRoutes[0];
}

export function isPageAllowedForRole(page: PageId, role: WorkspaceRole): boolean {
  return role !== "player" || playerAllowedPages.has(page);
}

export function resolveWorkspaceRoute(page: PageId, role: WorkspaceRole): WorkspaceRoute {
  return routeByPage(isPageAllowedForRole(page, role) ? page : "play");
}

export function pageFromPath(pathname: string): PageId {
  return workspaceRoutes.find((item) => item.path === pathname)?.id ?? "play";
}

export function useWorkspaceRoute() {
  const [activePage, setActivePage] = useState<PageId>(() => pageFromPath(window.location.pathname));

  useEffect(() => {
    const onPopState = () => setActivePage(pageFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const route = useMemo(
    () => routeByPage(activePage),
    [activePage]
  );

  function navigate(next: PageId) {
    const destination = routeByPage(next);
    if (window.location.pathname !== destination.path) {
      window.history.pushState({}, "", destination.path);
    }
    setActivePage(destination.id);
  }

  return { activePage, navigate, route };
}

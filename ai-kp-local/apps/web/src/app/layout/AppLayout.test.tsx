import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuthIdentity } from "../../api/types";
import { AppLayout } from "./AppLayout";
import { workspaceRoutes } from "../router";

const baseIdentity: AuthIdentity = {
  member_id: "member_1",
  session_id: "session_1",
  campaign_id: "camp_1",
  role: "kp",
  display_name: "OldOnes",
  pc_id: null
};

function renderLayout(identity: AuthIdentity | null) {
  render(
    <AppLayout
      activePage="play"
      campaignTitle="雾港 1928"
      currentRoute={workspaceRoutes[0]}
      identity={identity}
      loading={false}
      onNavigate={vi.fn()}
      onRefresh={vi.fn()}
      realtimeNote="离线"
      realtimeStatus="offline"
    >
      <section>content</section>
    </AppLayout>
  );
}

describe("AppLayout role-specific navigation", () => {
  it("keeps KP operation pages out of the player shell", () => {
    renderLayout({ ...baseIdentity, role: "player", display_name: "Ada" });

    expect(screen.getByText("玩家游玩台")).toBeVisible();
    expect(screen.getByRole("link", { name: /游玩桌面/ })).toBeVisible();
    expect(screen.queryByRole("link", { name: /模型设置/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /模拟团评测/ })).not.toBeInTheDocument();
  });

  it("shows the full command navigation for KP", () => {
    renderLayout(baseIdentity);

    expect(screen.getByText("KP 导演台")).toBeVisible();
    expect(screen.getByRole("link", { name: /模型设置/ })).toBeVisible();
    expect(screen.getByRole("link", { name: /模拟团评测/ })).toBeVisible();
  });
});

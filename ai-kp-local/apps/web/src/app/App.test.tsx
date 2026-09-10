import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  credentialBridge,
  requestJson,
  requestJsonWithAccessToken
} from "../api/client";
import type {
  ActionAdjudication,
  AuthIdentity,
  Campaign,
  SessionInfo
} from "../api/types";
import { AppProviders } from "./providers";
import App from "./App";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    requestJson: vi.fn(),
    requestJsonWithAccessToken: vi.fn()
  };
});

vi.mock("../features/planning/useCapabilities", () => ({
  useCapabilities: () => ({
    capabilities: [],
    error: "",
    loading: false,
    refresh: vi.fn()
  })
}));

vi.mock("../realtime/provider", () => ({
  useWorkspaceRealtime: () => ({
    status: "offline",
    note: "未连接",
    reset: vi.fn(),
    reportRefreshFailure: vi.fn(),
    expireCurrentScope: vi.fn()
  })
}));

const campaigns: Campaign[] = [
  {
    id: "camp_a",
    title: "团 A",
    system: "coc7",
    current_time: "1928-01-01"
  },
  {
    id: "camp_b",
    title: "团 B",
    system: "coc7",
    current_time: "1928-01-02"
  }
];

function renderApp() {
  return render(
    <AppProviders>
      <App />
    </AppProviders>
  );
}

describe("App workspace request isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    credentialBridge.session("");
    credentialBridge.admin("");
    credentialBridge.player("");
    window.history.replaceState({}, "", "/campaigns");
    vi.mocked(requestJsonWithAccessToken).mockResolvedValue(campaigns);
  });

  it("preserves a campaign token when restoring identity hits a transient failure", async () => {
    sessionStorage.setItem("ai-kp-session-token:camp_a", "stable-token");
    vi.mocked(requestJson).mockRejectedValue(
      new ApiError("后端短暂不可用", 503)
    );

    renderApp();

    expect(await screen.findByText("后端短暂不可用")).toBeVisible();
    expect(sessionStorage.getItem("ai-kp-session-token:camp_a")).toBe("stable-token");
  });

  it("evicts a campaign token after an explicit authentication failure", async () => {
    sessionStorage.setItem("ai-kp-session-token:camp_a", "expired-token");
    vi.mocked(requestJson).mockRejectedValue(
      new ApiError("会话已失效", 401)
    );

    renderApp();

    expect(await screen.findByText("会话已失效")).toBeVisible();
    expect(sessionStorage.getItem("ai-kp-session-token:camp_a")).toBeNull();
  });

  it("replaces a stale durable player identity while claiming a fresh seat", async () => {
    const user = userEvent.setup();
    localStorage.setItem("ai-kp-player-profile-token", "stale-player");
    const observedPlayerTokens: string[] = [];
    let claimCalls = 0;
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/session-seats/claim") {
        claimCalls += 1;
        observedPlayerTokens.push(credentialBridge.snapshot().playerToken);
        if (claimCalls === 1) {
          throw new ApiError("Invalid player profile token", 401);
        }
        return {
          session: { id: "session_a", campaign_id: "camp_a", title: "团 A", status: "active" },
          member: {
            id: "member_player",
            session_id: "session_a",
            campaign_id: "camp_a",
            role: "player",
            display_name: "顾闻舟",
            pc_id: null,
            player_profile_id: "profile_fresh",
            revoked_at: null
          },
          campaign: campaigns[0],
          access_token: "player-access",
          player_token: "fresh-player"
        };
      }
      return [];
    });

    renderApp();

    await user.type(await screen.findByLabelText("席位邀请码"), "AAAA-BBBB-CCCC");
    await user.clear(screen.getByLabelText("玩家显示名"));
    await user.type(screen.getByLabelText("玩家显示名"), "顾闻舟");
    await user.click(screen.getByRole("button", { name: "认领并进入席位" }));

    await waitFor(() => expect(claimCalls).toBe(2));
    expect(observedPlayerTokens).toEqual(["stale-player", ""]);
    expect(localStorage.getItem("ai-kp-player-profile-token")).toBe("fresh-player");
    expect(credentialBridge.snapshot().playerToken).toBe("fresh-player");
  });

  it("ignores a late response after the user switches to another campaign", async () => {
    const user = userEvent.setup();
    let resolveIdentity: ((identity: AuthIdentity) => void) | undefined;
    const pendingIdentity = new Promise<AuthIdentity>((resolve) => {
      resolveIdentity = resolve;
    });
    sessionStorage.setItem("ai-kp-session-token:camp_a", "stable-token");
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/auth/me") return pendingIdentity;
      return {};
    });

    renderApp();

    await waitFor(() => expect(requestJson).toHaveBeenCalledWith("/auth/me"));
    await user.click(screen.getByRole("button", { name: /团 B/ }));
    expect(await screen.findByText(/已切换到《团 B》/)).toBeVisible();

    resolveIdentity?.({
      member_id: "member_a",
      session_id: "session_a",
      campaign_id: "camp_a",
      role: "kp",
      display_name: "KP A",
      pc_id: null
    });

    await waitFor(() => {
      expect(screen.getByText(/已切换到《团 B》/)).toBeVisible();
      expect(screen.queryByText(/KP A/)).not.toBeInTheDocument();
    });
  });

  it("ignores the legacy auto-confirm preference until the player confirms a pending ruling", async () => {
    const user = userEvent.setup();
    const identity: AuthIdentity = {
      member_id: "member_player",
      session_id: "session_a",
      campaign_id: "camp_a",
      role: "player",
      display_name: "顾闻舟",
      pc_id: "pc_a"
    };
    const session: SessionInfo = {
      id: "session_a",
      campaign_id: "camp_a",
      title: "团 A",
      status: "active"
    };
    const adjudication: ActionAdjudication = {
      id: "adjudication_a",
      action_id: "action_a",
      proposal_id: "proposal_a",
      mode: "skill_check",
      status: "pending",
      version: 2,
      reason: "检查锁孔需要侦查。",
      prompt: "",
      skill_options: [{
        skill_name: "侦查",
        skill_key: "coc7.spot_hidden",
        target: 60,
        difficulty: "regular",
        reason: "观察锁孔的异常痕迹。",
        hidden: false
      }],
      selected_skill: "侦查",
      source_model: "test-model",
      source_error: null,
      ruling: { resolution: "check", method: "侦查" }
    };
    const confirmPath = "/player-actions/action_a/adjudication/confirm";

    localStorage.setItem("auto-confirm-adjudication", "on");
    sessionStorage.setItem("ai-kp-session-token:camp_a", "player-access");
    window.history.replaceState({}, "", "/play");
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/auth/me") return identity;
      if (url === "/sessions/session_a") return session;
      if (url === "/campaigns/camp_a/action-adjudications/pending") {
        return [adjudication];
      }
      if (url === "/campaigns/camp_a/module-runs/play-state") {
        return {
          accepts_actions: true,
          status: "active",
          module_title: "雾港疑云",
          completed_at: null,
          ending_id: null,
          ending_title: null,
          opening_narration: null
        };
      }
      if (url === "/campaigns/camp_a/consequence-signals") {
        return { run_id: null, state_version: null, signals: [], events: [] };
      }
      if (url === confirmPath) {
        return {
          status: "completed",
          player_action: {
            id: "action_a",
            campaign_id: "camp_a",
            member_id: identity.member_id,
            pc_id: identity.pc_id,
            action_text: "我检查锁孔。",
            location: null,
            map_id: null,
            status: "resolved",
            proposal_id: "proposal_a"
          },
          proposal: null,
          checks: [],
          adjudication: null,
          message: "裁定已确认并执行。"
        };
      }
      return [];
    });

    renderApp();

    const confirmButton = await screen.findByRole("button", { name: "确认此裁定" });
    expect(screen.getByRole("checkbox", { name: "AI KP 自动推进" })).toBeChecked();
    expect(
      vi.mocked(requestJson).mock.calls.filter(([url]) => url === confirmPath)
    ).toHaveLength(0);

    await user.click(confirmButton);

    await waitFor(() => {
      expect(
        vi.mocked(requestJson).mock.calls.filter(([url]) => url === confirmPath)
      ).toHaveLength(1);
    });
  });
});

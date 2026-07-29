import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  credentialBridge,
  requestJson,
  requestJsonWithAccessToken
} from "../api/client";
import type { AuthIdentity, Campaign } from "../api/types";
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
});

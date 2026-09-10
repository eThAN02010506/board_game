import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthIdentity, Campaign, SessionZeroView } from "../../api/types";
import { SessionZeroPanel } from "./SessionZeroPanel";

const saveSessionZeroConfig = vi.fn();
const saveSessionZeroPreferences = vi.fn();
const confirmSessionZero = vi.fn();
const triggerSessionSafety = vi.fn();
const resolveSessionSafety = vi.fn();

vi.mock("../../api/client", () => ({
  saveSessionZeroConfig: (...args: unknown[]) => saveSessionZeroConfig(...args),
  saveSessionZeroPreferences: (...args: unknown[]) => saveSessionZeroPreferences(...args),
  confirmSessionZero: (...args: unknown[]) => confirmSessionZero(...args),
  triggerSessionSafety: (...args: unknown[]) => triggerSessionSafety(...args),
  resolveSessionSafety: (...args: unknown[]) => resolveSessionSafety(...args)
}));

const campaign: Campaign = {
  id: "campaign-1",
  title: "Long campaign",
  system: "coc7",
  ruleset_id: "coc7-keeper-cn-2002c",
  ruleset_version: "2002c",
  current_time: null
};

const kp: AuthIdentity = {
  campaign_id: campaign.id,
  display_name: "KP",
  member_id: "member-kp",
  pc_id: null,
  role: "kp",
  session_id: "session-1"
};

const pending: SessionZeroView = {
  revision: {
    id: "setup-2",
    campaign_id: campaign.id,
    version: 2,
    status: "pending",
    config: {
      ruleset_id: "coc7-keeper-cn-2002c",
      ruleset_version: "2002c",
      worldview: "Mystery",
      hosting_mode: "ai_kp",
      expected_player_count: 2,
      campaign_type: "ongoing",
      starting_power: "standard",
      allowed_character_options: [],
      house_rules: [],
      default_visibility: "party",
      style: {},
      content_warnings: [],
      lines: [],
      veils: [],
      safety_default: "pause",
    idle_policy: "wait",
    idle_timeout_seconds: 300,
      allow_player_whispers: false
    },
    created_at: "2026-08-22T00:00:00Z",
    activated_at: null
  },
  ready: false,
  confirmed: false,
  confirmed_count: 1,
  required_count: 3,
  public_preferences: [],
  own_preferences: null,
  model_policy: null,
  active_safety_event: null,
  can_resolve_safety: false
};

describe("SessionZeroPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    saveSessionZeroConfig.mockResolvedValue(pending);
    saveSessionZeroPreferences.mockResolvedValue(pending);
    confirmSessionZero.mockResolvedValue(pending);
    triggerSessionSafety.mockResolvedValue({ session_zero: pending });
    resolveSessionSafety.mockResolvedValue({ session_zero: pending });
  });

  it("lets the KP create the first complete setup revision", async () => {
    const onChanged = vi.fn();
    render(
      <SessionZeroPanel
        campaign={campaign}
        error=""
        identity={kp}
        loading={false}
        onChanged={onChanged}
        view={{ ...pending, revision: null, confirmed_count: 0, required_count: 1 }}
      />
    );
    fireEvent.change(screen.getByLabelText("世界观"), {
      target: { value: "Player-authored mystery world" }
    });
    fireEvent.click(screen.getByRole("button", { name: "保存新版本并确认" }));

    await waitFor(() => expect(saveSessionZeroConfig).toHaveBeenCalled());
    const payload = saveSessionZeroConfig.mock.calls[0][1];
    expect(payload.expected_version).toBe(0);
    expect(payload.ruleset_id).toBe("coc7-keeper-cn-2002c");
    expect(payload.worldview).toBe("Player-authored mystery world");
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("keeps private preferences separate and exposes an explanation-free safety tool", async () => {
    const player = { ...kp, role: "player" as const, member_id: "member-player" };
    render(
      <SessionZeroPanel
        campaign={campaign}
        error=""
        identity={player}
        loading={false}
        onChanged={vi.fn()}
        view={pending}
      />
    );
    fireEvent.click(screen.getByText("我的风格与私密边界"));
    fireEvent.change(screen.getByLabelText("私密风格偏好"), {
      target: { value: "lower horror" }
    });
    fireEvent.change(screen.getByLabelText("我的 Lines（不会标记是谁）"), {
      target: { value: "private boundary" }
    });
    fireEvent.click(screen.getByRole("button", { name: "保存私密偏好并创建待确认版本" }));
    await waitFor(() => expect(saveSessionZeroPreferences).toHaveBeenCalledWith(
      campaign.id,
      expect.objectContaining({
        expected_version: 2,
        private_style: { notes: "lower horror" },
        lines: ["private boundary"]
      })
    ));

    fireEvent.click(screen.getByRole("button", { name: "暂停" }));
    await waitFor(() => expect(triggerSessionSafety).toHaveBeenCalledWith(
      campaign.id,
      "pause"
    ));
    expect(screen.queryByLabelText(/原因/)).not.toBeInTheDocument();
  });
});

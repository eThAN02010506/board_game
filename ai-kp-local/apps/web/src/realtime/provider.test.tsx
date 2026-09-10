import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthIdentity, Campaign, SessionInfo } from "../api/types";
import { connectRealtime, type RealtimeEvent } from "../realtime";
import { useWorkspaceRealtime } from "./provider";

vi.mock("../realtime", () => ({
  connectRealtime: vi.fn()
}));

type RealtimeConnection = Parameters<typeof connectRealtime>[0];

const campaign: Campaign = {
  id: "campaign-realtime",
  title: "Realtime campaign",
  system: "coc7",
  current_time: null
};

const session: SessionInfo = {
  id: "session-realtime",
  campaign_id: campaign.id,
  title: campaign.title,
  status: "active"
};

function identity(role: AuthIdentity["role"]): AuthIdentity {
  return {
    member_id: `member-${role}`,
    session_id: session.id,
    campaign_id: campaign.id,
    role,
    display_name: role,
    pc_id: role === "player" ? "pc-player" : null
  };
}

function callbacks() {
  return {
    refreshIdentity: vi.fn(),
    refreshMembers: vi.fn(),
    refreshSeats: vi.fn(),
    refreshChecks: vi.fn(),
    refreshPcs: vi.fn(),
    refreshActions: vi.fn(),
    refreshProposals: vi.fn(),
    refreshMaps: vi.fn(),
    expireSession: vi.fn()
  };
}

function worldUpdated(): RealtimeEvent {
  return {
    cursor: "cursor-world-updated",
    session_id: session.id,
    campaign_id: campaign.id,
    event_type: "world.updated",
    resource_type: "campaign",
    resource_id: campaign.id,
    payload: {},
    created_at: "2026-08-21T10:00:00Z"
  };
}

describe("useWorkspaceRealtime", () => {
  let connection: RealtimeConnection | null;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    sessionStorage.clear();
    connection = null;
    vi.mocked(connectRealtime).mockImplementation((options) => {
      connection = options;
      return vi.fn();
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function renderRealtime(role: AuthIdentity["role"]) {
    const activeIdentity = identity(role);
    const handlers = callbacks();
    sessionStorage.setItem(
      `ai-kp-session-token:${campaign.id}`,
      `${role}-access-token`
    );
    renderHook(() => useWorkspaceRealtime({
      campaign,
      session,
      identity: activeIdentity,
      callbacks: handlers
    }));
    expect(connection).not.toBeNull();
    return handlers;
  }

  async function flushRefreshQueue() {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
  }

  it("refreshes a player's public turns after a shared world update", async () => {
    const handlers = renderRealtime("player");

    act(() => connection?.onEvent(worldUpdated()));
    await flushRefreshQueue();

    expect(handlers.refreshActions).toHaveBeenCalledOnce();
    expect(handlers.refreshMaps).toHaveBeenCalledOnce();
    expect(handlers.refreshProposals).not.toHaveBeenCalled();
    expect(handlers.refreshMembers).not.toHaveBeenCalled();
    expect(handlers.refreshSeats).not.toHaveBeenCalled();
  });

  it("resynchronizes public turns when a player realtime connection becomes ready", async () => {
    const handlers = renderRealtime("player");

    act(() => connection?.onReady(true));
    await flushRefreshQueue();

    expect(handlers.refreshIdentity).toHaveBeenCalledOnce();
    expect(handlers.refreshChecks).toHaveBeenCalledOnce();
    expect(handlers.refreshPcs).toHaveBeenCalledOnce();
    expect(handlers.refreshMaps).toHaveBeenCalledOnce();
    expect(handlers.refreshActions).toHaveBeenCalledOnce();
    expect(handlers.refreshProposals).not.toHaveBeenCalled();
  });

  it("preserves KP action and proposal refresh behavior", async () => {
    const handlers = renderRealtime("kp");

    act(() => connection?.onEvent(worldUpdated()));
    await flushRefreshQueue();

    expect(handlers.refreshActions).toHaveBeenCalledOnce();
    expect(handlers.refreshProposals).toHaveBeenCalledOnce();
    expect(handlers.refreshMaps).toHaveBeenCalledOnce();
  });
});

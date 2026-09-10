import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getCurrentParallelActionPlayerBatch } from "../../api/client";
import type { AuthIdentity, ParallelActionPlayerBatch } from "../../api/types";
import { useParallelActionBatch } from "./useParallelActionBatch";

vi.mock("../../api/client", () => ({
  getCurrentParallelActionPlayerBatch: vi.fn()
}));

const activeBatch: ParallelActionPlayerBatch = {
  id: "batch-own",
  status: "awaiting_confirmation",
  version: 4,
  participant_count: 3,
  confirmed_count: 1,
  waiting_count: 2,
  self_phase: "awaiting_confirmation",
  own_item: {
    action_id: "action-own",
    adjudication: {
      id: "adjudication-own",
      action_id: "action-own",
      mode: "direct_resolution",
      status: "pending",
      version: 1,
      reason: "行动可以直接执行。",
      prompt: "",
      skill_options: [],
      selected_skill: null,
      source_model: "kernel:test",
      updated_at: "2026-08-21T10:00:00Z",
      confirmed_at: null,
      ruling: {}
    },
    checks: []
  },
  updated_at: "2026-08-21T10:00:00Z",
  settled_at: null,
  public_message: "请确认自己的裁定。"
};

function playerIdentity(memberId: string): AuthIdentity {
  return {
    campaign_id: "campaign-a",
    display_name: memberId,
    member_id: memberId,
    pc_id: null,
    role: "player",
    session_id: "session-a"
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function Probe(props: {
  campaignId: string;
  identity?: AuthIdentity | null;
  refreshKey?: string;
}) {
  const { batch, error } = useParallelActionBatch(
    props.campaignId,
    props.identity === undefined ? playerIdentity("member-a") : props.identity,
    props.refreshKey
  );
  return <output>{batch?.id ?? (error || "none")}</output>;
}

describe("useParallelActionBatch", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("loads the member-scoped current batch and polls while it is active", async () => {
    vi.useFakeTimers();
    vi.mocked(getCurrentParallelActionPlayerBatch).mockResolvedValue(activeBatch);

    render(<Probe campaignId="campaign-a" />);
    await act(async () => undefined);

    expect(screen.getByText("batch-own")).toBeVisible();
    expect(getCurrentParallelActionPlayerBatch).toHaveBeenCalledWith("campaign-a");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(getCurrentParallelActionPlayerBatch).toHaveBeenCalledTimes(2);
  });

  it("clears the previous campaign and ignores requests while disabled", async () => {
    vi.mocked(getCurrentParallelActionPlayerBatch).mockResolvedValue(activeBatch);
    const view = render(<Probe campaignId="campaign-a" />);
    expect(await screen.findByText("batch-own")).toBeVisible();

    view.rerender(<Probe campaignId="campaign-b" identity={null} />);

    expect(await screen.findByText("none")).toBeVisible();
    expect(getCurrentParallelActionPlayerBatch).toHaveBeenCalledTimes(1);
  });

  it("discovers a batch prepared later by the KP while the player stays on the desk", async () => {
    vi.useFakeTimers();
    vi.mocked(getCurrentParallelActionPlayerBatch)
      .mockResolvedValueOnce(null)
      .mockResolvedValue(activeBatch);

    render(<Probe campaignId="campaign-a" />);
    await act(async () => undefined);
    expect(screen.getByText("none")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(screen.getByText("batch-own")).toBeVisible();
    expect(getCurrentParallelActionPlayerBatch).toHaveBeenCalledTimes(2);
  });

  it("does not render player A's deferred response after switching to player B in the same campaign", async () => {
    const playerARequest = deferred<ParallelActionPlayerBatch | null>();
    const playerBRequest = deferred<ParallelActionPlayerBatch | null>();
    const playerBBatch = { ...activeBatch, id: "batch-player-b" };
    vi.mocked(getCurrentParallelActionPlayerBatch)
      .mockReturnValueOnce(playerARequest.promise)
      .mockReturnValueOnce(playerBRequest.promise);

    const view = render(
      <Probe campaignId="campaign-a" identity={playerIdentity("member-a")} />
    );
    await act(async () => undefined);

    view.rerender(
      <Probe campaignId="campaign-a" identity={playerIdentity("member-b")} />
    );
    expect(screen.getByText("none")).toBeVisible();

    await act(async () => {
      playerBRequest.resolve(playerBBatch);
    });
    expect(screen.getByText("batch-player-b")).toBeVisible();

    await act(async () => {
      playerARequest.resolve(activeBatch);
    });
    expect(screen.getByText("batch-player-b")).toBeVisible();
    expect(screen.queryByText("batch-own")).not.toBeInTheDocument();
  });
});

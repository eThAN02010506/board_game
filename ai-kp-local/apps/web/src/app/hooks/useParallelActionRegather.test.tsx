import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getCurrentParallelActionPlayerRegather } from "../../api/client";
import type { AuthIdentity, ParallelActionPlayerRegather } from "../../api/types";
import { useParallelActionRegather } from "./useParallelActionRegather";

vi.mock("../../api/client", () => ({
  getCurrentParallelActionPlayerRegather: vi.fn()
}));

const regather: ParallelActionPlayerRegather = {
  id: "regather-own",
  status: "gathering",
  version: 3,
  participant_count: 4,
  submitted_count: 1,
  waiting_count: 3,
  self_phase: "waiting_for_others",
  own_action_id: "action-own",
  updated_at: "2026-08-22T00:00:00Z",
  public_message: "Waiting for 3 participant(s) to resubmit."
};

function identity(memberId: string): AuthIdentity {
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
  const promise = new Promise<T>((next) => { resolve = next; });
  return { promise, resolve };
}

function Probe({ memberId }: { memberId: string }) {
  const { regather: current } = useParallelActionRegather(
    "campaign-a",
    identity(memberId)
  );
  return <output>{current?.id ?? "none"}</output>;
}

describe("useParallelActionRegather", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("polls the durable regrouping barrier while it is active", async () => {
    vi.useFakeTimers();
    vi.mocked(getCurrentParallelActionPlayerRegather).mockResolvedValue(regather);
    render(<Probe memberId="member-a" />);
    await act(async () => undefined);
    expect(screen.getByText("regather-own")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(getCurrentParallelActionPlayerRegather).toHaveBeenCalledTimes(2);
  });

  it("never renders player A's late response after identity switches", async () => {
    const first = deferred<ParallelActionPlayerRegather | null>();
    const second = deferred<ParallelActionPlayerRegather | null>();
    vi.mocked(getCurrentParallelActionPlayerRegather)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const view = render(<Probe memberId="member-a" />);
    await act(async () => undefined);
    view.rerender(<Probe memberId="member-b" />);
    await act(async () => { second.resolve({ ...regather, id: "regather-b" }); });
    expect(screen.getByText("regather-b")).toBeVisible();
    await act(async () => { first.resolve(regather); });
    expect(screen.queryByText("regather-own")).not.toBeInTheDocument();
  });
});

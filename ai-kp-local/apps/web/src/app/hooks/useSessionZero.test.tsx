import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getSessionZero } from "../../api/client";
import type { AuthIdentity, SessionZeroView } from "../../api/types";
import { useSessionZero } from "./useSessionZero";

vi.mock("../../api/client", () => ({ getSessionZero: vi.fn() }));

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

function view(line: string): SessionZeroView {
  return {
    revision: null,
    ready: false,
    confirmed: false,
    confirmed_count: 0,
    required_count: 2,
    public_preferences: [],
    own_preferences: { lines: [line] },
    model_policy: null,
    active_safety_event: null,
    can_resolve_safety: false
  };
}

function Probe({ memberId }: { memberId: string }) {
  const current = useSessionZero("campaign-a", identity(memberId));
  return <output>{current.view?.own_preferences?.lines?.[0] ?? "none"}</output>;
}

describe("useSessionZero", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("drops a private late response when the authenticated seat changes", async () => {
    const first = deferred<SessionZeroView>();
    const second = deferred<SessionZeroView>();
    vi.mocked(getSessionZero)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const rendered = render(<Probe memberId="member-a" />);
    await act(async () => undefined);
    rendered.rerender(<Probe memberId="member-b" />);
    await act(async () => { second.resolve(view("member-b-private")); });
    expect(screen.getByText("member-b-private")).toBeVisible();
    await act(async () => { first.resolve(view("member-a-private")); });
    expect(screen.queryByText("member-a-private")).not.toBeInTheDocument();
  });
});

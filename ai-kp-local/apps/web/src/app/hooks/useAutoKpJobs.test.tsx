import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "../../api/client";
import type { AuthIdentity, AutoKpJob } from "../../api/types";
import { useAutoKpJobs } from "./useAutoKpJobs";

vi.mock("../../api/client", () => ({
  requestJson: vi.fn()
}));

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

function job(id: string): AutoKpJob {
  return {
    attempt_count: 0,
    campaign_id: "campaign-a",
    created_at: "2026-08-21T10:00:00Z",
    id,
    job_type: "player_action",
    max_attempts: 3,
    resource_id: `action-${id}`,
    stage: "complete",
    status: "succeeded",
    updated_at: "2026-08-21T10:00:00Z"
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function Probe(props: { identity: AuthIdentity | null }) {
  const { jobs } = useAutoKpJobs("campaign-a", props.identity);
  return <output>{jobs.map((item) => item.id).join(",") || "none"}</output>;
}

describe("useAutoKpJobs", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("does not expose player A's deferred job metadata after switching to player B", async () => {
    const playerARequest = deferred<AutoKpJob[]>();
    const playerBRequest = deferred<AutoKpJob[]>();
    vi.mocked(requestJson)
      .mockReturnValueOnce(playerARequest.promise)
      .mockReturnValueOnce(playerBRequest.promise);

    const view = render(<Probe identity={playerIdentity("member-a")} />);
    await act(async () => undefined);

    view.rerender(<Probe identity={playerIdentity("member-b")} />);
    expect(screen.getByText("none")).toBeVisible();

    await act(async () => {
      playerBRequest.resolve([job("job-player-b")]);
    });
    expect(screen.getByText("job-player-b")).toBeVisible();

    await act(async () => {
      playerARequest.resolve([job("job-player-a")]);
    });
    expect(screen.getByText("job-player-b")).toBeVisible();
    expect(screen.queryByText("job-player-a")).not.toBeInTheDocument();
  });
});

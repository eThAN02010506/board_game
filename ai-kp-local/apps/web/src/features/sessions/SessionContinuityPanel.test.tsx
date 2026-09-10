import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuthIdentity, SessionContinuityView } from "../../api/types";
import { SessionContinuityPanel } from "./SessionContinuityPanel";

const kp: AuthIdentity = {
  campaign_id: "campaign-1",
  display_name: "KP",
  member_id: "member-kp",
  pc_id: null,
  role: "kp",
  session_id: "session-1"
};

function view(status: "in_progress" | "paused" | "ended", role: "kp" | "player" | "observer"): SessionContinuityView {
  return {
    accepts_actions: status === "in_progress",
    current_episode: {
      campaign_id: "campaign-1",
      ended_at: status === "ended" ? "2026-08-22T10:00:00Z" : null,
      id: "episode-1",
      sequence_no: 3,
      session_id: "session-1",
      started_at: "2026-08-22T09:00:00Z",
      status,
      version: 4
    },
    latest_snapshot: {
      campaign_id: "campaign-1",
      created_at: "2026-08-22T10:00:00Z",
      episode_id: "episode-1",
      event_window_hash: "public-hash",
      generation_cutoff: "2026-08-22T10:00:00Z",
      id: "snapshot-1",
      projection: {
        campaign_time: "1920-01-01T20:00:00Z",
        ended_at: "2026-08-22T10:00:00Z",
        episode_sequence: 3,
        module: { current_scene_title: "Clock tower" },
        recent_events: [],
        summary_text: role === "kp" ? "KP projection" : "Public previously on",
        current_objectives: [{
          id: "objective-1",
          title: "Find the missing mechanism",
          public_description: "The route remains blocked.",
          status: "blocked",
          version: 2
        }],
        unresolved_questions: [{
          id: "objective-1",
          title: "Find the missing mechanism",
          public_description: "The route remains blocked.",
          status: "blocked",
          version: 2
        }],
        known_npcs: [{ id: "npc-1", name: "Clock keeper" }],
        ...(role === "kp" ? {
          private_event_count: 1,
          private_events: [{ id: "secret-1", summary: "Hidden threat" }]
        } : {})
      },
      session_id: "session-1"
    }
  };
}

describe("SessionContinuityPanel", () => {
  it("lets the KP end and continue an episode", () => {
    const onEnd = vi.fn();
    const { rerender } = render(
      <SessionContinuityPanel busy={false} error="" identity={kp} onContinue={vi.fn()}
        onEnd={onEnd} onRefresh={vi.fn()} onTransition={vi.fn()} view={view("in_progress", "kp")} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Session End" }));
    expect(onEnd).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByText(/KP 私密回顾/));
    expect(screen.getByText("Hidden threat")).toBeInTheDocument();

    const onContinue = vi.fn();
    rerender(
      <SessionContinuityPanel busy={false} error="" identity={kp} onContinue={onContinue}
        onEnd={onEnd} onRefresh={vi.fn()} onTransition={vi.fn()} view={view("ended", "kp")} />
    );
    fireEvent.click(screen.getByRole("button", { name: "Continue Campaign" }));
    expect(onContinue).toHaveBeenCalledOnce();
  });

  it("shows only the supplied safe projection and no authority controls to observers", () => {
    render(
      <SessionContinuityPanel busy={false} error="" identity={{ ...kp, role: "observer" }}
        onContinue={vi.fn()} onEnd={vi.fn()} onRefresh={vi.fn()} onTransition={vi.fn()}
        view={view("ended", "observer")} />
    );
    expect(screen.getByText("Public previously on")).toBeInTheDocument();
    expect(screen.queryByText("KP projection")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue Campaign" })).not.toBeInTheDocument();
    expect(screen.getByText(/等待 KP/)).toBeInTheDocument();
    expect(screen.getAllByText(/Find the missing mechanism/)).toHaveLength(2);
    expect(screen.getByText(/Clock keeper/)).toBeInTheDocument();
  });
});

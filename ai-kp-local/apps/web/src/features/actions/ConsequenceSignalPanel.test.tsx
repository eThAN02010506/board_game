import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "../../api/client";
import { ConsequenceSignalPanel } from "./ConsequenceSignalPanel";

vi.mock("../../api/client", () => ({ requestJson: vi.fn() }));

const requestJsonMock = vi.mocked(requestJson);

describe("ConsequenceSignalPanel", () => {
  beforeEach(() => {
    requestJsonMock.mockReset();
  });

  it("renders only the server-projected public world signals for a player", async () => {
    const user = userEvent.setup();
    requestJsonMock.mockResolvedValue({
      run_id: "run_1",
      state_version: null,
      signals: [
        {
          signal_id: "signal-opaque",
          title: "Unwanted attention",
          display_mode: "stage",
          severity: 2,
          label: "You are being noticed",
          description: "Questions now draw visible attention.",
          current_value: null,
          source_path: null,
          band_id: null,
          active: true
        }
      ],
      events: []
    });

    render(
      <ConsequenceSignalPanel
        campaignId="camp_1"
        refreshKey="job_1:succeeded"
        role="player"
      />
    );

    expect(await screen.findByText("Unwanted attention")).toBeVisible();
    expect(screen.getByText("You are being noticed")).toBeVisible();
    expect(screen.queryByText(/source_path|secret_rival/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "刷新世界动态" }));
    expect(requestJsonMock).toHaveBeenLastCalledWith(
      "/campaigns/camp_1/consequence-signals"
    );
  });

  it("shows authoritative diagnostics only in the KP projection", async () => {
    requestJsonMock.mockResolvedValue({
      run_id: "run_1",
      state_version: 4,
      signals: [
        {
          signal_id: "secret_rival_schedule",
          title: "Rival investigators are preparing",
          display_mode: "narrative",
          severity: 1,
          label: "Preparing",
          description: "",
          current_value: "preparing",
          source_path: "facts.rival.phase",
          band_id: "preparing",
          active: true
        },
        {
          signal_id: "structured_diagnostic",
          title: "Weather diagnostic",
          display_mode: "exact",
          severity: 0,
          label: "Available",
          description: "",
          current_value: { state: "calm" },
          source_path: "facts.weather",
          band_id: "default",
          active: true
        }
      ],
      events: []
    });

    render(
      <ConsequenceSignalPanel campaignId="camp_1" refreshKey="" role="kp" />
    );

    expect(await screen.findByText("Rival investigators are preparing")).toBeVisible();
    expect(screen.getByText("状态 v4")).toBeVisible();
    expect(screen.getByText("facts.rival.phase · preparing")).toBeVisible();
    expect(screen.getByText('{"state":"calm"}')).toBeVisible();
  });

  it("does not let a late response from the previous campaign replace the view", async () => {
    let resolveFirst: ((value: unknown) => void) | undefined;
    requestJsonMock.mockImplementation(async (url) => {
      if (String(url).includes("camp_old")) {
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return {
        run_id: "run_new",
        state_version: null,
        signals: [{
          signal_id: "signal-new",
          title: "New campaign weather",
          display_mode: "narrative",
          severity: 0,
          label: "Clear",
          description: "",
          current_value: null,
          source_path: null,
          band_id: null,
          active: true
        }],
        events: []
      };
    });
    const { rerender } = render(
      <ConsequenceSignalPanel campaignId="camp_old" refreshKey="" role="player" />
    );

    rerender(
      <ConsequenceSignalPanel campaignId="camp_new" refreshKey="" role="player" />
    );
    expect(await screen.findByText("New campaign weather")).toBeVisible();
    await act(async () => {
      resolveFirst?.({
        run_id: "run_old",
        state_version: null,
        signals: [{
          signal_id: "signal-old",
          title: "Old campaign secret",
          display_mode: "narrative",
          severity: 4,
          label: "Leaked",
          description: "",
          current_value: null,
          source_path: null,
          band_id: null,
          active: true
        }],
        events: []
      });
    });

    expect(screen.queryByText("Old campaign secret")).not.toBeInTheDocument();
    expect(screen.getByText("New campaign weather")).toBeVisible();
  });
});

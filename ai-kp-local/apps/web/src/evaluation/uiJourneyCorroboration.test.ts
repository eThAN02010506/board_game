import { describe, expect, it } from "vitest";

import { UiCorroborationCollector } from "./uiJourneyCorroboration";

describe("UI journey corroboration", () => {
  it("records only typed visible events with monotonic timestamps", () => {
    const collector = new UiCorroborationCollector(
      () => new Date("2026-08-25T01:02:03.000Z")
    );

    collector.record("authoritative_ending_visible", 1);
    collector.record("session_end_visible", 1);
    collector.record("continue_campaign_visible", 1);

    expect(collector.snapshot()).toEqual([
      {
        kind: "authoritative_ending_visible",
        observed_at: "2026-08-25T01:02:03.000Z",
        session_index: 1
      },
      {
        kind: "session_end_visible",
        observed_at: "2026-08-25T01:02:03.001Z",
        session_index: 1
      },
      {
        kind: "continue_campaign_visible",
        observed_at: "2026-08-25T01:02:03.002Z",
        session_index: 1
      }
    ]);
    expect(Object.keys(collector.snapshot()[0]).sort()).toEqual([
      "kind",
      "observed_at",
      "session_index"
    ]);
  });

  it("rejects invalid session indexes", () => {
    const collector = new UiCorroborationCollector();

    expect(() => collector.record("session_end_visible", 0)).toThrow(
      "positive integer"
    );
  });
});

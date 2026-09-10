// @vitest-environment node

import { describe, expect, test } from "vitest";
import { AuthorityRefCollector } from "./authorityEvidence";

describe("AuthorityRefCollector continuity projection", () => {
  test("records episode and snapshot ids from one observed continuity mutation", async () => {
    let responseListener: ((response: unknown) => void) | undefined;
    const page = {
      on: (_event: string, listener: (response: unknown) => void) => {
        responseListener = listener;
      }
    };
    const collector = new AuthorityRefCollector();
    collector.attach(page as never);
    responseListener?.({
      ok: () => true,
      url: () => "http://127.0.0.1:8012/api/campaigns/campaign-1/continuity/end",
      request: () => ({ method: () => "POST" }),
      headers: () => ({ "content-type": "application/json" }),
      json: async () => ({
        current_episode: { id: "episode-10", sequence_no: 10 },
        latest_snapshot: { id: "snapshot-10" }
      })
    });

    const refs = await collector.snapshot();

    expect(refs.episode_ids).toEqual(["episode-10"]);
    expect(refs.continuity_snapshot_ids).toEqual(["snapshot-10"]);
    await expect(collector.latestContinuity()).resolves.toEqual({
      episodeId: "episode-10",
      snapshotId: "snapshot-10"
    });
  });
});

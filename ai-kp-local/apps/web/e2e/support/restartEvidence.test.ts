// @vitest-environment node

import { describe, expect, test } from "vitest";
import { buildProcessRestartEvidence } from "./restartEvidence";

const restart = {
  before: {
    process_instance_id: "process-old",
    os_pid: 401,
    persistent_store_id: "store-1",
    schema_version: 84,
    status: "ready" as const
  },
  after: {
    process_instance_id: "process-new",
    os_pid: 402,
    persistent_store_id: "store-1",
    schema_version: 84,
    status: "ready" as const
  },
  stop: {
    clean: true,
    forced: false,
    alreadyStopped: false,
    exitCode: 0,
    signal: null
  },
  stopped_at: "2026-08-28T00:00:00.001Z",
  started_at: "2026-08-28T00:00:00.002Z"
};

const observedAt = {
  before: "2026-08-28T00:00:00.000Z",
  after: "2026-08-28T00:00:00.003Z"
};

describe("buildProcessRestartEvidence", () => {
  test("projects only safe process facts and the checkpoint locator", () => {
    const evidence = buildProcessRestartEvidence({
      restart,
      checkpointSnapshotId: "snapshot-5",
      observedAt
    });

    expect(evidence.before.process_instance_id).toBe("process-old");
    expect(evidence.after.process_instance_id).toBe("process-new");
    expect(evidence.before.checkpoint_snapshot_id).toBe("snapshot-5");
    expect(evidence.started.health_status).toBe(200);
    expect(JSON.stringify(evidence)).not.toMatch(/api.?key|token|fingerprint/i);
  });

  test("rejects forced shutdown and non-monotonic observations", () => {
    expect(() => buildProcessRestartEvidence({
      restart: { ...restart, stop: { ...restart.stop, clean: false, forced: true } },
      checkpointSnapshotId: "snapshot-5",
      observedAt
    })).toThrow(/graceful/);
    expect(() => buildProcessRestartEvidence({
      restart,
      checkpointSnapshotId: "snapshot-5",
      observedAt: { ...observedAt, after: restart.started_at }
    })).toThrow(/strictly increasing/);
  });
});

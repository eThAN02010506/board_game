import type {
  BackendRestartResult,
  BackendRuntimeIdentity
} from "./backendProcess";

export type ProcessCheckpointObservation = {
  observed_at: string;
  process_instance_id: string;
  os_pid: number;
  persistent_store_id: string;
  checkpoint_snapshot_id: string;
};

export type ProcessRestartEvidence = {
  before: ProcessCheckpointObservation;
  stopped: {
    observed_at: string;
    process_instance_id: string;
    os_pid: number;
    persistent_store_id: string;
    clean_shutdown: boolean;
  };
  started: {
    observed_at: string;
    process_instance_id: string;
    os_pid: number;
    persistent_store_id: string;
    health_status: 200;
  };
  after: ProcessCheckpointObservation;
};

export function buildProcessRestartEvidence(input: {
  restart: BackendRestartResult;
  checkpointSnapshotId: string;
  observedAt: {
    before: string;
    after: string;
  };
}): ProcessRestartEvidence {
  const { before, after, stop } = input.restart;
  requireIdentity(before, "pre-restart");
  requireIdentity(after, "post-restart");
  if (!input.checkpointSnapshotId.trim()) {
    throw new Error("Restart evidence requires one continuity snapshot id");
  }
  if (!stop.clean || stop.forced) {
    throw new Error("Strict restart evidence requires a graceful backend shutdown");
  }
  const ordered = [
    input.observedAt.before,
    input.restart.stopped_at,
    input.restart.started_at,
    input.observedAt.after
  ].map((value) => Date.parse(value));
  if (ordered.some((value) => !Number.isFinite(value))) {
    throw new Error("Restart evidence timestamps must be ISO-8601 instants");
  }
  if (!ordered.every((value, index) => index === 0 || value > ordered[index - 1])) {
    throw new Error("Restart evidence timestamps must be strictly increasing");
  }
  return {
    before: checkpointObservation(
      before,
      input.observedAt.before,
      input.checkpointSnapshotId
    ),
    stopped: {
      observed_at: input.restart.stopped_at,
      process_instance_id: before.process_instance_id,
      os_pid: before.os_pid,
      persistent_store_id: before.persistent_store_id,
      clean_shutdown: true
    },
    started: {
      observed_at: input.restart.started_at,
      process_instance_id: after.process_instance_id,
      os_pid: after.os_pid,
      persistent_store_id: after.persistent_store_id,
      health_status: 200
    },
    after: checkpointObservation(
      after,
      input.observedAt.after,
      input.checkpointSnapshotId
    )
  };
}

function checkpointObservation(
  identity: BackendRuntimeIdentity,
  observedAt: string,
  checkpointSnapshotId: string
): ProcessCheckpointObservation {
  return {
    observed_at: observedAt,
    process_instance_id: identity.process_instance_id,
    os_pid: identity.os_pid,
    persistent_store_id: identity.persistent_store_id,
    checkpoint_snapshot_id: checkpointSnapshotId
  };
}

function requireIdentity(identity: BackendRuntimeIdentity, label: string): void {
  if (!identity.process_instance_id || identity.os_pid < 1 || !identity.persistent_store_id) {
    throw new Error(`Restart evidence has an invalid ${label} runtime identity`);
  }
}

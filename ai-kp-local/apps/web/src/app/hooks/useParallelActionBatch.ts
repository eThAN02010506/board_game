import { useCallback, useEffect, useRef, useState } from "react";

import { getCurrentParallelActionPlayerBatch } from "../../api/client";
import type { AuthIdentity, ParallelActionPlayerBatch } from "../../api/types";
import { useIdentityRequestScope } from "./useIdentityRequestScope";

const ACTIVE_POLL_INTERVAL_MS = 1500;
const IDLE_POLL_INTERVAL_MS = 5000;
const TERMINAL_BATCH_STATUSES = new Set<ParallelActionPlayerBatch["status"]>([
  "settled",
  "superseded"
]);

/** Poll the current member-scoped batch without ever requesting batch authority. */
export function useParallelActionBatch(
  campaignId: string,
  identity: AuthIdentity | null,
  refreshKey = ""
) {
  const { enabled, generation, key: scopeKey, trackerRef } = useIdentityRequestScope(
    campaignId,
    identity,
    identity?.role === "player"
  );
  const [state, setState] = useState<{
    batch: ParallelActionPlayerBatch | null;
    error: string;
    generation: number;
    scopeKey: string;
  }>({ batch: null, error: "", generation, scopeKey });
  const requestVersionRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestedScopeKey = scopeKey;
    const requestedGeneration = generation;
    const requestVersion = ++requestVersionRef.current;
    if (!campaignId || !enabled) {
      setState({
        batch: null,
        error: "",
        generation: requestedGeneration,
        scopeKey: requestedScopeKey
      });
      return null;
    }
    try {
      const next = await getCurrentParallelActionPlayerBatch(campaignId);
      const current = trackerRef.current;
      if (
        requestVersion === requestVersionRef.current
        && current.key === requestedScopeKey
        && current.generation === requestedGeneration
      ) {
        setState({
          batch: next,
          error: "",
          generation: requestedGeneration,
          scopeKey: requestedScopeKey
        });
      }
      return next;
    } catch (cause) {
      const current = trackerRef.current;
      if (
        requestVersion === requestVersionRef.current
        && current.key === requestedScopeKey
        && current.generation === requestedGeneration
      ) {
        setState({
          batch: null,
          error: cause instanceof Error ? cause.message : "多人行动状态暂时不可用",
          generation: requestedGeneration,
          scopeKey: requestedScopeKey
        });
      }
      return null;
    }
  }, [campaignId, enabled, generation, scopeKey, trackerRef]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (refreshKey) void refresh();
  }, [refresh, refreshKey]);

  const isCurrentScope = state.scopeKey === scopeKey && state.generation === generation;
  const batch = isCurrentScope ? state.batch : null;
  const error = isCurrentScope ? state.error : "";
  const isActive = batch !== null && !TERMINAL_BATCH_STATUSES.has(batch.status);
  useEffect(() => {
    if (!campaignId || !enabled) return;
    const timer = window.setInterval(
      () => void refresh(),
      isActive ? ACTIVE_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS
    );
    return () => window.clearInterval(timer);
  }, [campaignId, enabled, isActive, refresh]);

  return { batch, error, refresh };
}

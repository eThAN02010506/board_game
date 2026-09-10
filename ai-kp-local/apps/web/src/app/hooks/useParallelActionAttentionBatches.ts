import { useCallback, useEffect, useRef, useState } from "react";

import { listParallelActionAttentionBatches } from "../../api/client";
import type {
  AuthIdentity,
  ParallelActionAttentionBatch
} from "../../api/types";
import { useIdentityRequestScope } from "./useIdentityRequestScope";

const POLL_INTERVAL_MS = 5000;

/** Poll the recovery-safe KP projection, scoped to the exact authenticated KP. */
export function useParallelActionAttentionBatches(
  campaignId: string,
  identity: AuthIdentity | null,
  refreshKey = ""
) {
  const { enabled, generation, key: scopeKey, trackerRef } = useIdentityRequestScope(
    campaignId,
    identity,
    identity?.role === "kp"
  );
  const [state, setState] = useState<{
    batches: ParallelActionAttentionBatch[];
    error: string;
    generation: number;
    loading: boolean;
    scopeKey: string;
  }>({ batches: [], error: "", generation, loading: false, scopeKey });
  const requestVersionRef = useRef(0);
  const refreshKeyRef = useRef(refreshKey);

  const refresh = useCallback(async (silent = false) => {
    const requestedScopeKey = scopeKey;
    const requestedGeneration = generation;
    const requestVersion = ++requestVersionRef.current;
    if (!campaignId || !enabled) {
      setState({
        batches: [],
        error: "",
        generation: requestedGeneration,
        loading: false,
        scopeKey: requestedScopeKey
      });
      return [];
    }
    if (!silent) {
      setState((current) => ({
        batches: current.scopeKey === requestedScopeKey
          && current.generation === requestedGeneration
          ? current.batches
          : [],
        error: "",
        generation: requestedGeneration,
        loading: true,
        scopeKey: requestedScopeKey
      }));
    }
    try {
      const batches = await listParallelActionAttentionBatches(campaignId);
      const current = trackerRef.current;
      if (
        requestVersion === requestVersionRef.current
        && current.key === requestedScopeKey
        && current.generation === requestedGeneration
      ) {
        setState({
          batches,
          error: "",
          generation: requestedGeneration,
          loading: false,
          scopeKey: requestedScopeKey
        });
      }
      return batches;
    } catch (cause) {
      const current = trackerRef.current;
      if (
        requestVersion === requestVersionRef.current
        && current.key === requestedScopeKey
        && current.generation === requestedGeneration
      ) {
        setState((previous) => ({
          ...previous,
          error: cause instanceof Error ? cause.message : "多人行动恢复状态暂时不可用",
          loading: false
        }));
      }
      return [];
    }
  }, [campaignId, enabled, generation, scopeKey, trackerRef]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (refreshKeyRef.current === refreshKey) return;
    refreshKeyRef.current = refreshKey;
    if (refreshKey) void refresh(true);
  }, [refresh, refreshKey]);

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => void refresh(true), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);

  const isCurrent = state.scopeKey === scopeKey && state.generation === generation;
  return {
    batches: isCurrent ? state.batches : [],
    error: isCurrent ? state.error : "",
    loading: isCurrent && state.loading,
    refresh
  };
}

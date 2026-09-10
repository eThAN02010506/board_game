import { useCallback, useEffect, useRef, useState } from "react";

import { getCurrentParallelActionPlayerRegather } from "../../api/client";
import type { AuthIdentity, ParallelActionPlayerRegather } from "../../api/types";
import { useIdentityRequestScope } from "./useIdentityRequestScope";

const ACTIVE_POLL_INTERVAL_MS = 1500;
const IDLE_POLL_INTERVAL_MS = 5000;

/** Poll only the current member's safe durable regrouping projection. */
export function useParallelActionRegather(
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
    regather: ParallelActionPlayerRegather | null;
    error: string;
    generation: number;
    scopeKey: string;
  }>({ regather: null, error: "", generation, scopeKey });
  const requestVersionRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestedScopeKey = scopeKey;
    const requestedGeneration = generation;
    const requestVersion = ++requestVersionRef.current;
    if (!campaignId || !enabled) {
      setState({
        regather: null,
        error: "",
        generation: requestedGeneration,
        scopeKey: requestedScopeKey
      });
      return null;
    }
    try {
      const next = await getCurrentParallelActionPlayerRegather(campaignId);
      const current = trackerRef.current;
      if (
        requestVersion === requestVersionRef.current
        && current.key === requestedScopeKey
        && current.generation === requestedGeneration
      ) {
        setState({
          regather: next,
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
          regather: null,
          error: cause instanceof Error ? cause.message : "多人重新集结状态暂时不可用",
          generation: requestedGeneration,
          scopeKey: requestedScopeKey
        });
      }
      return null;
    }
  }, [campaignId, enabled, generation, scopeKey, trackerRef]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { if (refreshKey) void refresh(); }, [refresh, refreshKey]);

  const currentScope = state.scopeKey === scopeKey && state.generation === generation;
  const regather = currentScope ? state.regather : null;
  const error = currentScope ? state.error : "";
  useEffect(() => {
    if (!campaignId || !enabled) return;
    const timer = window.setInterval(
      () => void refresh(),
      regather ? ACTIVE_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS
    );
    return () => window.clearInterval(timer);
  }, [campaignId, enabled, refresh, regather]);

  return { regather, error, refresh };
}

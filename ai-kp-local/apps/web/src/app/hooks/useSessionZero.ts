import { useCallback, useEffect, useRef, useState } from "react";

import { getSessionZero } from "../../api/client";
import type { AuthIdentity, SessionZeroView } from "../../api/types";
import { useIdentityRequestScope } from "./useIdentityRequestScope";

export function useSessionZero(campaignId: string, identity: AuthIdentity | null) {
  const { enabled, generation, key: scopeKey, trackerRef } = useIdentityRequestScope(
    campaignId,
    identity
  );
  const [state, setState] = useState<{
    view: SessionZeroView | null;
    error: string;
    loading: boolean;
    generation: number;
    scopeKey: string;
  }>({ view: null, error: "", loading: false, generation, scopeKey });
  const requestVersion = useRef(0);

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    const requestedGeneration = generation;
    const requestedScope = scopeKey;
    if (!enabled) {
      setState({ view: null, error: "", loading: false, generation, scopeKey });
      return null;
    }
    setState((current) => ({ ...current, loading: true, generation, scopeKey }));
    try {
      const view = await getSessionZero(campaignId);
      const current = trackerRef.current;
      if (
        version === requestVersion.current
        && current.key === requestedScope
        && current.generation === requestedGeneration
      ) {
        setState({ view, error: "", loading: false, generation, scopeKey });
      }
      return view;
    } catch (cause) {
      const current = trackerRef.current;
      if (
        version === requestVersion.current
        && current.key === requestedScope
        && current.generation === requestedGeneration
      ) {
        setState({
          view: null,
          error: cause instanceof Error ? cause.message : "Session 0 暂时不可用",
          loading: false,
          generation,
          scopeKey
        });
      }
      return null;
    }
  }, [campaignId, enabled, generation, scopeKey, trackerRef]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);

  const current = state.generation === generation && state.scopeKey === scopeKey;
  return {
    view: current ? state.view : null,
    error: current ? state.error : "",
    loading: current ? state.loading : false,
    refresh
  };
}

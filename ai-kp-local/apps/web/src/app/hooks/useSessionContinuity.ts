import { useCallback, useEffect, useRef, useState } from "react";

import {
  continueCampaign,
  endSessionEpisode,
  getSessionContinuity,
  transitionSessionEpisode
} from "../../api/client";
import type { AuthIdentity, SessionContinuityView } from "../../api/types";
import { useIdentityRequestScope } from "./useIdentityRequestScope";

const POLL_INTERVAL_MS = 5000;

export function useSessionContinuity(
  campaignId: string,
  identity: AuthIdentity | null,
  refreshKey = ""
) {
  const { enabled, generation, key: scopeKey, trackerRef } =
    useIdentityRequestScope(campaignId, identity);
  const [state, setState] = useState<{
    view: SessionContinuityView | null;
    error: string;
    busy: boolean;
    generation: number;
    scopeKey: string;
  }>({ view: null, error: "", busy: false, generation, scopeKey });
  const requestVersionRef = useRef(0);

  const refresh = useCallback(async (silent = false) => {
    const requestVersion = ++requestVersionRef.current;
    const requestedScope = scopeKey;
    const requestedGeneration = generation;
    if (!campaignId || !enabled) {
      setState({ view: null, error: "", busy: false, generation, scopeKey });
      return null;
    }
    if (!silent) {
      setState((current) => ({ ...current, busy: true, error: "", scopeKey, generation }));
    }
    try {
      const view = await getSessionContinuity(campaignId);
      const current = trackerRef.current;
      if (
        requestVersion === requestVersionRef.current
        && current.key === requestedScope
        && current.generation === requestedGeneration
      ) {
        setState({ view, error: "", busy: false, generation, scopeKey });
      }
      return view;
    } catch (cause) {
      const current = trackerRef.current;
      if (current.key === requestedScope && current.generation === requestedGeneration) {
        setState((previous) => ({
          ...previous,
          busy: false,
          error: cause instanceof Error ? cause.message : "连续性状态暂时不可用"
        }));
      }
      return null;
    }
  }, [campaignId, enabled, generation, scopeKey, trackerRef]);

  const act = useCallback(async (
    operation: () => Promise<SessionContinuityView>
  ) => {
    const requestedScope = scopeKey;
    const requestedGeneration = generation;
    setState((current) => ({ ...current, busy: true, error: "" }));
    try {
      const view = await operation();
      const currentScope = trackerRef.current;
      if (
        currentScope.key === requestedScope
        && currentScope.generation === requestedGeneration
      ) {
        setState((current) => ({ ...current, view, busy: false }));
      }
      return view;
    } catch (cause) {
      const currentScope = trackerRef.current;
      if (
        currentScope.key === requestedScope
        && currentScope.generation === requestedGeneration
      ) {
        setState((current) => ({
          ...current,
          busy: false,
          error: cause instanceof Error ? cause.message : "连续性操作失败"
        }));
      }
      return null;
    }
  }, [generation, scopeKey, trackerRef]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => void refresh(true), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);
  useEffect(() => { if (refreshKey) void refresh(true); }, [refresh, refreshKey]);

  const isCurrent = state.scopeKey === scopeKey && state.generation === generation;
  return {
    view: isCurrent ? state.view : null,
    error: isCurrent ? state.error : "",
    busy: isCurrent && state.busy,
    refresh,
    end: () => act(() => endSessionEpisode(campaignId, crypto.randomUUID())),
    continueCampaign: () => act(() => continueCampaign(campaignId, crypto.randomUUID())),
    transition: (target: "paused" | "in_progress", expectedVersion: number) =>
      act(() => transitionSessionEpisode(campaignId, target, expectedVersion))
  };
}

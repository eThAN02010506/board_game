import { useCallback, useEffect, useRef, useState } from "react";

import { requestJson } from "../../api/client";
import type { AuthIdentity, AutoKpJob } from "../../api/types";
import { useIdentityRequestScope } from "./useIdentityRequestScope";

const ACTIVE_JOB_STATUSES = new Set<AutoKpJob["status"]>([
  "queued",
  "running",
  "retry_wait"
]);

export function useAutoKpJobs(campaignId: string, identity: AuthIdentity | null) {
  const { enabled, generation, key: scopeKey, trackerRef } = useIdentityRequestScope(
    campaignId,
    identity
  );
  const [state, setState] = useState<{
    generation: number;
    jobs: AutoKpJob[];
    scopeKey: string;
  }>({ generation, jobs: [], scopeKey });
  const requestVersionRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestedScopeKey = scopeKey;
    const requestedGeneration = generation;
    const requestVersion = ++requestVersionRef.current;
    if (!campaignId || !enabled) {
      setState({
        generation: requestedGeneration,
        jobs: [],
        scopeKey: requestedScopeKey
      });
      return;
    }
    try {
      const next = await requestJson<AutoKpJob[]>(
        `/campaigns/${encodeURIComponent(campaignId)}/auto-kp/jobs?limit=12`
      );
      const current = trackerRef.current;
      if (
        requestVersion === requestVersionRef.current
        && current.key === requestedScopeKey
        && current.generation === requestedGeneration
      ) {
        setState({
          generation: requestedGeneration,
          jobs: next,
          scopeKey: requestedScopeKey
        });
      }
    } catch {
      // Realtime and the normal workspace refresh remain usable if status polling fails.
    }
  }, [campaignId, enabled, generation, scopeKey, trackerRef]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const jobs = state.scopeKey === scopeKey && state.generation === generation
    ? state.jobs
    : [];
  const hasActiveJobs = jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status));
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, refresh]);

  const retry = useCallback(async (jobId: string) => {
    const requestedScopeKey = trackerRef.current.key;
    const requestedGeneration = trackerRef.current.generation;
    const retried = await requestJson<AutoKpJob>(
      `/auto-kp/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST" }
    );
    const currentScope = trackerRef.current;
    if (
      currentScope.key === requestedScopeKey
      && currentScope.generation === requestedGeneration
    ) {
      setState((current) => ({
        generation: requestedGeneration,
        jobs: [
          retried,
          ...(current.scopeKey === requestedScopeKey
            && current.generation === requestedGeneration
            ? current.jobs.filter((job) => job.id !== retried.id)
            : [])
        ],
        scopeKey: requestedScopeKey
      }));
    }
    return retried;
  }, [trackerRef]);

  return { jobs, refresh, retry };
}

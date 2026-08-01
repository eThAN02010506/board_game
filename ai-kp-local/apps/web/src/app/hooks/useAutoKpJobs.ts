import { useCallback, useEffect, useRef, useState } from "react";

import { requestJson } from "../../api/client";
import type { AutoKpJob } from "../../api/types";

const ACTIVE_JOB_STATUSES = new Set<AutoKpJob["status"]>([
  "queued",
  "running",
  "retry_wait"
]);

export function useAutoKpJobs(campaignId: string, enabled: boolean) {
  const [jobs, setJobs] = useState<AutoKpJob[]>([]);
  const scopeRef = useRef({ campaignId, enabled });
  scopeRef.current = { campaignId, enabled };

  const refresh = useCallback(async () => {
    if (!campaignId || !enabled) {
      setJobs([]);
      return;
    }
    try {
      const next = await requestJson<AutoKpJob[]>(
        `/campaigns/${encodeURIComponent(campaignId)}/auto-kp/jobs?limit=12`
      );
      const current = scopeRef.current;
      if (current.campaignId === campaignId && current.enabled === enabled) {
        setJobs(next);
      }
    } catch {
      // Realtime and the normal workspace refresh remain usable if status polling fails.
    }
  }, [campaignId, enabled]);

  useEffect(() => {
    setJobs([]);
    void refresh();
  }, [refresh]);

  const hasActiveJobs = jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status));
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, refresh]);

  return { jobs, refresh };
}

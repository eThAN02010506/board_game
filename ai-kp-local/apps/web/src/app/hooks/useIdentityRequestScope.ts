import { useRef } from "react";

import type { AuthIdentity } from "../../api/types";

type ScopeTracker = {
  generation: number;
  key: string;
};

/**
 * Bind asynchronous player-visible data to the exact authenticated seat.
 *
 * Campaign ids alone are not an identity boundary: two players can use the
 * same campaign in succession. The generation also prevents an A -> B -> A
 * switch from accepting the first A request after credentials have rotated.
 */
export function useIdentityRequestScope(
  campaignId: string,
  identity: AuthIdentity | null,
  allowed = true
) {
  const enabled = Boolean(
    allowed
    && campaignId
    && identity
    && identity.campaign_id === campaignId
  );
  const key = enabled && identity
    ? JSON.stringify([
        campaignId,
        identity.session_id,
        identity.member_id,
        identity.role
      ])
    : "";
  const trackerRef = useRef<ScopeTracker>({ generation: 0, key });

  if (trackerRef.current.key !== key) {
    trackerRef.current = {
      generation: trackerRef.current.generation + 1,
      key
    };
  }

  return {
    enabled,
    generation: trackerRef.current.generation,
    key,
    trackerRef
  };
}

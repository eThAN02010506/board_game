import { useCallback, useEffect, useRef, useState } from "react";

import { listTableMembers, listTableMessages, sendTableMessage } from "../../api/client";
import type {
  AuthIdentity,
  SafeTableMember,
  TableMessage,
  TableMessageAudience
} from "../../api/types";
import { useIdentityRequestScope } from "./useIdentityRequestScope";

const POLL_INTERVAL_MS = 5000;

/** Identity-scoped projection of the immutable communication ledger. */
export function useTableMessages(
  campaignId: string,
  identity: AuthIdentity | null,
  refreshKey = ""
) {
  const { enabled, generation, key: scopeKey, trackerRef } =
    useIdentityRequestScope(campaignId, identity);
  const [state, setState] = useState<{
    messages: TableMessage[];
    members: SafeTableMember[];
    error: string;
    loading: boolean;
    generation: number;
    scopeKey: string;
  }>({ messages: [], members: [], error: "", loading: false, generation, scopeKey });
  const requestVersionRef = useRef(0);

  const refresh = useCallback(async (silent = false) => {
    const requestedScope = scopeKey;
    const requestedGeneration = generation;
    const requestVersion = ++requestVersionRef.current;
    if (!campaignId || !enabled) {
      setState({ messages: [], members: [], error: "", loading: false, generation, scopeKey });
      return;
    }
    if (!silent) {
      setState((current) => ({
        ...current,
        messages: current.scopeKey === requestedScope ? current.messages : [],
        members: current.scopeKey === requestedScope ? current.members : [],
        error: "",
        loading: true,
        generation: requestedGeneration,
        scopeKey: requestedScope
      }));
    }
    try {
      const [messages, members] = await Promise.all([
        listTableMessages(campaignId),
        listTableMembers(campaignId)
      ]);
      const current = trackerRef.current;
      if (
        requestVersion === requestVersionRef.current
        && current.key === requestedScope
        && current.generation === requestedGeneration
      ) {
        setState({
          messages,
          members,
          error: "",
          loading: false,
          generation: requestedGeneration,
          scopeKey: requestedScope
        });
      }
    } catch (cause) {
      const current = trackerRef.current;
      if (current.key === requestedScope && current.generation === requestedGeneration) {
        setState((previous) => ({
          ...previous,
          error: cause instanceof Error ? cause.message : "桌内消息暂时不可用",
          loading: false
        }));
      }
    }
  }, [campaignId, enabled, generation, scopeKey, trackerRef]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => void refresh(true), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);
  useEffect(() => { if (refreshKey) void refresh(true); }, [refresh, refreshKey]);

  const send = useCallback(async (
    audience: TableMessageAudience,
    content: string,
    recipientMemberId: string | null
  ) => {
    if (!campaignId || !enabled) throw new Error("当前身份不能发送桌内消息");
    await sendTableMessage(campaignId, {
      audience,
      content,
      recipient_member_id: recipientMemberId,
      client_message_id: crypto.randomUUID()
    });
    await refresh(true);
  }, [campaignId, enabled, refresh]);

  const current = state.scopeKey === scopeKey && state.generation === generation;
  return {
    messages: current ? state.messages : [],
    members: current ? state.members : [],
    error: current ? state.error : "",
    loading: current && state.loading,
    refresh,
    send
  };
}

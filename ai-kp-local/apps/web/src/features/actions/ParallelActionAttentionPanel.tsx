import { AlertTriangle, RefreshCw, RotateCcw, XCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  abandonParallelActionBatch,
  resumeParallelActionBatch
} from "../../api/client";
import type { AuthIdentity, AutoKpJob } from "../../api/types";
import { useParallelActionAttentionBatches } from "../../app/hooks/useParallelActionAttentionBatches";

type Props = {
  campaignId: string;
  identity: AuthIdentity;
  jobs: AutoKpJob[];
  onChanged: () => void;
};

export function ParallelActionAttentionPanel({
  campaignId,
  identity,
  jobs,
  onChanged
}: Props) {
  const refreshKey = useMemo(
    () => jobs
      .filter((job) => job.job_type === "parallel_actions")
      .map((job) => `${job.id}:${job.status}:${job.updated_at}`)
      .join("|"),
    [jobs]
  );
  const { batches, error, loading, refresh } = useParallelActionAttentionBatches(
    campaignId,
    identity,
    refreshKey
  );
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busyBatchId, setBusyBatchId] = useState("");
  const [message, setMessage] = useState("");
  const [operationError, setOperationError] = useState("");
  const identityScope = JSON.stringify([
    campaignId,
    identity.session_id,
    identity.member_id,
    identity.role
  ]);
  const identityScopeRef = useRef(identityScope);
  identityScopeRef.current = identityScope;

  useEffect(() => {
    setReasons({});
    setBusyBatchId("");
    setMessage("");
    setOperationError("");
  }, [identityScope]);

  if (identity.role !== "kp" || !campaignId) return null;

  async function recover(batchId: string, version: number) {
    const requestedIdentityScope = identityScope;
    const reason = reasons[batchId]?.trim() ?? "";
    if (!reason) return;
    setBusyBatchId(batchId);
    setMessage("");
    setOperationError("");
    try {
      await resumeParallelActionBatch(batchId, {
        expected_version: version,
        reason
      });
      if (identityScopeRef.current !== requestedIdentityScope) return;
      setReasons((current) => ({ ...current, [batchId]: "" }));
      setMessage("已按当前持久化版本恢复整批行动。请等待玩家确认或后续结算。");
      await refresh();
      if (identityScopeRef.current === requestedIdentityScope) onChanged();
    } catch (cause) {
      if (identityScopeRef.current !== requestedIdentityScope) return;
      setOperationError(
        cause instanceof Error ? cause.message : "整批行动恢复失败，请刷新后重试。"
      );
    } finally {
      if (identityScopeRef.current === requestedIdentityScope) {
        setBusyBatchId("");
      }
    }
  }

  async function abandon(batchId: string, version: number) {
    const requestedIdentityScope = identityScope;
    const batch = batches.find((item) => item.id === batchId);
    const reason = reasons[batchId]?.trim() ?? "";
    if (!batch || !batch.abandon_allowed || !reason) return;
    setBusyBatchId(batchId);
    setMessage("");
    setOperationError("");
    try {
      await abandonParallelActionBatch(batchId, {
        expected_version: version,
        reason
      });
      if (identityScopeRef.current !== requestedIdentityScope) return;
      setReasons((current) => ({ ...current, [batchId]: "" }));
      setMessage("整批行动已终止并保留审计记录；玩家现在可以重新提交。");
      await refresh();
      if (identityScopeRef.current === requestedIdentityScope) onChanged();
    } catch (cause) {
      if (identityScopeRef.current !== requestedIdentityScope) return;
      setOperationError(
        cause instanceof Error ? cause.message : "整批行动放弃失败，请刷新后重试。"
      );
    } finally {
      if (identityScopeRef.current === requestedIdentityScope) {
        setBusyBatchId("");
      }
    }
  }

  return (
    <section className="parallel-attention-panel" aria-label="多人行动恢复工作台">
      <header>
        <span>
          <AlertTriangle size={18} />
          <span>
            <strong>多人行动恢复</strong>
            <small>只恢复已冻结的权威；不会修改玩家选择或骰子</small>
          </span>
        </span>
        <button
          className="ghost-button"
          disabled={loading || Boolean(busyBatchId)}
          onClick={() => {
            setMessage("");
            setOperationError("");
            void refresh();
          }}
          type="button"
        >
          <RefreshCw size={14} />刷新
        </button>
      </header>
      {loading && !batches.length ? (
        <p role="status">正在读取需要处理的整批行动……</p>
      ) : batches.length ? (
        <div className="parallel-attention-list">
          {batches.map((batch) => {
            const reason = reasons[batch.id] ?? "";
            const busy = busyBatchId === batch.id;
            const abandonAllowed = batch.abandon_allowed;
            return (
              <article key={batch.id}>
                <div className="parallel-attention-summary">
                  <div>
                    <strong>整批行动需要人工确认</strong>
                    <small>
                      v{batch.version} · {batch.confirmed_count}/{batch.participant_count}
                      人已确认 · {batch.pending_check_count} 项待检定
                    </small>
                  </div>
                  <time dateTime={batch.updated_at}>
                    {new Date(batch.updated_at).toLocaleString()}
                  </time>
                </div>
                <p>{batch.attention_reason || "自动流程已安全暂停，请核对当前权威后处理。"}</p>
                <label>
                  处理原因
                  <textarea
                    disabled={busy}
                    maxLength={2000}
                    onChange={(event) => setReasons((current) => ({
                      ...current,
                      [batch.id]: event.target.value
                    }))}
                    placeholder="记录你核对了什么，以及为何恢复或终止整批"
                    value={reason}
                  />
                </label>
                <div className="parallel-attention-actions">
                  <button
                    className="secondary-button"
                    disabled={busy || !reason.trim()}
                    onClick={() => void recover(batch.id, batch.version)}
                    type="button"
                  >
                    <RotateCcw size={15} />安全恢复
                  </button>
                  <button
                    className="danger-button"
                    disabled={busy || !reason.trim() || !abandonAllowed}
                    onClick={() => void abandon(batch.id, batch.version)}
                    type="button"
                  >
                    <XCircle size={15} />放弃整批
                  </button>
                </div>
                {!abandonAllowed && (
                  <small className="parallel-attention-safety-note">
                    {batch.abandon_block_reason
                      || "服务端权威禁止放弃本批；请恢复并完成既有结算。"}
                  </small>
                )}
              </article>
            );
          })}
        </div>
      ) : (
        <p>当前没有需要 KP 恢复的多人行动。</p>
      )}
      {(operationError || error) && (
        <p className="map-form-errors" role="alert">{operationError || error}</p>
      )}
      {message && <output>{message}</output>}
    </section>
  );
}

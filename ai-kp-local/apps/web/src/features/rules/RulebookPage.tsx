import { BookOpen, Database, FileUp, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { ChangeEvent, useEffect, useRef, useState } from "react";
import {
  listRuleReviewCandidates,
  requestBinary,
  requestJson,
  reviewRuleCandidate
} from "../../api/client";
import type {
  AuthIdentity,
  RuleQueryResult,
  RuleReviewCandidate,
  RuleReviewSubmission,
  RuleSource
} from "../../api/types";
import { statusLabel } from "../../ui/statusLabels";
import { RuntimeContractsPanel } from "./RuntimeContractsPanel";


type Props = {
  identity: AuthIdentity | null;
};

type ReviewDraft = {
  note: string;
  inputsText: string;
  expectedText: string;
};

const emptyReviewDraft: ReviewDraft = {
  note: "",
  inputsText: "{}",
  expectedText: "{}"
};

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label}必须是合法 JSON 对象。`);
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(`${label}必须是 JSON 对象。`);
  }
  return parsed as Record<string, unknown>;
}

export function RulebookPage({ identity }: Props) {
  const identityScope = identity
    ? `${identity.campaign_id}:${identity.session_id}:${identity.member_id}:${identity.role}`
    : "anonymous";
  const identityScopeRef = useRef(identityScope);
  identityScopeRef.current = identityScope;
  const sourceRequestVersion = useRef(0);
  const reviewRequestVersion = useRef(0);
  const queryRequestVersion = useRef(0);
  const [sources, setSources] = useState<RuleSource[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [question, setQuestion] = useState("单次伤害达到最大生命值一半时会发生什么？");
  const [result, setResult] = useState<RuleQueryResult | null>(null);
  const [resultScope, setResultScope] = useState("");
  const [message, setMessage] = useState("正在读取本地规则书库……");
  const [busy, setBusy] = useState(false);
  const [reviewCandidates, setReviewCandidates] = useState<RuleReviewCandidate[]>([]);
  const [reviewDrafts, setReviewDrafts] = useState<Record<string, ReviewDraft>>({});
  const [reviewMessage, setReviewMessage] = useState("正在读取待审核规则候选……");
  const [reviewBusy, setReviewBusy] = useState(false);
  const [queryBusy, setQueryBusy] = useState(false);
  const [queryMessage, setQueryMessage] = useState("");

  const selected = sources.find((source) => source.id === selectedId) ?? sources[0] ?? null;
  const selectedSourceIdRef = useRef(selected?.id ?? "");
  selectedSourceIdRef.current = selected?.id ?? "";
  const canManage = identity?.role === "kp";
  const canReview = canManage;

  async function loadSources() {
    const requestScope = identityScopeRef.current;
    const requestVersion = ++sourceRequestVersion.current;
    setBusy(true);
    try {
      const loaded = await requestJson<RuleSource[]>("/rulebooks/sources");
      if (
        identityScopeRef.current !== requestScope ||
        sourceRequestVersion.current !== requestVersion
      ) return;
      setSources(loaded);
      setSelectedId((current) => current || loaded[0]?.id || "");
      setMessage(loaded.length ? `已读取 ${loaded.length} 份规则书。` : "尚未导入规则书。");
    } catch (error) {
      if (
        identityScopeRef.current !== requestScope ||
        sourceRequestVersion.current !== requestVersion
      ) return;
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (
        identityScopeRef.current === requestScope &&
        sourceRequestVersion.current === requestVersion
      ) {
        setBusy(false);
      }
    }
  }

  useEffect(() => {
    sourceRequestVersion.current += 1;
    reviewRequestVersion.current += 1;
    queryRequestVersion.current += 1;
    setBusy(false);
    setReviewBusy(false);
    setQueryBusy(false);
    setResult(null);
    setResultScope("");
    setQueryMessage("");
    setReviewDrafts({});
    setReviewCandidates([]);
    if (!canManage) {
      setSources([]);
      setSelectedId("");
      return;
    }
    setSources([]);
    setSelectedId("");
    void loadSources();
  }, [canManage, identityScope]);

  useEffect(() => {
    if (!canReview || !selected?.id) {
      reviewRequestVersion.current += 1;
      setReviewCandidates([]);
      setReviewBusy(false);
      return;
    }
    void refreshReviewCandidates(selected.id, true);
  }, [canReview, identityScope, selected?.id]);

  async function refreshReviewCandidates(
    sourceId = selected?.id,
    clearCurrent = false
  ) {
    if (
      !canReview ||
      !sourceId ||
      selectedSourceIdRef.current !== sourceId
    ) return;
    const requestScope = identityScopeRef.current;
    const requestVersion = ++reviewRequestVersion.current;
    if (clearCurrent) setReviewCandidates([]);
    setReviewBusy(true);
    try {
      const loaded = await listRuleReviewCandidates(sourceId);
      if (
        identityScopeRef.current !== requestScope ||
        reviewRequestVersion.current !== requestVersion ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      setReviewCandidates(loaded);
      setReviewMessage(
        loaded.length
          ? `有 ${loaded.length} 条规则候选等待 KP 审核。`
          : "当前规则书没有待审核候选。"
      );
    } catch (error) {
      if (
        identityScopeRef.current !== requestScope ||
        reviewRequestVersion.current !== requestVersion ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      setReviewMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (
        identityScopeRef.current === requestScope &&
        reviewRequestVersion.current === requestVersion &&
        selectedSourceIdRef.current === sourceId
      ) {
        setReviewBusy(false);
      }
    }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const source = await requestBinary<RuleSource>(
        "/rulebooks/sources",
        file,
        "application/pdf"
      );
      setSelectedId(source.id);
      setMessage(`已提取 ${source.page_count} 页、${source.chunk_count} 个原文块。`);
      await loadSources();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      event.target.value = "";
      setBusy(false);
    }
  }

  async function indexSource() {
    if (!selected) return;
    const sourceId = selected.id;
    const requestScope = identityScopeRef.current;
    setBusy(true);
    try {
      const indexed = await requestJson<RuleSource>(
        `/rulebooks/sources/${sourceId}/index`,
        { method: "POST" }
      );
      if (
        identityScopeRef.current !== requestScope ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      setMessage(`MiniRAG 已索引 ${indexed.chunk_count} 个原文块。`);
      await loadSources();
    } catch (error) {
      if (
        identityScopeRef.current !== requestScope ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function extractBatch(retryFailed = false) {
    if (!selected) return;
    const sourceId = selected.id;
    const requestScope = identityScopeRef.current;
    setBusy(true);
    try {
      const run = await requestJson<{
        processed_count: number;
        accepted_count: number;
        rejected_count: number;
      }>(
        `/rulebooks/sources/${sourceId}/extract-rules?limit=5&retry_failed=${retryFailed}`,
        { method: "POST" }
      );
      if (
        identityScopeRef.current !== requestScope ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      setMessage(
        `本批处理 ${run.processed_count} 块；来源校验通过、待 KP 审核 ${run.accepted_count}，隔离或拒绝 ${run.rejected_count}。`
      );
      await loadSources();
      if (selectedSourceIdRef.current === sourceId) {
        await refreshReviewCandidates(sourceId);
      }
    } catch (error) {
      if (
        identityScopeRef.current !== requestScope ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function queryRules() {
    if (!identity || !question.trim()) return;
    const requestScope = identityScopeRef.current;
    const requestVersion = ++queryRequestVersion.current;
    setQueryBusy(true);
    setResult(null);
    setResultScope("");
    setQueryMessage("正在查询原文与规则对象……");
    try {
      const response = await requestJson<RuleQueryResult>("/rules/query", {
        method: "POST",
        body: JSON.stringify({ question: question.trim(), top_k: 8 })
      });
      if (
        identityScopeRef.current !== requestScope ||
        queryRequestVersion.current !== requestVersion
      ) return;
      setResult(response);
      setResultScope(requestScope);
      setQueryMessage(
        `${response.retrieval_backend === "minirag" ? "MiniRAG" : "词法后备"} 返回 ${response.chunks.length} 个原文依据。`
      );
    } catch (error) {
      if (
        identityScopeRef.current !== requestScope ||
        queryRequestVersion.current !== requestVersion
      ) return;
      setQueryMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (
        identityScopeRef.current === requestScope &&
        queryRequestVersion.current === requestVersion
      ) {
        setQueryBusy(false);
      }
    }
  }

  function reviewDraft(candidateId: string): ReviewDraft {
    return reviewDrafts[candidateId] ?? emptyReviewDraft;
  }

  function updateReviewDraft(candidateId: string, changes: Partial<ReviewDraft>) {
    setReviewDrafts((current) => ({
      ...current,
      [candidateId]: {
        ...(current[candidateId] ?? emptyReviewDraft),
        ...changes
      }
    }));
  }

  async function submitReview(
    candidate: RuleReviewCandidate,
    decision: RuleReviewSubmission["decision"]
  ) {
    if (!canReview || !selected) return;
    const requestScope = identityScopeRef.current;
    const requestVersion = ++reviewRequestVersion.current;
    const sourceId = selected.id;
    const draft = reviewDraft(candidate.id);
    let payload: RuleReviewSubmission;
    if (decision === "approved") {
      let inputs: Record<string, unknown>;
      let expectedOutput: Record<string, unknown>;
      try {
        inputs = parseJsonObject(draft.inputsText, "Golden inputs");
        expectedOutput = parseJsonObject(draft.expectedText, "Expected");
      } catch (error) {
        setReviewMessage(error instanceof Error ? error.message : String(error));
        return;
      }
      payload = {
        decision,
        note: draft.note.trim() || null,
        golden_cases: [{
          name: `${candidate.rule_key} KP 审核用例`,
          inputs,
          expected_output: expectedOutput
        }]
      };
    } else {
      if (!draft.note.trim()) {
        setReviewMessage("拒绝规则候选时必须填写审核备注。");
        return;
      }
      payload = {
        decision,
        note: draft.note.trim(),
        golden_cases: []
      };
    }

    setReviewBusy(true);
    try {
      const reviewed = await reviewRuleCandidate(candidate.id, payload);
      if (
        identityScopeRef.current !== requestScope ||
        reviewRequestVersion.current !== requestVersion ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      const loaded = await listRuleReviewCandidates(sourceId);
      if (
        identityScopeRef.current !== requestScope ||
        reviewRequestVersion.current !== requestVersion ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      setReviewCandidates(loaded);
      setReviewDrafts((current) => {
        const next = { ...current };
        delete next[candidate.id];
        return next;
      });
      setReviewMessage(
        reviewed.status === "validated"
          ? `已批准 ${reviewed.rule_key}，Golden 校验通过。`
          : decision === "rejected"
            ? `已拒绝 ${reviewed.rule_key}。`
            : `${reviewed.rule_key} 尚未通过 Golden 校验，仍待审核。`
      );
    } catch (error) {
      if (
        identityScopeRef.current !== requestScope ||
        reviewRequestVersion.current !== requestVersion ||
        selectedSourceIdRef.current !== sourceId
      ) return;
      setReviewMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (
        identityScopeRef.current === requestScope &&
        reviewRequestVersion.current === requestVersion &&
        selectedSourceIdRef.current === sourceId
      ) {
        setReviewBusy(false);
      }
    }
  }

  return (
    <div className="rulebook-page">
      <RuntimeContractsPanel />
      {canManage && (
        <>
          <section className="page-card rulebook-library-card">
            <div className="page-intro">
              <div><p className="eyebrow">双存储规则知识</p><h2>规则书与索引</h2></div>
              <BookOpen size={24} />
            </div>
            <label className="file-drop">
              导入带文本层的 PDF
              <input accept="application/pdf,.pdf" disabled={busy} onChange={upload} type="file" />
              <small>原文和页码保存在 SQLite；MiniRAG 索引是可重建缓存。</small>
            </label>
            <div className="rule-source-list">
              {sources.map((source) => (
                <button
                  className={`record-button ${selected?.id === source.id ? "selected" : ""}`}
                  disabled={reviewBusy}
                  key={source.id}
                  onClick={() => setSelectedId(source.id)}
                  type="button"
                >
                  <strong>{source.title}</strong>
                  <small>
                    {source.page_count} 页 · {source.chunk_count} 块 ·{" "}
                    {statusLabel(source.status)}
                  </small>
                </button>
              ))}
            </div>
            <button className="ghost-button" disabled={busy} onClick={() => void loadSources()} type="button">
              <RefreshCw size={15} />刷新
            </button>
          </section>

          <section className="page-card rulebook-pipeline-card">
            <div className="page-intro">
              <div><p className="eyebrow">MiniRAG + JSON + DSL</p><h2>摄取流水线</h2></div>
              <Database size={24} />
            </div>
            {selected ? (
              <>
                <dl className="rule-source-facts">
                  <div><dt>状态</dt><dd>{statusLabel(selected.status)}</dd></div>
                  <div><dt>原文块</dt><dd>{selected.chunk_count}</dd></div>
                  <div><dt>规则对象</dt><dd>{selected.rule_count}</dd></div>
                  <div><dt>SHA-256</dt><dd title={selected.source_hash}>{selected.source_hash.slice(0, 14)}…</dd></div>
                </dl>
                <div className="button-row">
                  <button className="secondary-button" disabled={busy} onClick={() => void indexSource()} type="button">
                    <Database size={16} />建立/重建索引
                  </button>
                  <button className="primary-button" disabled={busy} onClick={() => void extractBatch()} type="button">
                    <ShieldCheck size={16} />抽取下一批 5 块
                  </button>
                  <button className="ghost-button" disabled={busy} onClick={() => void extractBatch(true)} type="button">
                    <RefreshCw size={15} />重试失败块
                  </button>
                </div>
                <p className="permission-hint">
                  Schema、来源绑定、原文引用与冲突检查通过只会进入待审核；仍须配置本地管理员凭证，并由 KP 提交 Golden Case 且全部通过后，规则对象才可执行。
                </p>
              </>
            ) : <p className="empty-copy"><FileUp size={18} /> 请先导入规则书。</p>}
            <p className="inline-message">{message}</p>
          </section>
        </>
      )}

      {canReview && (
        <section className="page-card rule-review-card">
          <div className="page-intro">
            <div><p className="eyebrow">本地管理员 + KP</p><h2>规则候选审核</h2></div>
            <button
              aria-label="刷新规则候选"
              className="ghost-button"
              disabled={reviewBusy || !selected}
              onClick={() => void refreshReviewCandidates()}
              type="button"
            >
              <RefreshCw size={15} />
            </button>
          </div>
          {!selected ? (
            <p className="empty-copy">请先选择一份规则书。</p>
          ) : reviewCandidates.length ? (
            <div className="rule-review-list">
              {reviewCandidates.map((candidate) => {
                const draft = reviewDraft(candidate.id);
                const citations = candidate.object.citations ?? [];
                const candidateAudience = typeof candidate.object.audience === "string"
                  ? candidate.object.audience
                  : "未明确（仅 KP）";
                const validationLayers = Object.entries(candidate.validation).filter(
                  ([, value]) => value && typeof value === "object" && "passed" in value
                );
                return (
                  <article className="rule-review-candidate" key={candidate.id}>
                    <header>
                      <div>
                        <strong>{candidate.title}</strong>
                        <code>{candidate.rule_key}</code>
                      </div>
                      <span>{statusLabel(candidate.status)}</span>
                    </header>
                    <p className="permission-hint">
                      公开范围：{candidateAudience}。批准会把该范围与当前对象哈希一并锁定；未明确范围不会向玩家开放。
                    </p>
                    {candidate.object.summary && <p>{candidate.object.summary}</p>}
                    <details className="rule-review-object">
                      <summary>查看完整规则候选对象</summary>
                      <pre>{JSON.stringify(candidate.object, null, 2)}</pre>
                    </details>
                    <div className="rule-review-validation" aria-label="来源校验信息">
                      {validationLayers.map(([name, value]) => {
                        const report = value as { passed?: unknown; errors?: unknown };
                        const errors = Array.isArray(report.errors)
                          ? report.errors.map(String)
                          : [];
                        return (
                          <div key={name}>
                            <strong>{name}</strong>
                            <span>{report.passed ? "通过" : "未通过"}</span>
                            {!!errors.length && <small>{errors.join("；")}</small>}
                          </div>
                        );
                      })}
                    </div>
                    <div className="rule-review-citations">
                      {citations.map((citation) => (
                        <blockquote key={`${citation.chunk_id}:${citation.page}:${citation.evidence_text}`}>
                          <small>第 {citation.page} 页 · {citation.chunk_id}</small>
                          {citation.evidence_text}
                        </blockquote>
                      ))}
                    </div>
                    <div className="rule-review-form">
                      <label>
                        审核备注（{candidate.rule_key}）
                        <textarea
                          disabled={reviewBusy}
                          onChange={(event) => updateReviewDraft(candidate.id, {
                            note: event.target.value
                          })}
                          value={draft.note}
                        />
                      </label>
                      <label>
                        Golden inputs JSON（{candidate.rule_key}）
                        <textarea
                          disabled={reviewBusy}
                          onChange={(event) => updateReviewDraft(candidate.id, {
                            inputsText: event.target.value
                          })}
                          spellCheck={false}
                          value={draft.inputsText}
                        />
                      </label>
                      <label>
                        Expected JSON（{candidate.rule_key}）
                        <textarea
                          disabled={reviewBusy}
                          onChange={(event) => updateReviewDraft(candidate.id, {
                            expectedText: event.target.value
                          })}
                          spellCheck={false}
                          value={draft.expectedText}
                        />
                      </label>
                    </div>
                    <div className="button-row">
                      <button
                        className="primary-button"
                        disabled={reviewBusy}
                        onClick={() => void submitReview(candidate, "approved")}
                        type="button"
                      >
                        批准 {candidate.rule_key}
                      </button>
                      <button
                        className="ghost-button"
                        disabled={reviewBusy}
                        onClick={() => void submitReview(candidate, "rejected")}
                        type="button"
                      >
                        拒绝 {candidate.rule_key}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="empty-copy">当前规则书没有待审核候选。</p>
          )}
          <p className="inline-message" role="status">{reviewMessage}</p>
        </section>
      )}

      <section className="page-card rule-query-card">
        <div className="page-intro">
          <div>
            <p className="eyebrow">
              {identity?.role === "kp" ? "可追溯原文召回" : "已审核规则召回"}
            </p>
            <h2>查询规则</h2>
          </div>
          <Search size={24} />
        </div>
        <label>问题<textarea value={question} onChange={(event) => setQuestion(event.target.value)} /></label>
        <button className="primary-button" disabled={queryBusy || !identity} onClick={() => void queryRules()} type="button">
          <Search size={16} />
          {identity?.role === "kp" ? "查询原文与规则对象" : "查询已审核规则"}
        </button>
        {!identity && <p className="permission-hint">先加入团会话；系统会按 KP/玩家身份过滤规则内容。</p>}
        {identity?.role === "player" && (
          <p className="permission-hint">
            玩家不会收到规则书原文，只会看到由 KP 审核且明确标记为公开的规则对象。
          </p>
        )}
        {queryMessage && <p className="inline-message" role="status">{queryMessage}</p>}
        {result && resultScope === identityScope && (
          <div className="rule-query-results">
            {result.chunks.map((chunk) => (
              <details key={chunk.id}>
                <summary>第 {chunk.page_start} 页 · {chunk.section || chunk.chapter || "未命名章节"}</summary>
                <p>{chunk.text}</p>
              </details>
            ))}
            {result.rules.map((rule) => (
              <article className="rule-object-result" key={rule.id}>
                <strong>{rule.title}</strong><small>{rule.rule_key} · {rule.object.execution?.kind}</small>
                <p>{rule.object.summary}</p>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

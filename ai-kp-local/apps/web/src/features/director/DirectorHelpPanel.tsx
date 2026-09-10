import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  History,
  LifeBuoy,
  LoaderCircle,
  MessageSquareText,
  RefreshCw
} from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";

import {
  askDirectorHelp,
  getCurrentModuleRun,
  isApiError,
  listDirectorHelpAudits
} from "../../api/client";
import type { DirectorHelpAdvice, DirectorHelpAuditItem } from "../../api/types";
import { DirectorHelpAdviceCard } from "./DirectorHelpAdviceCard";

type Props = {
  campaignId: string;
};

const directorHelpErrorMessages: Record<string, string> = {
  director_help_in_progress: "已有一个带团帮助请求正在处理中，请等待完成后再试。",
  director_help_capacity_exceeded: "当前带团帮助请求较多，请稍后再试。",
  director_help_audit_unavailable: "无法保存带团帮助记录，因此未返回建议，请稍后重试。",
  conflict: "当前模组或游戏状态已变化，请重新提问。",
  rate_limit_exceeded: "请求过于频繁，请稍后再试。",
  upstream_invalid_response: "AI 返回了无法验证的格式，请重新尝试。",
  upstream_service_error: "AI 服务暂时不可用，请稍后再试。",
  invalid_input: "问题内容不符合要求，请检查后重试。"
};

const unknownDirectorHelpError = "暂时无法获取建议，请稍后重试。";
const unknownDirectorHelpHistoryError = "暂时无法加载历史记录，请稍后重试。";
const historyPageSize = 10;

const historyOutcomeLabels: Record<DirectorHelpAuditItem["outcome"], string> = {
  requested: "处理中",
  completed: "已完成",
  failed: "未完成",
  cancelled_client: "客户端已取消",
  cancelled_control: "状态变化，已取消",
  rejected_busy: "请求未受理"
};

function isAbortError(error: unknown): boolean {
  return Boolean(
    error
    && typeof error === "object"
    && "name" in error
    && error.name === "AbortError"
  );
}

function directorHelpErrorMessage(error: unknown): string {
  if (!isApiError(error) || !error.code) return unknownDirectorHelpError;
  return directorHelpErrorMessages[error.code] ?? unknownDirectorHelpError;
}

function historyOutcomeMessage(item: DirectorHelpAuditItem): string {
  switch (item.outcome) {
    case "requested":
      return "这次请求仍在处理中，稍后刷新可查看结果。";
    case "completed":
      return "这次请求已完成，但没有可显示的建议快照。";
    case "failed":
      return item.error_code
        ? directorHelpErrorMessages[item.error_code] ?? "这次请求未能生成建议。"
        : "这次请求未能生成建议。";
    case "cancelled_client":
      return "这次请求已在客户端取消。";
    case "cancelled_control":
      return "游戏控制状态发生变化，这次请求已取消。";
    case "rejected_busy":
      return "当时已有一个带团帮助请求正在处理中，这次请求未受理。";
  }
}

function formatHistoryTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

export function DirectorHelpPanel({ campaignId }: Props) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [advice, setAdvice] = useState<DirectorHelpAdvice | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyItems, setHistoryItems] = useState<DirectorHelpAuditItem[]>([]);
  const [historyNextBeforeId, setHistoryNextBeforeId] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [expandedHistoryId, setExpandedHistoryId] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const resultRef = useRef<HTMLElement>(null);
  const requestEpoch = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const historyRequestEpoch = useRef(0);
  const activeHistoryRequest = useRef<AbortController | null>(null);
  const contentId = useId();
  const questionId = useId();
  const hintId = useId();
  const historyHeadingId = useId();
  const historyDetailsId = useId();

  const loadHistory = useCallback(async ({
    append = false,
    beforeId
  }: { append?: boolean; beforeId?: string } = {}) => {
    activeHistoryRequest.current?.abort();
    const controller = new AbortController();
    activeHistoryRequest.current = controller;
    const epoch = ++historyRequestEpoch.current;
    setHistoryBusy(true);
    setHistoryError("");

    try {
      const page = await listDirectorHelpAudits(campaignId, {
        limit: historyPageSize,
        beforeId,
        signal: controller.signal
      });
      if (epoch !== historyRequestEpoch.current || controller.signal.aborted) return;

      if (append) {
        setHistoryItems((current) => {
          const seen = new Set(current.map((item) => item.id));
          return current.concat(page.items.filter((item) => {
            if (seen.has(item.id)) return false;
            seen.add(item.id);
            return true;
          }));
        });
      } else {
        setHistoryItems(page.items);
        setExpandedHistoryId((current) => (
          current && page.items.some((item) => item.id === current) ? current : null
        ));
      }
      setHistoryNextBeforeId(page.next_before_id);
    } catch (caught) {
      if (epoch !== historyRequestEpoch.current || isAbortError(caught)) return;
      setHistoryError(unknownDirectorHelpHistoryError);
    } finally {
      if (activeHistoryRequest.current === controller) activeHistoryRequest.current = null;
      if (epoch === historyRequestEpoch.current) setHistoryBusy(false);
    }
  }, [campaignId]);

  useEffect(() => {
    requestEpoch.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    setQuestion("");
    setAdvice(null);
    setError("");
    setBusy(false);
    historyRequestEpoch.current += 1;
    activeHistoryRequest.current?.abort();
    activeHistoryRequest.current = null;
    setHistoryItems([]);
    setHistoryNextBeforeId(null);
    setHistoryError("");
    setHistoryBusy(false);
    setExpandedHistoryId(null);
  }, [campaignId]);

  useEffect(() => () => {
    requestEpoch.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    historyRequestEpoch.current += 1;
    activeHistoryRequest.current?.abort();
    activeHistoryRequest.current = null;
  }, []);

  useEffect(() => {
    if (open) void loadHistory();
  }, [loadHistory, open]);

  useEffect(() => {
    if (open) questionRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (advice) resultRef.current?.focus();
  }, [advice]);

  function closePanel() {
    requestEpoch.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    setBusy(false);
    historyRequestEpoch.current += 1;
    activeHistoryRequest.current?.abort();
    activeHistoryRequest.current = null;
    setHistoryBusy(false);
    setOpen(false);
    triggerRef.current?.focus();
  }

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = question.trim();
    if (!normalized) {
      setError("请输入你想询问的带团问题。");
      questionRef.current?.focus();
      return;
    }

    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    const epoch = ++requestEpoch.current;
    setBusy(true);
    setError("");
    setAdvice(null);
    try {
      const run = await getCurrentModuleRun(campaignId, {
        signal: controller.signal
      });
      if (epoch !== requestEpoch.current || controller.signal.aborted) return;
      if (!run) {
        setError("当前没有正在进行的模组。");
        return;
      }
      const result = await askDirectorHelp(run.id, normalized, {
        signal: controller.signal
      });
      if (epoch !== requestEpoch.current || controller.signal.aborted) return;
      setAdvice(result);
      void loadHistory();
    } catch (caught) {
      if (epoch !== requestEpoch.current || isAbortError(caught)) return;
      setError(directorHelpErrorMessage(caught));
    } finally {
      if (activeRequest.current === controller) activeRequest.current = null;
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  function handleQuestionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  function handlePanelKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closePanel();
    }
  }

  return (
    <section className={`director-help-panel ${open ? "open" : ""}`} aria-label="KP 带团帮助">
      <div className="director-help-launcher">
        <button
          aria-controls={contentId}
          aria-expanded={open}
          className="director-help-trigger"
          onClick={() => open ? closePanel() : setOpen(true)}
          ref={triggerRef}
          type="button"
        >
          <LifeBuoy aria-hidden="true" size={20} />
          <span><strong>需要帮助？</strong><small>询问 AI 现在该怎么带</small></span>
          {open
            ? <ChevronUp aria-hidden="true" size={18} />
            : <ChevronDown aria-hidden="true" size={18} />}
        </button>
      </div>

      {open && (
        <div id={contentId} className="director-help-content" onKeyDown={handlePanelKeyDown}>
          <header>
            <div>
              <p className="eyebrow">Need Help · 新手 KP 助手</p>
              <h2>你现在卡在哪里？</h2>
              <p>建议仅供 KP 参考，不会改变场景、角色、线索或检定状态；模型措辞须先检查剧透。</p>
            </div>
          </header>

          <form aria-busy={busy} onSubmit={(event) => void submitQuestion(event)}>
            <label htmlFor={questionId}>询问一个具体的带团问题</label>
            <textarea
              aria-describedby={hintId}
              disabled={busy}
              id={questionId}
              maxLength={2000}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleQuestionKeyDown}
              placeholder="例如：玩家想烧掉旅馆，我现在该怎么处理？"
              ref={questionRef}
              rows={3}
              value={question}
            />
            <div className="director-help-form-footer">
              <small id={hintId}>按 Ctrl/Command + Enter 获取建议，按 Esc 收起。</small>
              <button className="director-help-submit" disabled={busy} type="submit">
                {busy
                  ? <><LoaderCircle aria-hidden="true" className="spin" size={17} />正在查找依据……</>
                  : <><MessageSquareText aria-hidden="true" size={17} />获取建议</>}
              </button>
            </div>
          </form>

          {error && (
            <div className="director-help-error" role="alert">
              <AlertTriangle aria-hidden="true" size={18} />
              <div><strong>暂时无法给出建议</strong><p>{error}</p></div>
            </div>
          )}

          {busy && (
            <p className="director-help-loading" role="status">
              正在对照当前场景、模组契约和运行状态。
            </p>
          )}

          {advice && <DirectorHelpAdviceCard advice={advice} ref={resultRef} />}

          <section
            aria-busy={historyBusy}
            aria-labelledby={historyHeadingId}
            className="director-help-history"
          >
            <header>
              <div>
                <h3 id={historyHeadingId}><History aria-hidden="true" size={18} />历史建议</h3>
                <p>按提问时间查看当时的只读建议快照。</p>
              </div>
              <button
                aria-label="刷新 Need Help 历史"
                className="director-help-history-refresh"
                disabled={historyBusy}
                onClick={() => void loadHistory()}
                type="button"
              >
                <RefreshCw aria-hidden="true" className={historyBusy ? "spin" : ""} size={16} />
                刷新
              </button>
            </header>

            {historyError && (
              <div className="director-help-history-error" role="alert">
                <AlertTriangle aria-hidden="true" size={17} />
                <div>
                  <strong>历史记录暂时无法加载</strong>
                  <p>{historyError}</p>
                  <button onClick={() => void loadHistory()} type="button">重试加载历史记录</button>
                </div>
              </div>
            )}

            {historyBusy && (
              <p className="director-help-history-status" role="status">
                {historyItems.length ? "正在刷新历史记录……" : "正在加载历史记录……"}
              </p>
            )}

            {!historyBusy && !historyError && historyItems.length === 0 && (
              <p className="director-help-history-empty" role="status">
                还没有 Need Help 历史记录。
              </p>
            )}

            {historyItems.length > 0 && (
              <ol aria-busy={historyBusy} aria-label="Need Help 历史记录">
                {historyItems.map((item, index) => {
                  const expanded = expandedHistoryId === item.id;
                  const detailsId = `${historyDetailsId}-${index}`;
                  return (
                    <li className={`outcome-${item.outcome}`} key={item.id}>
                      <button
                        aria-controls={detailsId}
                        aria-expanded={expanded}
                        className="director-help-history-toggle"
                        onClick={() => setExpandedHistoryId(expanded ? null : item.id)}
                        type="button"
                      >
                        <span>
                          <strong>{item.question}</strong>
                          <small>
                            <time dateTime={item.created_at}>{formatHistoryTime(item.created_at)}</time>
                            <span className="director-help-history-outcome">
                              {historyOutcomeLabels[item.outcome]}
                            </span>
                          </small>
                        </span>
                        {expanded
                          ? <ChevronUp aria-hidden="true" size={17} />
                          : <ChevronDown aria-hidden="true" size={17} />}
                      </button>

                      {expanded && (
                        <div className="director-help-history-details" id={detailsId}>
                          {item.outcome === "completed" && item.advice
                            ? <DirectorHelpAdviceCard advice={item.advice} historical />
                            : (
                              <p role={item.outcome === "requested" ? "status" : undefined}>
                                {historyOutcomeMessage(item)}
                              </p>
                            )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}

            {historyNextBeforeId && (
              <button
                className="director-help-history-more"
                disabled={historyBusy}
                onClick={() => void loadHistory({
                  append: true,
                  beforeId: historyNextBeforeId
                })}
                type="button"
              >
                加载更早的 Need Help 记录
              </button>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

import { BookOpen, Database, FileUp, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { ChangeEvent, useEffect, useState } from "react";
import { requestBinary, requestJson } from "../../api/client";
import type { AuthIdentity, RuleQueryResult, RuleSource } from "../../api/types";


type Props = {
  identity: AuthIdentity | null;
};


export function RulebookPage({ identity }: Props) {
  const [sources, setSources] = useState<RuleSource[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [question, setQuestion] = useState("单次伤害达到最大生命值一半时会发生什么？");
  const [result, setResult] = useState<RuleQueryResult | null>(null);
  const [message, setMessage] = useState("正在读取本地规则书库……");
  const [busy, setBusy] = useState(false);

  const selected = sources.find((source) => source.id === selectedId) ?? sources[0] ?? null;

  async function loadSources() {
    setBusy(true);
    try {
      const loaded = await requestJson<RuleSource[]>("/rulebooks/sources");
      setSources(loaded);
      setSelectedId((current) => current || loaded[0]?.id || "");
      setMessage(loaded.length ? `已读取 ${loaded.length} 份规则书。` : "尚未导入规则书。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void loadSources();
  }, []);

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
    setBusy(true);
    try {
      const indexed = await requestJson<RuleSource>(
        `/rulebooks/sources/${selected.id}/index`,
        { method: "POST" }
      );
      setMessage(`MiniRAG 已索引 ${indexed.chunk_count} 个原文块。`);
      await loadSources();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function extractBatch(retryFailed = false) {
    if (!selected) return;
    setBusy(true);
    try {
      const run = await requestJson<{
        processed_count: number;
        accepted_count: number;
        rejected_count: number;
      }>(
        `/rulebooks/sources/${selected.id}/extract-rules?limit=5&retry_failed=${retryFailed}`,
        { method: "POST" }
      );
      setMessage(
        `本批处理 ${run.processed_count} 块；验证通过 ${run.accepted_count}，隔离或拒绝 ${run.rejected_count}。`
      );
      await loadSources();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function queryRules() {
    if (!identity || !question.trim()) return;
    setBusy(true);
    try {
      const response = await requestJson<RuleQueryResult>("/rules/query", {
        method: "POST",
        body: JSON.stringify({ question: question.trim(), top_k: 8 })
      });
      setResult(response);
      setMessage(
        `${response.retrieval_backend === "minirag" ? "MiniRAG" : "词法后备"} 返回 ${response.chunks.length} 个原文依据。`
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rulebook-page">
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
              key={source.id}
              onClick={() => setSelectedId(source.id)}
              type="button"
            >
              <strong>{source.title}</strong>
              <small>{source.page_count} 页 · {source.chunk_count} 块 · {source.status}</small>
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
              <div><dt>状态</dt><dd>{selected.status}</dd></div>
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
            <p className="permission-hint">只有通过 Schema、原文引用和冲突检查的对象才能被确定性规则引擎执行。</p>
          </>
        ) : <p className="empty-copy"><FileUp size={18} /> 请先导入规则书。</p>}
        <p className="inline-message">{message}</p>
      </section>

      <section className="page-card rule-query-card">
        <div className="page-intro">
          <div><p className="eyebrow">可追溯原文召回</p><h2>查询规则</h2></div>
          <Search size={24} />
        </div>
        <label>问题<textarea value={question} onChange={(event) => setQuestion(event.target.value)} /></label>
        <button className="primary-button" disabled={busy || !identity} onClick={() => void queryRules()} type="button">
          <Search size={16} />查询原文与规则对象
        </button>
        {!identity && <p className="permission-hint">先加入团会话；系统会按 KP/玩家身份过滤规则内容。</p>}
        {result && (
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

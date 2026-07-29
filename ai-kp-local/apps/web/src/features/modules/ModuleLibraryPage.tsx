import {
  BookOpen,
  Check,
  FileImage,
  FileUp,
  RefreshCw,
  RotateCcw,
  Search,
  Shield,
  Sparkles,
  X
} from "lucide-react";
import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

import { requestBinary, requestBlob, requestJson } from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  ModuleAnalysisCapabilities,
  ModuleAsset,
  ModuleChunk,
  ModuleImportJob,
  ModuleKnowledgeCandidate,
  ModuleRecord,
  ModuleSearchResult
} from "../../api/types";
import { ModuleGraphWorkbench } from "./ModuleGraphWorkbench";

type Props = {
  campaign: Campaign | null;
  identity: AuthIdentity | null;
};

const stageLabels: Record<string, string> = {
  queued: "等待解析",
  validating: "校验源文件",
  extracting: "提取图文",
  storing: "保存资产",
  completed: "导入完成",
  failed: "导入失败"
};

function AssetPreview({
  asset,
  busy,
  onAnalyze
}: {
  asset: ModuleAsset;
  busy: boolean;
  onAnalyze: (assetId: string, mode: "tesseract" | "vision") => void;
}) {
  const [source, setSource] = useState("");

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void requestBlob(`/module-assets/${asset.id}/content`)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch(() => setSource(""));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [asset.id]);

  return (
    <article className="module-asset-card">
      {source ? <img alt={asset.nearby_heading ?? "KP 本图片"} src={source} /> : <FileImage size={28} />}
      <div>
        <strong>{asset.nearby_heading ?? "未命名图片"}</strong>
        <small>{asset.source_locator}</small>
        <span>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : asset.mime_type}</span>
        {asset.ocr_text && <p><b>OCR</b> {asset.ocr_text}</p>}
        {asset.visual_summary && <p><b>视觉摘要</b> {asset.visual_summary}</p>}
        {asset.analysis_error && <p className="module-analysis-error">{asset.analysis_error}</p>}
      </div>
      <em>{asset.analysis_status === "pending_analysis" ? "等待 OCR / 视觉摘要" : asset.analysis_status}</em>
      <div className="module-asset-actions">
        <button disabled={busy} onClick={() => onAnalyze(asset.id, "tesseract")} type="button">
          本地 OCR
        </button>
        <button disabled={busy} onClick={() => onAnalyze(asset.id, "vision")} type="button">
          视觉分析
        </button>
      </div>
    </article>
  );
}

export function ModuleLibraryPage({ campaign, identity }: Props) {
  const [jobs, setJobs] = useState<ModuleImportJob[]>([]);
  const [modules, setModules] = useState<ModuleRecord[]>([]);
  const [selectedModuleId, setSelectedModuleId] = useState("");
  const [chunks, setChunks] = useState<ModuleChunk[]>([]);
  const [assets, setAssets] = useState<ModuleAsset[]>([]);
  const [candidates, setCandidates] = useState<ModuleKnowledgeCandidate[]>([]);
  const [capabilities, setCapabilities] = useState<ModuleAnalysisCapabilities | null>(null);
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState<ModuleSearchResult[]>([]);
  const [title, setTitle] = useState("");
  const [message, setMessage] = useState("选择当前团并以 KP 身份进入，即可导入 KP 本。");
  const [busy, setBusy] = useState(false);

  const canManage = Boolean(campaign && identity?.role === "kp" && identity.campaign_id === campaign.id);
  const selectedModule = modules.find((item) => item.id === selectedModuleId) ?? null;
  const hasActiveJob = jobs.some((job) => job.status === "queued" || job.status === "processing");

  async function loadLibrary(silent = false) {
    if (!campaign || !canManage) {
      setJobs([]);
      setModules([]);
      return;
    }
    if (!silent) setBusy(true);
    try {
      const [loadedJobs, loadedModules, loadedCapabilities] = await Promise.all([
        requestJson<ModuleImportJob[]>(`/campaigns/${campaign.id}/module-imports`),
        requestJson<ModuleRecord[]>(`/campaigns/${campaign.id}/modules`),
        requestJson<ModuleAnalysisCapabilities>(
          `/module-analysis/capabilities?campaign_id=${campaign.id}`
        )
      ]);
      setJobs(loadedJobs);
      setModules(loadedModules);
      setCapabilities(loadedCapabilities);
      setSelectedModuleId((current) =>
        loadedModules.some((item) => item.id === current) ? current : loadedModules[0]?.id ?? ""
      );
      if (!silent) {
        setMessage(
          loadedModules.length
            ? `当前团已保存 ${loadedModules.length} 个模组版本。`
            : "当前团尚未导入 KP 本。"
        );
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (!silent) setBusy(false);
    }
  }

  useEffect(() => {
    void loadLibrary();
  }, [campaign?.id, canManage]);

  useEffect(() => {
    if (!hasActiveJob) return;
    const timer = window.setInterval(() => void loadLibrary(true), 1200);
    return () => window.clearInterval(timer);
  }, [hasActiveJob, campaign?.id]);

  useEffect(() => {
    if (!selectedModuleId || !canManage) {
      setChunks([]);
      setAssets([]);
      setCandidates([]);
      return;
    }
    let active = true;
    void Promise.all([
      requestJson<ModuleChunk[]>(`/modules/${selectedModuleId}/chunks?view=kp`),
      requestJson<ModuleAsset[]>(`/modules/${selectedModuleId}/assets`),
      requestJson<ModuleKnowledgeCandidate[]>(
        `/modules/${selectedModuleId}/knowledge/candidates`
      )
    ])
      .then(([loadedChunks, loadedAssets, loadedCandidates]) => {
        if (!active) return;
        setChunks(loadedChunks);
        setAssets(loadedAssets);
        setCandidates(loadedCandidates);
      })
      .catch((error) => {
        if (active) setMessage(error instanceof Error ? error.message : String(error));
      });
    return () => {
      active = false;
    };
  }, [selectedModuleId, canManage]);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !campaign || !canManage) return;
    setBusy(true);
    try {
      const effectiveTitle = title.trim() || file.name.replace(/\.(pdf|docx)$/i, "");
      const job = await requestBinary<ModuleImportJob>(
        `/campaigns/${campaign.id}/module-imports?title=${encodeURIComponent(effectiveTitle)}`,
        file,
        file.type || "application/octet-stream"
      );
      setMessage(`已创建导入任务：${job.source_filename}`);
      setTitle("");
      await loadLibrary(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      event.target.value = "";
      setBusy(false);
    }
  }

  async function retry(jobId: string) {
    setBusy(true);
    try {
      await requestJson(`/module-imports/${jobId}/retry`, { method: "POST" });
      setMessage("已重新排队，正在再次解析源文件。");
      await loadLibrary(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function refreshModule() {
    if (!selectedModuleId) return;
    const [loadedChunks, loadedAssets, loadedCandidates] = await Promise.all([
      requestJson<ModuleChunk[]>(`/modules/${selectedModuleId}/chunks?view=kp`),
      requestJson<ModuleAsset[]>(`/modules/${selectedModuleId}/assets`),
      requestJson<ModuleKnowledgeCandidate[]>(
        `/modules/${selectedModuleId}/knowledge/candidates`
      )
    ]);
    setChunks(loadedChunks);
    setAssets(loadedAssets);
    setCandidates(loadedCandidates);
  }

  async function analyzeAsset(assetId: string, mode: "tesseract" | "vision") {
    setBusy(true);
    try {
      const language = capabilities?.tesseract.languages.includes("chi_sim")
        ? "chi_sim+eng"
        : "eng";
      const asset = await requestJson<ModuleAsset>(`/module-assets/${assetId}/analyze`, {
        method: "POST",
        body: JSON.stringify({ mode, language })
      });
      setAssets((current) => current.map((item) => item.id === asset.id ? asset : item));
      setMessage(asset.analysis_status === "completed" ? "图片分析已保存。" : asset.analysis_error ?? "分析失败。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function extractKnowledge() {
    if (!selectedModuleId) return;
    setBusy(true);
    try {
      const result = await requestJson<{ processed_count: number; accepted_count: number }>(
        `/modules/${selectedModuleId}/knowledge/extract?limit=5`,
        { method: "POST" }
      );
      await refreshModule();
      setMessage(`已处理 ${result.processed_count} 个文本块，生成 ${result.accepted_count} 条待审候选。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function reviewCandidate(
    candidateId: string,
    decision: "approved" | "rejected"
  ) {
    const note = decision === "rejected" ? "KP 审核未通过" : null;
    setBusy(true);
    try {
      await requestJson(`/module-knowledge/${candidateId}/review`, {
        method: "POST",
        body: JSON.stringify({ decision, note })
      });
      await refreshModule();
      setMessage(decision === "approved" ? "候选已批准并进入检索库。" : "候选已拒绝。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function searchModule() {
    if (!selectedModuleId || !searchText.trim()) return;
    try {
      setSearchResults(await requestJson<ModuleSearchResult[]>(
        `/modules/${selectedModuleId}/search?q=${encodeURIComponent(searchText.trim())}`
      ));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function updateSectionScope(
    heading: string,
    event: FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();
    if (!selectedModuleId) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await requestJson(`/modules/${selectedModuleId}/sections`, {
        method: "PATCH",
        body: JSON.stringify({
          title: heading,
          visibility: data.get("visibility"),
          spoiler_tag: data.get("spoiler_tag") || null
        })
      });
      await refreshModule();
      setMessage(`已更新章节“${heading}”的可见范围。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  const groupedChunks = useMemo(() => {
    const groups = new Map<string, ModuleChunk[]>();
    for (const chunk of chunks) {
      const group = groups.get(chunk.title) ?? [];
      group.push(chunk);
      groups.set(chunk.title, group);
    }
    return [...groups.entries()];
  }, [chunks]);

  return (
    <div className="module-library-page">
      <section className="page-card module-import-card">
        <div className="page-intro">
          <div><p className="eyebrow">不可变来源版本</p><h2>导入 KP 本</h2></div>
          <FileUp size={24} />
        </div>
        <label>
          模组标题
          <input
            disabled={!canManage || busy}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="留空则使用文件名"
            value={title}
          />
        </label>
        <label className="file-drop">
          选择 PDF 或 DOCX
          <input
            accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx"
            disabled={!canManage || busy}
            onChange={upload}
            type="file"
          />
          <small>最多 64 MiB；旧式 .doc 请先另存为 .docx。图片默认仅 KP 可见。</small>
        </label>
        {!canManage && (
          <p className="permission-hint">请先在“团与权限”页面选择团，并以该团 KP 身份进入。</p>
        )}
        <p className="inline-message">{message}</p>
      </section>

      <section className="page-card module-job-card">
        <div className="page-intro">
          <div><p className="eyebrow">持久任务</p><h2>解析与重试</h2></div>
          <RefreshCw size={24} />
        </div>
        <div className="module-job-list">
          {jobs.map((job) => {
            const total = Math.max(job.progress_total, 1);
            const percentage = job.status === "completed"
              ? 100
              : Math.round((job.progress_current / total) * 100);
            return (
              <article className={`module-job ${job.status}`} key={job.id}>
                <div>
                  <strong>{job.title}</strong>
                  <span>{stageLabels[job.stage] ?? job.stage} · 第 {job.attempt_count} 次</span>
                </div>
                <progress max={100} value={percentage} />
                <small>{job.source_filename}</small>
                {job.error_text && <p>{job.error_text}</p>}
                {job.status === "failed" && (
                  <button className="ghost-button" disabled={busy} onClick={() => void retry(job.id)} type="button">
                    <RotateCcw size={14} />重试
                  </button>
                )}
              </article>
            );
          })}
          {!jobs.length && <p className="empty-copy">还没有导入任务。</p>}
        </div>
      </section>

      <section className="page-card module-source-card">
        <div className="page-intro">
          <div><p className="eyebrow">图文证据</p><h2>模组版本</h2></div>
          <Shield size={24} />
        </div>
        <div className="module-source-list">
          {modules.map((item) => (
            <button
              className={`record-button ${selectedModuleId === item.id ? "selected" : ""}`}
              key={item.id}
              onClick={() => setSelectedModuleId(item.id)}
              type="button"
            >
              <strong>{item.title}</strong>
              <small>{item.source_filename ?? item.source_type} · {item.parser_version ?? "内部文本"}</small>
            </button>
          ))}
        </div>
        {selectedModule && (
          <dl className="rule-source-facts">
            <div><dt>文本块</dt><dd>{chunks.length}</dd></div>
            <div><dt>图片</dt><dd>{assets.length}</dd></div>
            <div><dt>来源</dt><dd>{selectedModule.source_type.toUpperCase()}</dd></div>
            <div><dt>哈希</dt><dd title={selectedModule.source_hash ?? ""}>{selectedModule.source_hash?.slice(0, 14) ?? "—"}…</dd></div>
          </dl>
        )}
      </section>

      <section className="page-card module-reader-card">
        <div className="page-intro">
          <div><p className="eyebrow">页码与段落可追溯</p><h2>章节预览</h2></div>
          <BookOpen size={24} />
        </div>
        <div className="module-reader">
          {groupedChunks.map(([heading, headingChunks]) => (
            <details key={`${heading}-${headingChunks[0]?.id}`} open={groupedChunks.length <= 3}>
              <summary>{heading} <small>{headingChunks.length} 块</small></summary>
              <form
                className="module-section-scope"
                onSubmit={(event) => void updateSectionScope(heading, event)}
              >
                <label>
                  可见性
                  <select defaultValue={headingChunks[0]?.visibility ?? "kp"} name="visibility">
                    <option value="player">玩家</option>
                    <option value="table">同桌</option>
                    <option value="kp">KP</option>
                    <option value="secret">绝密</option>
                  </select>
                </label>
                <label>
                  剧透标签
                  <input
                    defaultValue={headingChunks[0]?.spoiler_tag ?? ""}
                    name="spoiler_tag"
                    placeholder="如 act-2；留空表示立即可见"
                  />
                </label>
                <button disabled={busy} type="submit">保存范围</button>
              </form>
              {headingChunks.map((chunk) => (
                <article key={chunk.id}>
                  <small>{chunk.source_locator ?? "内部文本"} · {chunk.content_kind}</small>
                  <p>{chunk.text}</p>
                </article>
              ))}
            </details>
          ))}
          {!chunks.length && <p className="empty-copy">选择已完成的模组后在这里预览正文。</p>}
        </div>
      </section>

      <section className="page-card module-knowledge-card">
        <div className="page-intro">
          <div><p className="eyebrow">证据约束与人工审批</p><h2>模组知识工作台</h2></div>
          <Sparkles size={24} />
        </div>
        <div className="module-toolbar">
          <input
            onChange={(event) => setSearchText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void searchModule();
            }}
            placeholder="检索原文、图片 OCR 与已批准知识"
            value={searchText}
          />
          <button disabled={!selectedModuleId || busy} onClick={() => void searchModule()} type="button">
            <Search size={14} />检索
          </button>
          <button disabled={!selectedModuleId || busy} onClick={() => void extractKnowledge()} type="button">
            <Sparkles size={14} />提取 5 块
          </button>
        </div>
        <small>
          Tesseract：
          {capabilities?.tesseract?.available
            ? `${capabilities.tesseract.version}（${capabilities.tesseract.languages.join(", ")}）`
            : "不可用"}
          {" · "}视觉模型：{capabilities?.vision?.model || "未配置"}
        </small>
        <div className="module-search-results">
          {searchResults.map((result) => (
            <article key={`${result.source_type}-${result.source_id}`}>
              <strong>{result.title}</strong>
              <small>{result.source_type} · {result.source_locator}</small>
              <p>{result.text}</p>
            </article>
          ))}
        </div>
        <div className="module-candidate-list">
          {candidates.map((candidate) => (
            <article className={candidate.status} key={candidate.id}>
              <div><strong>{candidate.title}</strong><em>{candidate.status}</em></div>
              <p>{candidate.statement}</p>
              {candidate.citations.map((citation, index) => (
                <blockquote key={`${candidate.id}-${index}`}>
                  {citation.evidence_text}<small>{citation.source_locator}</small>
                </blockquote>
              ))}
              {candidate.status === "pending" && (
                <div className="module-review-actions">
                  <button disabled={busy} onClick={() => void reviewCandidate(candidate.id, "approved")} type="button">
                    <Check size={14} />批准
                  </button>
                  <button disabled={busy} onClick={() => void reviewCandidate(candidate.id, "rejected")} type="button">
                    <X size={14} />拒绝
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      </section>

      <section className="page-card module-assets-card">
        <div className="page-intro">
          <div><p className="eyebrow">原图永久保留</p><h2>照片、地图与扫描线索</h2></div>
          <FileImage size={24} />
        </div>
        <div className="module-asset-grid">
          {assets.map((asset) => (
            <AssetPreview
              asset={asset}
              busy={busy}
              key={asset.id}
              onAnalyze={(assetId, mode) => void analyzeAsset(assetId, mode)}
            />
          ))}
          {!assets.length && <p className="empty-copy">当前版本没有提取到内嵌图片。</p>}
        </div>
      </section>
      <ModuleGraphWorkbench
        candidates={candidates}
        moduleId={selectedModuleId}
        onMessage={setMessage}
      />
    </div>
  );
}

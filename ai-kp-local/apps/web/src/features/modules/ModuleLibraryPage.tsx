import {
  BookOpen,
  FileImage,
  FileUp,
  RefreshCw,
  RotateCcw,
  Shield
} from "lucide-react";
import { ChangeEvent, useEffect, useMemo, useState } from "react";

import { requestBinary, requestBlob, requestJson } from "../../api/client";
import type {
  AuthIdentity,
  Campaign,
  ModuleAsset,
  ModuleChunk,
  ModuleImportJob,
  ModuleRecord
} from "../../api/types";

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

function AssetPreview({ asset }: { asset: ModuleAsset }) {
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
      </div>
      <em>{asset.analysis_status === "pending_analysis" ? "等待 OCR / 视觉摘要" : asset.analysis_status}</em>
    </article>
  );
}

export function ModuleLibraryPage({ campaign, identity }: Props) {
  const [jobs, setJobs] = useState<ModuleImportJob[]>([]);
  const [modules, setModules] = useState<ModuleRecord[]>([]);
  const [selectedModuleId, setSelectedModuleId] = useState("");
  const [chunks, setChunks] = useState<ModuleChunk[]>([]);
  const [assets, setAssets] = useState<ModuleAsset[]>([]);
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
      const [loadedJobs, loadedModules] = await Promise.all([
        requestJson<ModuleImportJob[]>(`/campaigns/${campaign.id}/module-imports`),
        requestJson<ModuleRecord[]>(`/campaigns/${campaign.id}/modules`)
      ]);
      setJobs(loadedJobs);
      setModules(loadedModules);
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
      return;
    }
    let active = true;
    void Promise.all([
      requestJson<ModuleChunk[]>(`/modules/${selectedModuleId}/chunks?view=kp`),
      requestJson<ModuleAsset[]>(`/modules/${selectedModuleId}/assets`)
    ])
      .then(([loadedChunks, loadedAssets]) => {
        if (!active) return;
        setChunks(loadedChunks);
        setAssets(loadedAssets);
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

      <section className="page-card module-assets-card">
        <div className="page-intro">
          <div><p className="eyebrow">原图永久保留</p><h2>照片、地图与扫描线索</h2></div>
          <FileImage size={24} />
        </div>
        <div className="module-asset-grid">
          {assets.map((asset) => <AssetPreview asset={asset} key={asset.id} />)}
          {!assets.length && <p className="empty-copy">当前版本没有提取到内嵌图片。</p>}
        </div>
      </section>
    </div>
  );
}

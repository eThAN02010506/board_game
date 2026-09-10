import { Braces, Link2, RefreshCw, Send, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ScenarioEntityIdentityEditor } from "./ScenarioEntityIdentityEditor";

import {
  bindScenarioContract,
  compileScenarioContract,
  generateScenarioContract,
  getScenarioContractBinding,
  listScenarioContractJobs,
  listScenarioContracts,
  listScenarioSourceScopes,
  publishScenarioContract,
  retryScenarioContractJob
} from "../../api/client";
import type {
  ModuleRun,
  ScenarioContractBinding,
  ScenarioContractIssue,
  ScenarioContractJob,
  ScenarioSourceScope,
  ScenarioContractValidationReport,
  ScenarioContractVersion
} from "../../api/types";

type Props = {
  run: ModuleRun;
};

type ContractReleasePresentation = {
  ready: boolean;
  label: string;
  blockedReason?: string;
};

type CoverageSupplementProgress = {
  cycle?: number;
  target_count: number;
  partition_count: number;
  completed_partition_count: number;
  failed_partition_count: number;
};

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : null;
}

function coverageSupplementLabel(
  summary: CoverageSupplementProgress,
  prefix: string
): string {
  const targetCount = nonNegativeInteger(summary.target_count);
  const partitionCount = nonNegativeInteger(summary.partition_count);
  const completedCount = nonNegativeInteger(summary.completed_partition_count);
  const failedCount = nonNegativeInteger(summary.failed_partition_count);
  const targetLabel = targetCount === null ? "目标统计不可用" : `${targetCount} 项目标`;

  if (
    partitionCount !== null &&
    completedCount !== null &&
    failedCount !== null &&
    completedCount + failedCount === partitionCount
  ) {
    return (
      `${prefix} ${completedCount}/${partitionCount} 分区 · ${targetLabel}` +
      (failedCount > 0 ? ` · ${failedCount} 失败` : "")
    );
  }

  const details = [
    partitionCount === null ? "分区计划统计不可用" : `${partitionCount} 分区计划`,
    targetLabel,
    completedCount === null
      ? "成功统计无效"
      : `已报告成功批次 ${completedCount}`,
    failedCount === null
      ? "失败统计无效"
      : `已报告失败批次 ${failedCount}`
  ];
  return `${prefix}：${details.join(" · ")}`;
}

function hasCoverageSupplementActivity(summary: CoverageSupplementProgress): boolean {
  return [
    summary.target_count,
    summary.partition_count,
    summary.completed_partition_count,
    summary.failed_partition_count
  ].some((value) => value !== 0);
}

function contractReleasePresentation(
  validation: ScenarioContractValidationReport
): ContractReleasePresentation {
  const provenanceReady = validation.provenance_ready === true;
  const playabilityReady = validation.playability.ready === true;
  const ready =
    validation.valid === true &&
    validation.release_ready === true &&
    provenanceReady &&
    playabilityReady;

  if (ready) {
    return {
      ready: true,
      label: `来源与可玩性证明通过 · ${validation.playability.explored_state_count} 个状态`
    };
  }
  if (!validation.valid) {
    return {
      ready: false,
      label: "结构校验未通过",
      blockedReason: "结构校验尚未通过"
    };
  }
  if (!provenanceReady && !playabilityReady) {
    return {
      ready: false,
      label: "仅结构合法 · 来源绑定与因果证明均未达到发布条件",
      blockedReason: "存在未绑定来源的可执行记录；六项因果可玩性证明尚未全部通过"
    };
  }
  if (!provenanceReady) {
    return {
      ready: false,
      label: "仅结构合法 · 存在未绑定来源的可执行记录",
      blockedReason: "存在未绑定来源的可执行记录"
    };
  }
  if (!playabilityReady) {
    return {
      ready: false,
      label: "仅结构合法 · 因果证明未达到发布条件",
      blockedReason: "六项因果可玩性证明尚未全部通过"
    };
  }
  return {
    ready: false,
    label: "仅结构合法 · 综合发布校验未达到发布条件",
    blockedReason: "契约尚未达到发布条件"
  };
}

export function ScenarioContractPanel({ run }: Props) {
  const [versions, setVersions] = useState<ScenarioContractVersion[]>([]);
  const [binding, setBinding] = useState<ScenarioContractBinding | null>(null);
  const [manualJson, setManualJson] = useState("");
  const [issues, setIssues] = useState<ScenarioContractIssue[]>([]);
  const [jobs, setJobs] = useState<ScenarioContractJob[]>([]);
  const [sourceScopes, setSourceScopes] = useState<ScenarioSourceScope[]>([]);
  const [sourceScopeKey, setSourceScopeKey] = useState("");
  const [message, setMessage] = useState("正在读取可执行契约……");
  const [busy, setBusy] = useState(false);

  const published = useMemo(
    () =>
      versions.find(
        (item) =>
          item.status === "published" &&
          contractReleasePresentation(item.validation).ready
      ) ?? null,
    [versions]
  );
  const currentVersionId = binding?.contract_version_id ?? published?.id ?? versions[0]?.id;

  function jobStatus(job: ScenarioContractJob) {
    if (job.status === "failed") return "失败 · 后台契约编译失败";
    if (job.status !== "succeeded") return `${job.status} · ${job.stage}`;
    if (job.stage === "review_rejected") return "完成 · 自动审核未通过";
    if (job.stage === "completed_draft") return "完成 · 草稿待人工接管";
    if (job.stage === "completed_invalid") return "完成 · 未形成有效契约";
    if (job.stage === "completed" && job.result?.auto_published) return "完成 · 已自动审核发布";
    return `${job.status} · ${job.stage}`;
  }

  async function load() {
    // A Full-AI worker publishes and binds in one transaction. Read its job
    // state before the binding so a terminal job cannot be paired with the
    // pre-commit null binding returned by an earlier concurrent GET.
    const [nextVersions, nextJobs, nextScopes] = await Promise.all([
      listScenarioContracts(run.module_id),
      listScenarioContractJobs(run.module_id),
      listScenarioSourceScopes(run.module_id)
    ]);
    const nextBinding = await getScenarioContractBinding(run.id);
    setVersions(nextVersions);
    setBinding(nextBinding);
    setJobs(nextJobs);
    setSourceScopes(nextScopes);
    setSourceScopeKey((current) => {
      if (nextScopes.some((scope) => scope.key === current)) return current;
      return nextScopes.length === 1 ? nextScopes[0].key : "";
    });
    const latestJob = nextJobs[0];
    if (latestJob?.status === "succeeded" && latestJob.result) {
      setIssues(latestJob.result.compilation?.report.issues ?? []);
    }
    if (latestJob?.status === "failed") {
      setMessage(`后台契约编译失败：${latestJob.last_error ?? "未知错误"}`);
    } else if (latestJob && ["queued", "running", "retry_wait"].includes(latestJob.status)) {
      setMessage(
        `后台契约编译 ${latestJob.stage}：${latestJob.progress_current}/${latestJob.progress_total} 分区。`
      );
    } else if (latestJob?.result?.authoring.review?.decision === "reject") {
      setMessage(
        `独立 AI 审核未通过，契约仅保留为草稿：${latestJob.result.authoring.review.findings.join(" · ")}`
      );
    } else if (nextBinding) {
      setMessage("当前运行已绑定可执行契约，玩家行动会进入确定性 Kernel。");
    } else if (latestJob?.status === "succeeded" && !latestJob.result?.version) {
      setMessage(
        `模型输出未通过契约校验：${latestJob.result?.authoring.validation_errors.join(" · ") ?? "未生成有效候选"}`
      );
    } else if (latestJob?.result?.corpus_truncated) {
      setMessage("契约草稿已保存；来源窗口不完整，必须人工复核后发布。");
    } else if (nextVersions.length) {
      setMessage("已有契约版本，但当前运行尚未绑定。");
    } else {
      setMessage("当前模组还没有可执行契约；未绑定时行动会走兼容裁定流程。");
    }
  }

  useEffect(() => {
    void load().catch((error) => {
      setMessage(error instanceof Error ? error.message : String(error));
    });
  }, [run.id, run.module_id]);

  const activeJob = jobs.find((job) =>
    ["queued", "running", "retry_wait"].includes(job.status)
  );

  useEffect(() => {
    if (!activeJob) return;
    const timer = window.setInterval(() => {
      void load().catch((error) => {
        setMessage(error instanceof Error ? error.message : String(error));
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [activeJob?.id]);

  async function generate() {
    setBusy(true);
    setIssues([]);
    try {
      const job = await generateScenarioContract(
        run.module_id,
        "coc7",
        sourceScopeKey || undefined
      );
      setMessage(`契约编译已进入后台队列（0/${job.progress_total} 分区）。`);
      // The worker may complete a resumed semantic attempt before this POST
      // returns.  Let the authoritative reload win over the optimistic queue
      // message so an immediate terminal-unbound result re-enables generation
      // instead of looking like a job that is still waiting to start.
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function retry(job: ScenarioContractJob) {
    setBusy(true);
    try {
      await retryScenarioContractJob(job.id);
      await load();
      setMessage("失败任务已重新排队；已完成的分区不会重复生成。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function compileManual() {
    setBusy(true);
    setIssues([]);
    try {
      const payload = JSON.parse(manualJson) as Record<string, unknown>;
      const result = await compileScenarioContract(run.module_id, payload);
      setIssues(result.compilation.report.issues);
      await load();
      setMessage(
        result.version
          ? "人工契约通过确定性编译并保存为草稿。"
          : "人工契约未通过编译；没有写入版本。"
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function publish(version: ScenarioContractVersion) {
    setBusy(true);
    try {
      await publishScenarioContract(version.id, version.row_version);
      await load();
      setMessage("契约已发布；可将它绑定到当前运行。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function bind() {
    if (!published) return;
    setBusy(true);
    try {
      const next = await bindScenarioContract(run.id, published.id);
      setBinding(next);
      setMessage("当前运行已绑定该不可变契约版本。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="scenario-contract-panel" aria-label="可执行场景契约">
      <header>
        <div className="director-card-title">
          <Braces size={18} />
          <div>
            <strong>可执行 ScenarioContract</strong>
            <small>来源证据 → 严格编译 → 发布 → 当前运行绑定</small>
          </div>
        </div>
        <span className={binding ? "status-ready" : "status-pending"}>
          {binding ? "Kernel 已启用" : "尚未绑定"}
        </span>
      </header>

      {sourceScopes.length > 1 && !binding && (
        <label className="scenario-source-scope">
          <span>本次要玩的剧本范围</span>
          <select
            aria-label="本次要玩的剧本范围"
            disabled={busy || !!activeJob}
            onChange={(event) => setSourceScopeKey(event.target.value)}
            value={sourceScopeKey}
          >
            <option value="">请选择一个章节范围</option>
            {sourceScopes.map((scope) => (
              <option key={scope.key} value={scope.key}>
                {scope.whole_document ? `整本文档：${scope.title}` : scope.title}
                {`（${scope.block_count} 块${scope.first_page ? `，第 ${scope.first_page}-${scope.last_page} 页` : ""}）`}
              </option>
            ))}
          </select>
          <small>
            检测到多个顶层章节。选择范围可防止合集中的独立故事被合并；
            “整本文档”仅适合确认整本就是一个剧本时使用。
          </small>
        </label>
      )}

      <div className="button-row">
        <button
          disabled={busy || !!binding || !!activeJob || !sourceScopeKey}
          onClick={() => void generate()}
          type="button"
        >
          <Sparkles size={15} />从模组证据生成
        </button>
        <button
          disabled={busy || !!binding || !published}
          onClick={() => void bind()}
          type="button"
        >
          <Link2 size={15} />绑定已发布版本
        </button>
      </div>

      {!!jobs.length && (
        <div className="scenario-contract-jobs" aria-label="契约后台任务">
          {jobs.slice(0, 3).map((job) => (
            <article key={job.id}>
              <div>
                <strong>{jobStatus(job)}</strong>
                <small>{job.progress_current}/{job.progress_total} 分区 · 第 {job.attempt_count}/{job.max_attempts} 次任务尝试</small>
                {job.last_error && <small className="scenario-contract-version-issue">{job.last_error}</small>}
                {job.result?.compilation?.coverage && (
                  <small>
                    来源义务覆盖 {job.result.compilation.coverage.covered_item_count}/
                    {job.result.compilation.coverage.required_item_count}
                    {job.result.compilation.coverage.blocking_item_count !== undefined &&
                      ` · 硬约束 ${job.result.compilation.coverage.covered_blocking_item_count}/${job.result.compilation.coverage.blocking_item_count}`}
                  </small>
                )}
                {!!job.result?.authoring?.check_mappings?.length && (
                  <small>
                    检定词映射 {job.result.authoring.check_mappings.filter((item) => item.status === "resolved").length}/
                    {job.result.authoring.check_mappings.length}
                  </small>
                )}
                {!!job.result?.authoring?.normalizations?.length && (
                  <small>
                    确定性安全规范化 {job.result.authoring.normalizations.length} 项
                  </small>
                )}
                {job.result?.authoring?.review?.decision === "approve" && (
                  <small>
                    独立 AI 审核通过
                    {job.result.authoring.review.assumptions_resolved && " · 已确认全部组装假设"}
                  </small>
                )}
                {!!job.result?.authoring?.repair_diagnostics?.length && (
                  <small>
                    定向记录修复：应用 {job.result.authoring.repair_diagnostics.filter((item) => item.status === "applied").length}
                    {' · '}丢弃 {job.result.authoring.repair_diagnostics.filter((item) => item.status === "discarded").length}
                    {' · '}失败 {job.result.authoring.repair_diagnostics.filter((item) => item.status === "failed").length}
                  </small>
                )}
                {job.result?.authoring?.coverage_supplement &&
                  hasCoverageSupplementActivity(
                    job.result.authoring.coverage_supplement
                  ) && (
                  <small>
                    {coverageSupplementLabel(
                      job.result.authoring.coverage_supplement,
                      "来源义务补写"
                    )}
                  </small>
                )}
                {job.result?.authoring?.post_review_coverage?.map((summary, index) => (
                  <small key={`coverage-cycle-${summary.cycle}-${index}`}>
                    {coverageSupplementLabel(
                      summary,
                      `复审补写周期 ${summary.cycle}`
                    )}
                  </small>
                ))}
                {job.status === "succeeded" && !job.result?.version && (
                  <small>模型输出未形成有效契约；可查看编译问题后重新生成。</small>
                )}
              </div>
              {job.status === "failed" && job.retryable && (
                <button disabled={busy} onClick={() => void retry(job)} type="button">
                  <RefreshCw size={14} />重试
                </button>
              )}
            </article>
          ))}
        </div>
      )}

      {versions.length ? (
        <div className="scenario-contract-versions">
          {versions.map((version) => {
            const isCurrent = version.id === currentVersionId;
            const issueSummary = summarizeIssues(version.validation.issues);
            const release = contractReleasePresentation(version.validation);
            return (
            <article key={version.id}>
              <div>
                <span>{version.status} · v{version.version}</span>
                <strong>{version.contract.title}</strong>
                <small>
                  {version.contract.operators.length} 行动 · {version.contract.task_methods.length} 计划 · {version.contract.endings.length} 结局
                </small>
                <small title={version.contract_hash}>{version.contract_hash.slice(0, 16)}</small>
                <small className={release.ready ? "status-ready" : "status-pending"}>
                  {release.label}
                </small>
                {!!version.validation.playability.proofs.length && (
                  <details className="scenario-contract-issues">
                    <summary>六项因果可玩性证明</summary>
                    <ul>{version.validation.playability.proofs.map((proof) => (
                      <li key={proof.invariant}>
                        <strong>{proof.status} · {proof.invariant}</strong>
                        <span>
                          {(proof.status === "passed" ? proof.witness : proof.counterexamples).join(" · ")}
                        </span>
                      </li>
                    ))}</ul>
                  </details>
                )}
                {isCurrent && version.validation.issues.length > 0 && (
                  <details className="scenario-contract-issues">
                    <summary>
                      当前版本风险：{issueSummary.errors} 错误 · {issueSummary.warnings} 警告 · {issueSummary.categories} 类
                    </summary>
                    <ul>{version.validation.issues.map((issue, index) => (
                      <li key={`${issue.code}:${index}`}>
                        <strong>{issue.severity} · {issue.code}</strong>
                        <span>{issue.message}</span>
                      </li>
                    ))}</ul>
                  </details>
                )}
                {!isCurrent && version.validation.issues.length > 0 && (
                  <small>历史版本：{version.validation.issues.length} 项校验记录已折叠</small>
                )}
                <details className="scenario-contract-json">
                  <summary>查看完整契约 JSON</summary>
                  <pre>{JSON.stringify(version.contract, null, 2)}</pre>
                </details>
                {isCurrent && version.contract.entities.length > 0 && (
                  <ScenarioEntityIdentityEditor key={`${run.id}:${version.id}`} version={version}
                    disabled={busy || !!binding} onSaved={load} />
                )}
              </div>
              {version.status === "draft" && (
                <button
                  disabled={busy || !!binding || !release.ready}
                  onClick={() => void publish(version)}
                  title={release.blockedReason}
                  type="button"
                >
                  <Send size={14} />{run.automation_level === "ai_kp" ? "人工接管审核并发布" : "审核并发布"}
                </button>
              )}
            </article>
          )})}
        </div>
      ) : <p className="empty-copy">还没有契约版本。</p>}

      {!!issues.length && (
        <details
          className="scenario-contract-issues"
          open={issues.some((issue) => issue.severity === "error")}
        >
          <summary>编译报告（{issues.length}）</summary>
          <ul>{issues.map((issue, index) => (
            <li key={`${issue.code}:${issue.path}:${index}`}>
              <strong>{issue.severity} · {issue.code}</strong>
              <span>{issue.path || "contract"}：{issue.message}</span>
            </li>
          ))}</ul>
        </details>
      )}

      <details>
        <summary>无模型：粘贴人工契约 JSON</summary>
        <textarea
          aria-label="人工 ScenarioContract JSON"
          disabled={busy || !!binding}
          onChange={(event) => setManualJson(event.target.value)}
          placeholder="粘贴完整 ScenarioContract；服务器仍会严格编译，不直接信任 JSON。"
          rows={8}
          spellCheck={false}
          value={manualJson}
        />
        <button
          disabled={busy || !!binding || !manualJson.trim()}
          onClick={() => void compileManual()}
          type="button"
        >
          编译人工契约
        </button>
      </details>

      <p className="inline-message" role="status">{message}</p>
    </section>
  );
}

function summarizeIssues(issues: ScenarioContractIssue[]) {
  return {
    errors: issues.filter((issue) => issue.severity === "error").length,
    warnings: issues.filter((issue) => issue.severity === "warning").length,
    categories: new Set(issues.map((issue) => issue.code)).size
  };
}

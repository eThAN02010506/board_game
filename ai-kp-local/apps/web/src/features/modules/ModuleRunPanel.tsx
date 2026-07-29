import {
  CheckCircle2,
  CirclePause,
  Play,
  RefreshCw,
  Save
} from "lucide-react";
import { FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";

import {
  getCurrentModuleRun,
  isApiError,
  listModuleRuns,
  startModuleRun,
  updateModuleRun
} from "../../api/client";
import type { ModuleRecord, ModuleRun, ModuleRunUpdate } from "../../api/types";

type Props = {
  campaignId: string;
  modules: ModuleRecord[];
  selectedModuleId: string;
};

type RunDraft = {
  runId: string | null;
  moduleId: string | null;
  moduleTitle: string;
  scene: string;
  spoilerTags: string;
  stateText: string;
  dirty: boolean;
};

type PreservedDraft = Omit<RunDraft, "dirty">;
type DraftSyncOutcome = "synced" | "retained" | "detached";

const statusLabels = {
  active: "运行中",
  paused: "已暂停",
  completed: "已完成"
} as const;

function parseTags(value: string): string[] {
  return [...new Set(
    value
      .split(/[,\n，]/)
      .map((tag) => tag.trim())
      .filter(Boolean)
  )];
}

function parseState(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value || "{}");
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("运行状态必须是 JSON 对象。");
  }
  return parsed as Record<string, unknown>;
}

function draftFromRun(
  run: ModuleRun | null,
  selectedModule: ModuleRecord | null = null
): RunDraft {
  return {
    runId: run?.id ?? null,
    moduleId: run?.module_id ?? selectedModule?.id ?? null,
    moduleTitle: run?.module_title ?? selectedModule?.title ?? "",
    scene: run?.current_scene_key ?? "",
    spoilerTags: run?.active_spoiler_tags.join(", ") ?? "",
    stateText: JSON.stringify(run?.state ?? {}, null, 2),
    dirty: false
  };
}

export function ModuleRunPanel({
  campaignId,
  modules,
  selectedModuleId
}: Props) {
  const selectedModule = modules.find((item) => item.id === selectedModuleId) ?? null;
  const [runs, setRuns] = useState<ModuleRun[]>([]);
  const [current, setCurrent] = useState<ModuleRun | null>(null);
  const [draft, setDraft] = useState<RunDraft>(() => draftFromRun(null, selectedModule));
  const draftRef = useRef(draft);
  const selectedModuleRef = useRef(selectedModule);
  const [preservedDraft, setPreservedDraft] = useState<PreservedDraft | null>(null);
  const [message, setMessage] = useState("正在读取当前模组运行……");
  const [busy, setBusy] = useState(false);

  function replaceDraft(next: RunDraft) {
    draftRef.current = next;
    setDraft(next);
  }

  function editDraft(changes: Partial<Pick<RunDraft, "scene" | "spoilerTags" | "stateText">>) {
    const ownerModule = current ? null : selectedModuleRef.current;
    const ownerModuleId = current?.module_id ?? ownerModule?.id ?? null;
    const ownerRunId = current?.id ?? null;
    const previous = (
      draftRef.current.runId === ownerRunId &&
      draftRef.current.moduleId === ownerModuleId
    )
      ? draftRef.current
      : draftFromRun(current, ownerModule);
    replaceDraft({
      ...previous,
      ...changes,
      moduleTitle:
        previous.moduleTitle ||
        current?.module_title ||
        ownerModule?.title ||
        "未开始模组",
      dirty: true
    });
  }

  function syncDraft(
    run: ModuleRun | null,
    preserveDirty: boolean
  ): DraftSyncOutcome {
    const previous = draftRef.current;
    const target = draftFromRun(run, run ? null : selectedModuleRef.current);
    if (preserveDirty && previous.dirty) {
      if (
        previous.runId === target.runId &&
        previous.moduleId === target.moduleId
      ) {
        return "retained";
      }
      const recoverable: PreservedDraft = {
        runId: previous.runId,
        moduleId: previous.moduleId,
        moduleTitle: previous.moduleTitle,
        scene: previous.scene,
        spoilerTags: previous.spoilerTags,
        stateText: previous.stateText
      };
      setPreservedDraft(recoverable);
      replaceDraft(target);
      return "detached";
    }
    replaceDraft(target);
    return "synced";
  }

  async function loadRuns({
    silent = false,
    preserveDirty = false
  }: {
    silent?: boolean;
    preserveDirty?: boolean;
  } = {}): Promise<DraftSyncOutcome> {
    if (!silent) setBusy(true);
    try {
      const [loadedRuns, loadedCurrent] = await Promise.all([
        listModuleRuns(campaignId),
        getCurrentModuleRun(campaignId)
      ]);
      setRuns(loadedRuns);
      setCurrent(loadedCurrent);
      const outcome = syncDraft(loadedCurrent, preserveDirty);
      if (!silent) {
        if (outcome === "retained") {
          setMessage("已刷新服务端运行版本；本地未保存草稿已保留。");
        } else if (outcome === "detached") {
          setMessage("活动运行已变化；旧草稿已移到只读保留区，不会套用到新的运行。");
        } else {
          setMessage(
            loadedCurrent
              ? `当前正在运行《${loadedCurrent.module_title}》。`
              : "当前没有活动模组，可从模组版本中选择并开始。"
          );
        }
      }
      return outcome;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      if (silent) throw error;
      return "synced";
    } finally {
      if (!silent) setBusy(false);
    }
  }

  useEffect(() => {
    void loadRuns();
  }, [campaignId]);

  useLayoutEffect(() => {
    selectedModuleRef.current = selectedModule;
    if (current) return;
    const previous = draftRef.current;
    const target = draftFromRun(null, selectedModule);
    if (
      previous.runId === target.runId &&
      previous.moduleId === target.moduleId
    ) {
      if (!previous.dirty && previous.moduleTitle !== target.moduleTitle) {
        replaceDraft(target);
      }
      return;
    }
    if (previous.dirty) {
      setPreservedDraft({
        runId: previous.runId,
        moduleId: previous.moduleId,
        moduleTitle: previous.moduleTitle,
        scene: previous.scene,
        spoilerTags: previous.spoilerTags,
        stateText: previous.stateText
      });
      setMessage(
        `已切换到《${selectedModule?.title ?? "未选择模组"}》；旧模组草稿已移到只读保留区。`
      );
    }
    replaceDraft(target);
  }, [current?.id, selectedModuleId, selectedModule?.title]);

  async function recoverConflict() {
    const outcome = await loadRuns({ silent: true, preserveDirty: true });
    setMessage(
      outcome === "retained"
        ? "运行记录已被其他 KP 更新；已载入新版本并保留本地草稿，请确认后重试。"
        : outcome === "detached"
          ? "运行记录已结束或切换；旧草稿已保留在只读区，不会套用到新的运行。"
          : "运行记录已被其他 KP 更新；已重新载入当前状态，请确认后重试。"
    );
  }

  async function runMutation(
    action: () => Promise<ModuleRun>,
    success: string,
    preserveDirtyOnSuccess = false
  ) {
    setBusy(true);
    try {
      await action();
      const outcome = await loadRuns({
        silent: true,
        preserveDirty: preserveDirtyOnSuccess
      });
      setMessage(
        outcome === "retained"
          ? `${success} 本地未保存草稿已保留。`
          : outcome === "detached"
            ? `${success} 旧运行的未保存草稿已移到只读保留区。`
            : success
      );
    } catch (error) {
      if (isApiError(error, 409) && error.code === "conflict") {
        try {
          await recoverConflict();
        } catch (refreshError) {
          setMessage(
            `运行记录发生冲突，且重新载入失败：${
              refreshError instanceof Error ? refreshError.message : String(refreshError)
            }`
          );
        }
      } else {
        setMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setBusy(false);
    }
  }

  async function startSelected(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedModule) return;
    const draftSnapshot = draftRef.current;
    if (
      draftSnapshot.runId !== null ||
      draftSnapshot.moduleId !== selectedModule.id
    ) {
      setMessage("当前草稿不属于所选模组，已阻止提交；请确认模组后重新填写。");
      return;
    }
    let state: Record<string, unknown>;
    try {
      state = parseState(draftSnapshot.stateText);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      return;
    }
    await runMutation(
      () => startModuleRun(campaignId, {
        module_id: selectedModule.id,
        current_scene_key: draftSnapshot.scene.trim() || null,
        active_spoiler_tags: parseTags(draftSnapshot.spoilerTags),
        state
      }),
      `已开始运行《${selectedModule.title}》。`
    );
  }

  async function saveCurrent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!current) return;
    const draftSnapshot = draftRef.current;
    if (
      draftSnapshot.runId !== current.id ||
      draftSnapshot.moduleId !== current.module_id
    ) {
      setMessage("当前草稿不属于活动运行，已阻止提交；请刷新后重新确认。");
      return;
    }
    let state: Record<string, unknown>;
    try {
      state = parseState(draftSnapshot.stateText);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      return;
    }
    await runMutation(
      () => updateModuleRun(current.id, {
        expected_version: current.version,
        current_scene_key: draftSnapshot.scene.trim() || null,
        active_spoiler_tags: parseTags(draftSnapshot.spoilerTags),
        state
      }),
      "当前场景、剧透范围与运行状态已保存。"
    );
  }

  async function changeStatus(run: ModuleRun, status: ModuleRunUpdate["status"]) {
    if (!status) return;
    const action = status === "active" ? "恢复" : status === "paused" ? "暂停" : "完成";
    await runMutation(
      () => updateModuleRun(run.id, {
        expected_version: run.version,
        status
      }),
      `已${action}《${run.module_title}》。`,
      true
    );
  }

  const form = (
    <form
      className="module-run-form"
      onSubmit={(event) => void (current ? saveCurrent(event) : startSelected(event))}
    >
      <label>
        当前场景
        <input
          disabled={busy}
          maxLength={160}
          onChange={(event) => editDraft({ scene: event.target.value })}
          placeholder="如 scene-1 或 仓库"
          value={draft.scene}
        />
      </label>
      <label>
        已解锁剧透标签
        <input
          disabled={busy}
          onChange={(event) => editDraft({ spoilerTags: event.target.value })}
          placeholder="逗号分隔，如 act-1, warehouse"
          value={draft.spoilerTags}
        />
      </label>
      <label className="module-run-state-field">
        KP 运行状态（JSON 对象）
        <textarea
          disabled={busy}
          onChange={(event) => editDraft({ stateText: event.target.value })}
          rows={5}
          spellCheck={false}
          value={draft.stateText}
        />
      </label>
      <button
        disabled={
          busy ||
          (!current && (
            !selectedModule ||
            draft.runId !== null ||
            draft.moduleId !== selectedModule.id
          ))
        }
        type="submit"
      >
        {current ? <Save size={14} /> : <Play size={14} />}
        {current ? "保存运行进度" : `开始${selectedModule ? `《${selectedModule.title}》` : "所选模组"}`}
      </button>
    </form>
  );

  return (
    <section className="page-card module-run-card">
      <div className="page-intro">
        <div><p className="eyebrow">仅当前团 KP 可见</p><h2>当前模组运行</h2></div>
        <button
          aria-label="刷新模组运行"
          className="ghost-button"
          disabled={busy}
          onClick={() => void loadRuns({ preserveDirty: true })}
          type="button"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      {current ? (
        <>
          <div className="module-run-summary">
            <div><span>活动模组</span><strong>{current.module_title}</strong></div>
            <div><span>状态</span><strong>{statusLabels[current.status]}</strong></div>
            <div><span>版本</span><strong>{current.version}</strong></div>
            <div><span>来源哈希</span><strong title={current.module_source_hash ?? ""}>
              {current.module_source_hash?.slice(0, 12) ?? "内部文本"}
            </strong></div>
          </div>
          {form}
          <div className="button-row module-run-status-actions">
            <button
              disabled={busy}
              onClick={() => void changeStatus(current, "paused")}
              type="button"
            >
              <CirclePause size={14} />暂停运行
            </button>
            <button
              disabled={busy}
              onClick={() => void changeStatus(current, "completed")}
              type="button"
            >
              <CheckCircle2 size={14} />标记完成
            </button>
          </div>
          {selectedModule && selectedModule.id !== current.module_id && (
            <p className="permission-hint">
              要切换到《{selectedModule.title}》，请先暂停当前运行；随后可用上方选中的模组开始新运行。
            </p>
          )}
        </>
      ) : form}

      <p className="inline-message" role="status">{message}</p>

      {preservedDraft && (
        <section
          aria-label="已保留的冲突草稿"
          className="module-run-preserved-draft"
        >
          <h3>已保留的旧运行草稿</h3>
          <p>
            《{preservedDraft.moduleTitle || "未命名模组"}》· 运行 ID{" "}
            <code>{preservedDraft.runId ?? "尚未开始"}</code>
          </p>
          <dl>
            <div><dt>场景</dt><dd>{preservedDraft.scene || "（空）"}</dd></div>
            <div><dt>剧透标签</dt><dd>{preservedDraft.spoilerTags || "（空）"}</dd></div>
          </dl>
          <label>
            运行状态 JSON（只读，可复制）
            <textarea
              aria-label="已保留草稿的运行状态 JSON"
              readOnly
              rows={5}
              spellCheck={false}
              value={preservedDraft.stateText}
            />
          </label>
        </section>
      )}

      {!!runs.length && (
        <details className="module-run-history">
          <summary>运行历史（{runs.length}）</summary>
          <div>
            {runs.map((run) => (
              <article key={run.id}>
                <div>
                  <strong>{run.module_title}</strong>
                  <small>{statusLabels[run.status]} · v{run.version} · {run.started_at}</small>
                </div>
                {run.status !== "active" && (
                  <button
                    aria-label={`恢复《${run.module_title}》（开始于 ${run.started_at}）`}
                    disabled={busy}
                    onClick={() => void changeStatus(run, "active")}
                    type="button"
                  >
                    <Play size={13} />恢复
                  </button>
                )}
              </article>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

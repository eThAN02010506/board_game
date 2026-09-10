import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  ScenarioContractJob,
  ScenarioContractVersion
} from "../../api/types";
import { ScenarioContractPanel } from "./ScenarioContractPanel";

vi.mock("../../api/client", () => ({
  bindScenarioContract: vi.fn(),
  compileScenarioContract: vi.fn(),
  generateScenarioContract: vi.fn(),
  getScenarioContractBinding: vi.fn(),
  listScenarioContractJobs: vi.fn(),
  listScenarioContracts: vi.fn(),
  listScenarioSourceScopes: vi.fn(),
  publishScenarioContract: vi.fn(),
  retryScenarioContractJob: vi.fn()
}));

const run: ModuleRun = {
  id: "run-1",
  campaign_id: "campaign-1",
  module_id: "module-1",
  module_title: "雾港疑云",
  module_source_hash: "a".repeat(64),
  status: "active",
  automation_level: "ai_kp",
  current_scene_key: "harbor",
  current_scene_title: "港口",
  play_pace: "freeform",
  current_location_entity_id: null,
  scene_started_world_time: null,
  active_spoiler_tags: [],
  state: {},
  version: 1,
  started_by_member_id: "kp-1",
  started_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
  completed_at: null
};

const draft: ScenarioContractVersion = {
  id: "contract-version-1",
  module_id: run.module_id,
  version: 1,
  row_version: 1,
  status: "draft",
  contract_hash: "b".repeat(64),
  created_at: "2026-08-11T00:00:00Z",
  published_at: null,
  contract: {
    contract_id: "module-contract",
    title: run.module_title,
    source_version: 1,
    ruleset_id: "coc7",
    locations: [{ location_id: "harbor", title: "港口" }],
    entities: [],
    clues: [],
    operators: [{ operator_id: "search", title: "搜索", policy: "automatic" }],
    task_methods: [],
    endings: []
  },
  validation: {
    valid: true,
    release_ready: true,
    provenance_ready: true,
    issues: [],
    contract_hash: "b".repeat(64),
    playability: {
      ready: true,
      explored_state_count: 4,
      proofs: [
        "scene_reachability",
        "branch_consequences",
        "source_content_delivery",
        "failure_recovery",
        "core_clue_discoverability",
        "ending_reachability"
      ].map((invariant) => ({
        invariant: invariant as
          | "scene_reachability"
          | "branch_consequences"
          | "source_content_delivery"
          | "failure_recovery"
          | "core_clue_discoverability"
          | "ending_reachability",
        status: "passed" as const,
        witness: ["verified"],
        counterexamples: []
      }))
    }
  }
};

const binding: ScenarioContractBinding = {
  run_id: run.id,
  contract_version_id: draft.id,
  contract_hash: draft.contract_hash,
  version: 1,
  source_version: 1,
  active_overlay_id: null
};

const queuedJob: ScenarioContractJob = {
  id: "contract-job-1",
  campaign_id: run.campaign_id,
  module_id: run.module_id,
  run_id: run.id,
  ruleset_id: "coc7",
  automation_level: "ai_kp",
  status: "queued",
  stage: "queued",
  progress_current: 0,
  progress_total: 2,
  attempt_count: 0,
  max_attempts: 3,
  last_error: null,
  retryable: true,
  result: null,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z"
};

const wholeSourceScope = {
  key: "whole-document",
  title: run.module_title,
  start_order_index: 0,
  end_order_index: 20,
  block_count: 21,
  character_count: 4000,
  first_page: 1,
  last_page: 12,
  semantic_counts: { text: 18, check: 2, ending: 1 },
  whole_document: true
};

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe("ScenarioContractPanel", () => {
  beforeEach(() => {
    vi.mocked(listScenarioContracts).mockResolvedValue([]);
    vi.mocked(listScenarioContractJobs).mockResolvedValue([]);
    vi.mocked(listScenarioSourceScopes).mockResolvedValue([wholeSourceScope]);
    vi.mocked(getScenarioContractBinding).mockResolvedValue(null);
    vi.mocked(bindScenarioContract).mockResolvedValue(binding);
    vi.mocked(publishScenarioContract).mockResolvedValue({
      ...draft,
      status: "published",
      row_version: 2,
      published_at: "2026-08-11T00:01:00Z"
    });
  });

  it("reads the binding after a terminal Full-AI job stabilizes", async () => {
    const terminalJob: ScenarioContractJob = {
      ...queuedJob,
      status: "succeeded",
      stage: "completed",
      progress_current: 2,
      result: {
        authoring: {
          candidate: {}, attempt_count: 1, partition_count: 2,
          completed_partition_count: 2, validation_errors: [],
          review: { decision: "approve", findings: [], assumptions_resolved: true }
        },
        compilation: {
          decision: "auto_publishable",
          report: draft.validation,
          coverage: { required_item_count: 1, covered_item_count: 1, coverage_ratio: 1 }
        },
        version: { ...draft, status: "published", row_version: 2 },
        auto_published: true,
        corpus_block_count: 2,
        corpus_total_block_count: 2,
        corpus_truncated: false,
        model: "test-model",
        binding
      }
    };
    const jobResponse = deferred<ScenarioContractJob[]>();
    let workerTransactionCommitted = false;
    vi.mocked(listScenarioContracts).mockResolvedValue([
      { ...draft, status: "published", row_version: 2 }
    ]);
    vi.mocked(listScenarioContractJobs).mockImplementation(async () => {
      const result = await jobResponse.promise;
      workerTransactionCommitted = true;
      return result;
    });
    vi.mocked(getScenarioContractBinding).mockImplementation(async () =>
      workerTransactionCommitted ? binding : null
    );

    render(<ScenarioContractPanel run={run} />);

    await waitFor(() => expect(listScenarioContractJobs).toHaveBeenCalledOnce());
    expect(getScenarioContractBinding).not.toHaveBeenCalled();
    jobResponse.resolve([terminalJob]);

    expect(await screen.findByText("Kernel 已启用")).toBeVisible();
    expect(screen.getByText(/玩家行动会进入确定性 Kernel/)).toBeVisible();
    expect(screen.getByRole("button", { name: "绑定已发布版本" })).toBeDisabled();
    expect(screen.queryByText("尚未绑定")).not.toBeInTheDocument();
  });

  it("queues full-AI generation and exposes partition progress", async () => {
    const user = userEvent.setup();
    vi.mocked(generateScenarioContract).mockResolvedValue(queuedJob);
    vi.mocked(listScenarioContractJobs)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([queuedJob]);

    render(<ScenarioContractPanel run={run} />);
    await screen.findByText(/还没有可执行契约/);
    await user.click(screen.getByRole("button", { name: /从模组证据生成/ }));

    expect(await screen.findByText(/后台契约编译 queued/)).toBeVisible();
    expect(screen.getAllByText(/0\/2 分区/)).toHaveLength(2);
    expect(generateScenarioContract).toHaveBeenCalledWith(
      run.module_id,
      "coc7",
      "whole-document"
    );
  });

  it("accepts an immediately terminal semantic attempt without leaving generation busy", async () => {
    const user = userEvent.setup();
    const rejectedAttemptOne: ScenarioContractJob = {
      ...queuedJob,
      status: "succeeded",
      stage: "review_rejected",
      progress_current: 2,
      result: {
        authoring: {
          candidate: {}, attempt_count: 1, partition_count: 2,
          completed_partition_count: 2, validation_errors: [],
          review: {
            decision: "reject", findings: ["第一轮未通过。"],
            assumptions_resolved: false
          }
        },
        compilation: null,
        version: draft,
        auto_published: false,
        corpus_block_count: 2,
        corpus_total_block_count: 2,
        corpus_truncated: false,
        model: "test-model",
        binding: null
      }
    };
    const rejectedAttemptTwo: ScenarioContractJob = {
      ...rejectedAttemptOne,
      id: "contract-job-2",
      result: {
        ...rejectedAttemptOne.result!,
        authoring: {
          ...rejectedAttemptOne.result!.authoring,
          review: {
            decision: "reject", findings: ["第二轮仍未通过。"],
            assumptions_resolved: false
          }
        }
      }
    };
    vi.mocked(generateScenarioContract).mockResolvedValue(rejectedAttemptTwo);
    vi.mocked(listScenarioContractJobs)
      .mockResolvedValueOnce([rejectedAttemptOne])
      .mockResolvedValueOnce([rejectedAttemptTwo, rejectedAttemptOne]);

    render(<ScenarioContractPanel run={run} />);
    const generateButton = await screen.findByRole("button", {
      name: /从模组证据生成/
    });
    await user.click(generateButton);

    expect(await screen.findByText(/第二轮仍未通过/)).toBeVisible();
    expect(generateButton).toBeEnabled();
    expect(screen.getAllByText("完成 · 自动审核未通过")).toHaveLength(2);
    expect(screen.queryByText(/已进入后台队列/)).not.toBeInTheDocument();
  });

  it("requires an explicit chapter when the source is a compendium", async () => {
    const user = userEvent.setup();
    vi.mocked(listScenarioSourceScopes).mockResolvedValue([
      wholeSourceScope,
      {
        ...wholeSourceScope,
        key: "section-20-a1",
        title: "Chapter 2: 雾中来客",
        start_order_index: 20,
        whole_document: false
      },
      {
        ...wholeSourceScope,
        key: "section-40-b2",
        title: "Chapter 3: 山下回声",
        start_order_index: 40,
        whole_document: false
      }
    ]);
    vi.mocked(generateScenarioContract).mockResolvedValue(queuedJob);

    render(<ScenarioContractPanel run={run} />);
    const generateButton = await screen.findByRole("button", { name: /从模组证据生成/ });
    expect(generateButton).toBeDisabled();
    await user.selectOptions(
      screen.getByLabelText("本次要玩的剧本范围"),
      "section-20-a1"
    );
    expect(generateButton).toBeEnabled();
    await user.click(generateButton);

    expect(generateScenarioContract).toHaveBeenCalledWith(
      run.module_id,
      "coc7",
      "section-20-a1"
    );
  });

  it("publishes a reviewed draft and then binds the immutable version", async () => {
    const user = userEvent.setup();
    const warnedDraft = {
      ...draft,
      validation: {
        ...draft.validation,
        issues: [{
          severity: "warning" as const,
          code: "unresolved_authoring_assumption",
          path: "authoring.assumptions.0",
          message: "AI 审核要求人工复核这一效果"
        }]
      }
    };
    const published = { ...warnedDraft, status: "published" as const, row_version: 2 };
    vi.mocked(listScenarioContracts)
      .mockResolvedValueOnce([warnedDraft])
      .mockResolvedValueOnce([published]);

    render(<ScenarioContractPanel run={run} />);
    const riskSummary = await screen.findByText(/当前版本风险：0 错误 · 1 警告 · 1 类/);
    expect(screen.getByText(/AI 审核要求人工复核/)).not.toBeVisible();
    await user.click(riskSummary);
    expect(screen.getByText(/AI 审核要求人工复核/)).toBeVisible();
    expect(screen.getByText("查看完整契约 JSON")).toBeVisible();
    await user.click(await screen.findByRole("button", { name: /审核并发布/ }));
    await waitFor(() => expect(publishScenarioContract).toHaveBeenCalledWith(draft.id, 1));
    await user.click(screen.getByRole("button", { name: /绑定已发布版本/ }));

    await waitFor(() => expect(bindScenarioContract).toHaveBeenCalledWith(run.id, draft.id));
    expect(await screen.findByText(/不可变契约版本/)).toBeVisible();
  });

  it("blocks publication when a causal playability proof fails", async () => {
    const incomplete = {
      ...draft,
      validation: {
        ...draft.validation,
        release_ready: false,
        playability: {
          ...draft.validation.playability,
          ready: false,
          proofs: draft.validation.playability.proofs.map((proof) =>
            proof.invariant === "ending_reachability"
              ? {
                  ...proof,
                  status: "failed" as const,
                  witness: [],
                  counterexamples: ["没有从初始状态到结局的可执行路径。"]
                }
              : proof
          )
        }
      }
    };
    vi.mocked(listScenarioContracts).mockResolvedValue([incomplete]);

    render(<ScenarioContractPanel run={run} />);

    expect(
      await screen.findByText("仅结构合法 · 因果证明未达到发布条件")
    ).toBeVisible();
    const publishButton = screen.getByRole("button", { name: "人工接管审核并发布" });
    expect(publishButton).toBeDisabled();
    expect(publishButton).toHaveAttribute(
      "title",
      "六项因果可玩性证明尚未全部通过"
    );
    await userEvent.setup().click(screen.getByText("六项因果可玩性证明"));
    expect(screen.getByText(/没有从初始状态到结局/)).toBeVisible();
  });

  it("blocks a provenance-only failure with a source-specific reason", async () => {
    vi.mocked(listScenarioContracts).mockResolvedValue([{
      ...draft,
      validation: {
        ...draft.validation,
        release_ready: false,
        provenance_ready: false
      }
    }]);

    render(<ScenarioContractPanel run={run} />);

    expect(
      await screen.findByText("仅结构合法 · 存在未绑定来源的可执行记录")
    ).toBeVisible();
    const publishButton = screen.getByRole("button", { name: "人工接管审核并发布" });
    expect(publishButton).toBeDisabled();
    expect(publishButton).toHaveAttribute(
      "title",
      "存在未绑定来源的可执行记录"
    );
  });

  it("does not trust a legacy release-ready flag when provenance is missing", async () => {
    vi.mocked(listScenarioContracts).mockResolvedValue([{
      ...draft,
      validation: {
        ...draft.validation,
        release_ready: true,
        provenance_ready: false
      }
    }]);

    render(<ScenarioContractPanel run={run} />);

    expect(
      await screen.findByText("仅结构合法 · 存在未绑定来源的可执行记录")
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "人工接管审核并发布" })).toBeDisabled();
    expect(screen.queryByText(/来源与可玩性证明通过/)).not.toBeInTheDocument();
  });

  it("does not offer a legacy published contract for binding when provenance is missing", async () => {
    vi.mocked(listScenarioContracts).mockResolvedValue([{
      ...draft,
      status: "published",
      validation: {
        ...draft.validation,
        release_ready: true,
        provenance_ready: false
      }
    }]);

    render(<ScenarioContractPanel run={run} />);

    expect(
      await screen.findByText("仅结构合法 · 存在未绑定来源的可执行记录")
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "绑定已发布版本" })).toBeDisabled();
  });

  it("reports provenance and causal failures together", async () => {
    vi.mocked(listScenarioContracts).mockResolvedValue([{
      ...draft,
      validation: {
        ...draft.validation,
        release_ready: false,
        provenance_ready: false,
        playability: {
          ...draft.validation.playability,
          ready: false
        }
      }
    }]);

    render(<ScenarioContractPanel run={run} />);

    expect(
      await screen.findByText("仅结构合法 · 来源绑定与因果证明均未达到发布条件")
    ).toBeVisible();
    const publishButton = screen.getByRole("button", { name: "人工接管审核并发布" });
    expect(publishButton).toBeDisabled();
    expect(publishButton).toHaveAttribute(
      "title",
      "存在未绑定来源的可执行记录；六项因果可玩性证明尚未全部通过"
    );
  });

  it("offers deterministic manual compilation when no model is available", async () => {
    const user = userEvent.setup();
    vi.mocked(compileScenarioContract).mockResolvedValue({
      compilation: { report: {
        ...draft.validation,
        valid: false,
        release_ready: false,
        issues: [{
          severity: "error",
          code: "missing_operator",
          path: "operators",
          message: "至少需要一个行动"
        }]
      } },
      version: null
    });

    render(<ScenarioContractPanel run={run} />);
    await screen.findByText(/还没有可执行契约/);
    await user.click(screen.getByText("无模型：粘贴人工契约 JSON"));
    fireEvent.change(screen.getByLabelText("人工 ScenarioContract JSON"), {
      target: { value: "{}" }
    });
    await user.click(screen.getByRole("button", { name: "编译人工契约" }));

    expect(await screen.findByText(/至少需要一个行动/)).toBeVisible();
    expect(compileScenarioContract).toHaveBeenCalledWith(run.module_id, {});
  });

  it("lets the KP retry an exhausted recoverable background job", async () => {
    const user = userEvent.setup();
    const failedJob: ScenarioContractJob = {
      ...queuedJob,
      status: "failed",
      stage: "failed",
      attempt_count: 3,
      last_error: "模型服务暂时不可用"
    };
    vi.mocked(listScenarioContractJobs)
      .mockResolvedValueOnce([failedJob])
      .mockResolvedValueOnce([queuedJob]);
    vi.mocked(retryScenarioContractJob).mockResolvedValue(queuedJob);

    render(<ScenarioContractPanel run={run} />);
    expect(await screen.findByText("失败 · 后台契约编译失败")).toBeVisible();
    expect(await screen.findAllByText(/模型服务暂时不可用/)).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(retryScenarioContractJob).toHaveBeenCalledWith(failedJob.id));
    expect(await screen.findByText(/已重新排队/)).toBeVisible();
  });

  it("shows deterministic coverage and check-mapping diagnostics", async () => {
    const completedJob: ScenarioContractJob = {
      ...queuedJob,
      status: "succeeded",
      stage: "succeeded",
      progress_current: 2,
      result: {
        authoring: {
          candidate: {},
          attempt_count: 2,
          partition_count: 2,
          completed_partition_count: 2,
          validation_errors: [],
          check_mappings: [{
            action_id: "repair-door",
            source_term: "修理",
            resolved_skill_keys: ["coc7.electrical_repair", "coc7.mechanical_repair"],
            status: "resolved"
          }],
          normalizations: [{
            code: "kp_signal_public_projection_removed",
            record_kind: "consequence_signals",
            record_id: "hidden-danger"
          }],
          repair_diagnostics: [{
            partition_index: 0,
            model_attempt: 2,
            group: "actions",
            record_index: 1,
            status: "applied",
            validation_errors: ["policy: invalid value"]
          }, {
            partition_index: 1,
            model_attempt: 3,
            group: "endings",
            record_index: 0,
            status: "discarded",
            validation_errors: ["all_conditions: required"]
          }],
          coverage_supplement: {
            target_count: 4,
            partition_count: 2,
            completed_partition_count: 2,
            failed_partition_count: 0
          },
          review: {
            decision: "approve",
            findings: [],
            assumptions_resolved: true
          }
        },
        compilation: {
          decision: "review_required",
          report: {
            ...draft.validation,
            release_ready: false,
            issues: []
          },
          coverage: { required_item_count: 4, covered_item_count: 3, coverage_ratio: 0.75 }
        },
        version: null,
        auto_published: false,
        corpus_block_count: 2,
        corpus_total_block_count: 2,
        corpus_truncated: false,
        model: "test-model",
        binding: null
      }
    };
    vi.mocked(listScenarioContractJobs).mockResolvedValue([completedJob]);

    render(<ScenarioContractPanel run={run} />);

    expect(await screen.findByText("来源义务覆盖 3/4")).toBeVisible();
    expect(screen.getByText("检定词映射 1/1")).toBeVisible();
    expect(screen.getByText("确定性安全规范化 1 项")).toBeVisible();
    expect(screen.getByText("独立 AI 审核通过 · 已确认全部组装假设")).toBeVisible();
    expect(screen.getByText("定向记录修复：应用 1 · 丢弃 1 · 失败 0")).toBeVisible();
    expect(screen.getByText("来源义务补写 2/2 分区 · 4 项目标")).toBeVisible();
  });

  it("shows non-negative per-cycle supplement progress after deferred multi-cycle work", async () => {
    const jobResponse = deferred<ScenarioContractJob[]>();
    const multiCycleJob: ScenarioContractJob = {
      ...queuedJob,
      status: "succeeded",
      stage: "review_rejected",
      progress_current: 21,
      progress_total: 21,
      result: {
        authoring: {
          candidate: {}, attempt_count: 7, partition_count: 2,
          completed_partition_count: 2, validation_errors: [],
          coverage_supplement: {
            target_count: 4,
            partition_count: 2,
            completed_partition_count: 17,
            failed_partition_count: -15
          },
          post_review_coverage: [{
            cycle: 1,
            target_count: 2,
            partition_count: 2,
            completed_partition_count: 2,
            failed_partition_count: 0
          }, {
            cycle: 2,
            target_count: 1,
            partition_count: 2,
            completed_partition_count: 1,
            failed_partition_count: 1
          }],
          review: {
            decision: "reject", findings: ["仍有一项硬约束未覆盖。"],
            assumptions_resolved: false
          }
        },
        compilation: {
          decision: "review_required",
          report: { ...draft.validation, release_ready: false },
          coverage: { required_item_count: 4, covered_item_count: 3, coverage_ratio: 0.75 }
        },
        version: draft,
        auto_published: false,
        corpus_block_count: 2,
        corpus_total_block_count: 2,
        corpus_truncated: false,
        model: "test-model",
        binding: null
      }
    };
    vi.mocked(listScenarioContractJobs).mockReturnValue(jobResponse.promise);

    render(<ScenarioContractPanel run={run} />);
    expect(screen.getByText("正在读取可执行契约……")).toBeVisible();
    jobResponse.resolve([multiCycleJob]);

    expect(await screen.findByText(
      "来源义务补写：2 分区计划 · 4 项目标 · 已报告成功批次 17 · 失败统计无效"
    )).toBeVisible();
    expect(screen.getByText("复审补写周期 1 2/2 分区 · 2 项目标")).toBeVisible();
    expect(screen.getByText("复审补写周期 2 1/2 分区 · 1 项目标 · 1 失败")).toBeVisible();
    expect(screen.queryByText(/17\/2/)).not.toBeInTheDocument();
    expect(screen.queryByText(/-15/)).not.toBeInTheDocument();
  });

  it("distinguishes a completed worker from a rejected full-AI review", async () => {
    vi.mocked(listScenarioContractJobs).mockResolvedValue([{
      ...queuedJob,
      status: "succeeded",
      stage: "review_rejected",
      progress_current: 1,
      result: {
        authoring: {
          candidate: {}, attempt_count: 1, partition_count: 1,
          completed_partition_count: 1, validation_errors: [],
          review: {
            decision: "reject", findings: ["记录缺少来源支持。"],
            assumptions_resolved: false
          }
        },
        compilation: null,
        version: draft,
        auto_published: false,
        corpus_block_count: 1,
        corpus_total_block_count: 1,
        corpus_truncated: false,
        model: "test-model",
        binding: null
      }
    }]);
    vi.mocked(listScenarioContracts).mockResolvedValue([draft]);

    render(<ScenarioContractPanel run={run} />);

    expect(await screen.findByText("完成 · 自动审核未通过")).toBeVisible();
    expect(screen.getByRole("button", { name: "人工接管审核并发布" })).toBeVisible();
    expect(screen.queryByText("succeeded · completed")).not.toBeInTheDocument();
  });

  it("does not present an intermediate review repair stage as terminal", async () => {
    vi.mocked(listScenarioContractJobs).mockResolvedValue([{
      ...queuedJob,
      status: "running",
      stage: "review_rejected",
      progress_current: 5,
      progress_total: 6
    }]);

    render(<ScenarioContractPanel run={run} />);

    expect(await screen.findByText("running · review_rejected")).toBeVisible();
    expect(screen.queryByText("完成 · 自动审核未通过")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "从模组证据生成" })).toBeDisabled();
  });
});

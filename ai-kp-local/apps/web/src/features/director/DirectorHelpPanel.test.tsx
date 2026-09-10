import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  askDirectorHelp,
  getCurrentModuleRun,
  listDirectorHelpAudits
} from "../../api/client";
import type {
  DirectorHelpAdvice,
  DirectorHelpAuditItem,
  DirectorHelpAuditPage,
  ModuleRun
} from "../../api/types";
import { DirectorHelpPanel } from "./DirectorHelpPanel";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    askDirectorHelp: vi.fn(),
    getCurrentModuleRun: vi.fn(),
    listDirectorHelpAudits: vi.fn()
  };
});

const run: ModuleRun = {
  id: "run/1",
  campaign_id: "campaign/1",
  module_id: "module/1",
  module_title: "湖之仆从",
  module_source_hash: "a".repeat(64),
  status: "active",
  current_scene_key: "motel-night",
  current_scene_title: "汽车旅馆深夜",
  play_pace: "structured",
  current_location_entity_id: "motel",
  scene_started_world_time: "1926-08-13 01:45",
  active_spoiler_tags: ["finale"],
  state: {},
  version: 8,
  started_by_member_id: "kp/1",
  started_at: "2026-09-06 10:00:00",
  updated_at: "2026-09-06 10:30:00",
  completed_at: null,
  director_control_mode: "human_kp"
};

const advice: DirectorHelpAdvice = {
  run_id: run.id,
  run_version: 8,
  contract_id: "servants-of-the-lake",
  contract_hash: "b".repeat(64),
  scenario_version: 3,
  state_version: 12,
  basis_hash: "c".repeat(64),
  question: "玩家要烧掉旅馆，我该怎么办？",
  status: "partial",
  answer: "先请玩家说明点火位置与逃生计划，再判断是否进入有风险的结构化行动。",
  follow_up_question: null,
  suggested_response: "可以。你打算在哪里点火，并且怎么离开？",
  suggested_response_audience: "kp_review_only",
  next_steps: ["确认玩家的方法", "交代明显风险", "再决定是否检定"],
  confidence: "medium",
  uncertainty_reasons: ["契约没有单独的纵火行动。"],
  assumptions: ["玩家尚未点火。"],
  citations: [{
    evidence_id: "evidence/1",
    source_type: "scenario_contract",
    authority: "executable_contract",
    visibility: "kp",
    title: "汽车旅馆的夜间事件",
    text: "旅馆在深夜仍有住客和工作人员。",
    source_locator: "PDF p.61",
    source_refs: [],
    source_refs_total_count: 0,
    source_refs_truncated: false
  }],
  action: {
    candidate_id: "start-fire",
    kind: "operator",
    title: "尝试在旅馆纵火",
    available: true,
    reason: "玩家有时间接触可燃物。",
    policy: "required_check",
    selected_skill_key: "luck",
    step_operator_ids: [],
    skill_choices: [{
      skill_key: "luck",
      difficulty: "regular",
      reason: "判断火势是否在被发现前成形。",
      hidden: false,
      bonus_dice: 0,
      allow_push: false,
      scope: "点火与逃生",
      automatic_information: ["建筑内仍有人。"],
      failure_stakes: "火势失控或调查员被困。",
      pushed_failure_stakes: ""
    }],
    skill_choices_total_count: 1,
    skill_choices_truncated: false,
    automatic_information: ["烟雾会很快惊动其他人。"],
    maximum_effect: "只预览纵火的直接后果，不会自动结束模组。",
    success_effects: ["火势开始蔓延"],
    success_effects_total_count: 1,
    success_effects_truncated: false,
    failure_effects: ["附近 NPC 被惊动"],
    failure_effects_total_count: 1,
    failure_effects_truncated: false
  },
  writes_performed: false,
  can_execute: false
};

const emptyHistoryPage: DirectorHelpAuditPage = {
  items: [],
  next_before_id: null
};

function historyItem(
  overrides: Partial<DirectorHelpAuditItem> = {}
): DirectorHelpAuditItem {
  return {
    id: "audit/1",
    run_id: run.id,
    requested_by_member_id: "kp/1",
    question: "当时怎样推进旅馆场景？",
    outcome: "completed",
    error_code: null,
    duration_ms: 1250,
    created_at: "2026-09-06T10:31:00Z",
    completed_at: "2026-09-06T10:31:01Z",
    response_hash: "d".repeat(64),
    advice,
    ...overrides
  };
}

function rejectWhenAborted(signal?: AbortSignal): Promise<DirectorHelpAdvice> {
  return new Promise((_resolve, reject) => {
    const abort = () => reject(
      signal?.reason ?? new DOMException("request was aborted", "AbortError")
    );
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}

function rejectHistoryWhenAborted(signal?: AbortSignal): Promise<DirectorHelpAuditPage> {
  return new Promise((_resolve, reject) => {
    const abort = () => reject(
      signal?.reason ?? new DOMException("request was aborted", "AbortError")
    );
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}

describe("DirectorHelpPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getCurrentModuleRun).mockResolvedValue(run);
    vi.mocked(askDirectorHelp).mockResolvedValue(advice);
    vi.mocked(listDirectorHelpAudits).mockResolvedValue(emptyHistoryPage);
  });

  it("opens a prominent help surface and focuses the question", async () => {
    const user = userEvent.setup();
    render(<DirectorHelpPanel campaignId="campaign/1" />);

    const trigger = screen.getByRole("button", { name: /需要帮助/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("询问一个具体的带团问题")).toHaveFocus();
  });

  it("loads the first campaign history page on open and shows an empty state", async () => {
    const user = userEvent.setup();
    render(<DirectorHelpPanel campaignId="campaign/1" />);

    await user.click(screen.getByRole("button", { name: /需要帮助/ }));

    await waitFor(() => expect(listDirectorHelpAudits).toHaveBeenCalledWith(
      "campaign/1",
      {
        limit: 10,
        beforeId: undefined,
        signal: expect.any(AbortSignal)
      }
    ));
    expect(await screen.findByText("还没有 Need Help 历史记录。")).toBeVisible();
  });

  it("expands a completed audit through the shared read-only advice card", async () => {
    const user = userEvent.setup();
    vi.mocked(listDirectorHelpAudits).mockResolvedValueOnce({
      items: [historyItem()],
      next_before_id: null
    });
    render(<DirectorHelpPanel campaignId="campaign/1" />);

    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    const list = await screen.findByRole("list", { name: "Need Help 历史记录" });
    expect(list).toHaveAttribute("aria-busy", "false");
    const toggle = screen.getByRole("button", { name: /当时怎样推进旅馆场景/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const detailsId = toggle.getAttribute("aria-controls");
    expect(detailsId).toBeTruthy();
    expect(document.getElementById(detailsId!)).not.toBeNull();
    const card = screen.getByLabelText("历史 AI 带团建议");
    expect(within(card).getByText("历史快照 · 基于当时状态 · 不可执行")).toBeVisible();
    expect(within(card).getByText("当时可用")).toBeVisible();
    expect(within(card).queryByText("当前可用")).not.toBeInTheDocument();
    expect(within(card).getByText("给 KP 的措辞草稿")).toBeVisible();
    expect(within(card).getByText("仅供 KP 审阅，可能综合隐藏资料，不可直接念给玩家。")).toBeVisible();
    expect(within(card).getByText("仅 KP")).toBeVisible();
    expect(screen.queryByRole("button", { name: /执行|应用|确认裁定/ })).not.toBeInTheDocument();
  });

  it("renders pending and failed audits with safe status text only", async () => {
    const user = userEvent.setup();
    vi.mocked(listDirectorHelpAudits).mockResolvedValueOnce({
      items: [
        historyItem({
          id: "audit/requested",
          question: "还在处理的问题",
          outcome: "requested",
          duration_ms: null,
          completed_at: null,
          response_hash: null,
          advice: null
        }),
        historyItem({
          id: "audit/failed",
          question: "没有完成的问题",
          outcome: "failed",
          error_code: "private_backend_trace",
          advice: null
        })
      ],
      next_before_id: null
    });
    render(<DirectorHelpPanel campaignId="campaign/1" />);

    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.click(await screen.findByRole("button", { name: /还在处理的问题/ }));
    expect(screen.getByRole("status")).toHaveTextContent("这次请求仍在处理中");

    await user.click(screen.getByRole("button", { name: /没有完成的问题/ }));
    expect(screen.getByText("这次请求未能生成建议。")).toBeVisible();
    expect(screen.queryByText(/private_backend_trace|trace/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("历史 AI 带团建议")).not.toBeInTheDocument();
  });

  it("keeps existing history after a safe refresh failure and recovers on retry", async () => {
    const user = userEvent.setup();
    const oldItem = historyItem({ id: "audit/old", question: "仍需保留的旧问题" });
    const newItem = historyItem({ id: "audit/new", question: "刷新后的新问题" });
    vi.mocked(listDirectorHelpAudits)
      .mockResolvedValueOnce({ items: [oldItem], next_before_id: null })
      .mockRejectedValueOnce(new ApiError("private storage detail", 500, "private_code"))
      .mockResolvedValueOnce({ items: [newItem], next_before_id: null });
    render(<DirectorHelpPanel campaignId="campaign/1" />);

    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    expect(await screen.findByText("仍需保留的旧问题")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "刷新 Need Help 历史" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("暂时无法加载历史记录，请稍后重试。");
    expect(alert).not.toHaveTextContent(/private|storage|detail|private_code/i);
    expect(screen.getByText("仍需保留的旧问题")).toBeVisible();

    await user.click(within(alert).getByRole("button", { name: "重试加载历史记录" }));
    expect(await screen.findByText("刷新后的新问题")).toBeVisible();
    expect(screen.queryByText("仍需保留的旧问题")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("loads older pages with the cursor and deduplicates overlapping ids", async () => {
    const user = userEvent.setup();
    const latest = historyItem({ id: "audit/latest", question: "最新问题" });
    const overlap = historyItem({ id: "audit/overlap", question: "重复问题" });
    const older = historyItem({ id: "audit/older", question: "更早问题" });
    let resolveOlder!: (page: DirectorHelpAuditPage) => void;
    const olderPage = new Promise<DirectorHelpAuditPage>((resolve) => {
      resolveOlder = resolve;
    });
    vi.mocked(listDirectorHelpAudits)
      .mockResolvedValueOnce({
        items: [latest, overlap],
        next_before_id: "audit/older cursor"
      })
      .mockImplementationOnce(() => olderPage);
    render(<DirectorHelpPanel campaignId="campaign/1" />);

    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    const list = await screen.findByRole("list", { name: "Need Help 历史记录" });
    await user.click(screen.getByRole("button", { name: "加载更早的 Need Help 记录" }));

    expect(list).toHaveAttribute("aria-busy", "true");
    expect(listDirectorHelpAudits).toHaveBeenNthCalledWith(2, "campaign/1", {
      limit: 10,
      beforeId: "audit/older cursor",
      signal: expect.any(AbortSignal)
    });
    await act(async () => resolveOlder({
      items: [overlap, older],
      next_before_id: null
    }));

    await waitFor(() => expect(list).toHaveAttribute("aria-busy", "false"));
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getAllByText("重复问题")).toHaveLength(1);
    expect(screen.getByText("更早问题")).toBeVisible();
    expect(screen.queryByRole("button", { name: "加载更早的 Need Help 记录" })).not.toBeInTheDocument();
  });

  it("refreshes the first history page without blocking a successful answer", async () => {
    const user = userEvent.setup();
    let resolveRefresh!: (page: DirectorHelpAuditPage) => void;
    const refresh = new Promise<DirectorHelpAuditPage>((resolve) => {
      resolveRefresh = resolve;
    });
    vi.mocked(listDirectorHelpAudits)
      .mockResolvedValueOnce(emptyHistoryPage)
      .mockImplementationOnce(() => refresh);
    render(<DirectorHelpPanel campaignId="campaign/1" />);

    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await screen.findByText("还没有 Need Help 历史记录。");
    await user.type(screen.getByLabelText("询问一个具体的带团问题"), "现在怎样推进？");
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    expect(await screen.findByLabelText("AI 带团建议")).toBeVisible();
    expect(listDirectorHelpAudits).toHaveBeenCalledTimes(2);
    expect(screen.getByText("正在加载历史记录……")).toBeVisible();
    await act(async () => resolveRefresh(emptyHistoryPage));
    await waitFor(() => expect(screen.queryByText("正在加载历史记录……")).not.toBeInTheDocument());
  });

  it("aborts a history request when the panel closes", async () => {
    const user = userEvent.setup();
    vi.mocked(listDirectorHelpAudits).mockImplementationOnce((_campaignId, options) =>
      rejectHistoryWhenAborted(options?.signal)
    );
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    const trigger = screen.getByRole("button", { name: /需要帮助/ });

    await user.click(trigger);
    await waitFor(() => expect(listDirectorHelpAudits).toHaveBeenCalledOnce());
    const signal = vi.mocked(listDirectorHelpAudits).mock.calls[0]?.[1]?.signal;
    await user.click(trigger);

    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText("历史建议")).not.toBeInTheDocument();
  });

  it("aborts old history and loads the new campaign after a campaign switch", async () => {
    const user = userEvent.setup();
    vi.mocked(listDirectorHelpAudits)
      .mockImplementationOnce((_campaignId, options) =>
        rejectHistoryWhenAborted(options?.signal)
      )
      .mockResolvedValueOnce(emptyHistoryPage);
    const { rerender } = render(<DirectorHelpPanel campaignId="campaign/1" />);

    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await waitFor(() => expect(listDirectorHelpAudits).toHaveBeenCalledOnce());
    const firstSignal = vi.mocked(listDirectorHelpAudits).mock.calls[0]?.[1]?.signal;
    rerender(<DirectorHelpPanel campaignId="campaign/2" />);

    await waitFor(() => expect(firstSignal?.aborted).toBe(true));
    await waitFor(() => expect(listDirectorHelpAudits).toHaveBeenNthCalledWith(
      2,
      "campaign/2",
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    ));
    expect(await screen.findByText("还没有 Need Help 历史记录。")).toBeVisible();
  });

  it("aborts a history request when the panel unmounts", async () => {
    const user = userEvent.setup();
    vi.mocked(listDirectorHelpAudits).mockImplementationOnce((_campaignId, options) =>
      rejectHistoryWhenAborted(options?.signal)
    );
    const { unmount } = render(<DirectorHelpPanel campaignId="campaign/1" />);

    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await waitFor(() => expect(listDirectorHelpAudits).toHaveBeenCalledOnce());
    const signal = vi.mocked(listDirectorHelpAudits).mock.calls[0]?.[1]?.signal;
    unmount();

    expect(signal?.aborted).toBe(true);
  });

  it("asks against the current run and renders read-only, source-backed advice", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockResolvedValueOnce({
      ...advice,
      citations: [{
        ...advice.citations[0],
        source_refs: [
          { source_block_id: "block/1", document_id: "module.pdf", page: 61, paragraph: 2 },
          { source_block_id: "block/2", document_id: "module.pdf", page: 61, paragraph: 4 }
        ],
        source_refs_total_count: 7,
        source_refs_truncated: true
      }],
      action: {
        ...advice.action!,
        skill_choices: [
          ...advice.action!.skill_choices,
          { ...advice.action!.skill_choices[0], skill_key: "spot-hidden" }
        ],
        skill_choices_total_count: 6,
        skill_choices_truncated: true,
        success_effects_total_count: 5,
        success_effects_truncated: true,
        failure_effects_total_count: 4,
        failure_effects_truncated: true
      }
    });
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(
      screen.getByLabelText("询问一个具体的带团问题"),
      "  玩家要烧掉旅馆，我该怎么办？  "
    );
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    await waitFor(() => expect(getCurrentModuleRun).toHaveBeenCalledWith(
      "campaign/1",
      { signal: expect.any(AbortSignal) }
    ));
    expect(askDirectorHelp).toHaveBeenCalledWith(
      "run/1",
      "玩家要烧掉旅馆，我该怎么办？",
      { signal: expect.any(AbortSignal) }
    );
    expect(await screen.findByText("建议这样处理")).toBeVisible();
    expect(screen.getByText("给 KP 的措辞草稿")).toBeVisible();
    expect(screen.getByText("仅供 KP 审阅，可能综合隐藏资料，不可直接念给玩家。")).toBeVisible();
    expect(screen.queryByText("可以这样对玩家说")).not.toBeInTheDocument();
    expect(screen.getByText("建议检定：luck · regular")).toBeVisible();
    expect(screen.getByText("火势开始蔓延")).toBeVisible();
    expect(screen.getByText("仅预览，尚未应用")).toBeVisible();
    expect(screen.getByText("汽车旅馆的夜间事件")).toBeVisible();
    expect(screen.getByText("仅 KP")).toBeVisible();
    expect(screen.getByText("仅显示前 2/7 条来源定位。")).toBeVisible();
    expect(screen.getByText("仅显示前 2/6 项技能选项。")).toBeVisible();
    expect(screen.getByText("仅显示前 1/5 项成功效果。")).toBeVisible();
    expect(screen.getByText("仅显示前 1/4 项失败效果。")).toBeVisible();
    expect(screen.getByText("不可直接执行")).toBeVisible();
    expect(screen.queryByRole("button", { name: /执行|应用|确认裁定/ })).not.toBeInTheDocument();
  });

  it("renders asset and knowledge citations without fabricating links when source refs are empty", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockResolvedValueOnce({
      ...advice,
      action: null,
      citations: [
        {
          evidence_id: "asset/guest-register",
          source_type: "module_asset",
          authority: "source_context_only",
          visibility: "table",
          title: "旅客登记簿扫描件",
          text: "OCR 显示车牌 AB1652 曾登记入住。",
          source_locator: "PDF p.55 · image 2",
          source_refs: [],
          source_refs_total_count: 0,
          source_refs_truncated: false
        },
        {
          evidence_id: "knowledge/james-car",
          source_type: "module_knowledge",
          authority: "source_context_only",
          visibility: "kp",
          title: "James 的车辆记录",
          text: "该资料只用于说明登记簿与失踪者的关联。",
          source_locator: "knowledge:vehicle-record",
          source_refs: [],
          source_refs_total_count: 0,
          source_refs_truncated: false
        }
      ]
    });
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(
      screen.getByLabelText("询问一个具体的带团问题"),
      "登记簿能说明什么？"
    );
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    expect(await screen.findByText("旅客登记簿扫描件")).toBeVisible();
    expect(screen.getByText("PDF p.55 · image 2")).toBeVisible();
    expect(screen.getByText("OCR 显示车牌 AB1652 曾登记入住。")).toBeVisible();
    expect(screen.getByText("James 的车辆记录")).toBeVisible();
    expect(screen.getByText("knowledge:vehicle-record")).toBeVisible();
    expect(screen.getByText("该资料只用于说明登记簿与失踪者的关联。")).toBeVisible();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /执行|应用|确认裁定/ })).not.toBeInTheDocument();
    expect(screen.getByText("不可直接执行")).toBeVisible();
  });

  it("submits with Ctrl+Enter and closes with Escape while restoring focus", async () => {
    const user = userEvent.setup();
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    const trigger = screen.getByRole("button", { name: /需要帮助/ });
    await user.click(trigger);
    const input = screen.getByLabelText("询问一个具体的带团问题");
    await user.type(input, "玩家想保护被害人");
    await user.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(askDirectorHelp).toHaveBeenCalledOnce());

    await user.keyboard("{Escape}");
    expect(screen.queryByLabelText("询问一个具体的带团问题")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it.each([
    ["director_help_in_progress", "已有一个带团帮助请求正在处理中，请等待完成后再试。"],
    ["director_help_capacity_exceeded", "当前带团帮助请求较多，请稍后再试。"],
    ["director_help_audit_unavailable", "无法保存带团帮助记录，因此未返回建议，请稍后重试。"],
    ["conflict", "当前模组或游戏状态已变化，请重新提问。"],
    ["rate_limit_exceeded", "请求过于频繁，请稍后再试。"],
    ["upstream_invalid_response", "AI 返回了无法验证的格式，请重新尝试。"],
    ["upstream_service_error", "AI 服务暂时不可用，请稍后再试。"],
    ["invalid_input", "问题内容不符合要求，请检查后重试。"]
  ])("maps %s to a safe retryable message", async (code, expectedMessage) => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockRejectedValueOnce(
      new ApiError("sensitive internal provider detail", 409, code)
    );
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    const input = screen.getByLabelText("询问一个具体的带团问题");
    await user.type(input, "我该怎么推进？");
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(expectedMessage);
    expect(alert).not.toHaveTextContent("sensitive internal provider detail");
    expect(input).toHaveValue("我该怎么推进？");
    expect(screen.getByRole("button", { name: "获取建议" })).toBeEnabled();
  });

  it.each([
    new ApiError("private backend traceback", 500, "unknown_internal_code"),
    new ApiError("private unclassified detail", 502),
    new Error("private network stack detail")
  ])("uses one safe fallback for an unknown failure", async (failure) => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockRejectedValueOnce(failure);
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(screen.getByLabelText("询问一个具体的带团问题"), "现在怎么办？");
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("暂时无法获取建议，请稍后重试。");
    expect(alert).not.toHaveTextContent(/private|traceback|stack|detail/);
  });

  it("treats AbortError as silent control flow", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockRejectedValueOnce(
      new DOMException("request was aborted", "AbortError")
    );
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(screen.getByLabelText("询问一个具体的带团问题"), "现在怎么办？");
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    await waitFor(() => expect(
      screen.getByRole("button", { name: "获取建议" })
    ).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("AI 带团建议")).not.toBeInTheDocument();
  });

  it("aborts the previous request before starting a new submission", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp)
      .mockImplementationOnce((_runId, _question, options) =>
        rejectWhenAborted(options?.signal)
      )
      .mockResolvedValueOnce(advice);
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    const input = screen.getByLabelText("询问一个具体的带团问题");
    await user.type(input, "现在怎么办？");
    await user.click(screen.getByRole("button", { name: "获取建议" }));
    await waitFor(() => expect(askDirectorHelp).toHaveBeenCalledTimes(1));

    const firstSignal = vi.mocked(askDirectorHelp).mock.calls[0]?.[2]?.signal;
    const form = input.closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    await waitFor(() => expect(askDirectorHelp).toHaveBeenCalledTimes(2));
    expect(firstSignal?.aborted).toBe(true);
    expect(vi.mocked(askDirectorHelp).mock.calls[1]?.[2]?.signal?.aborted).toBe(false);
    expect(await screen.findByText("建议这样处理")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("aborts the active request when the panel closes", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockImplementationOnce(
      (_runId, _question, options) => rejectWhenAborted(options?.signal)
    );
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    const trigger = screen.getByRole("button", { name: /需要帮助/ });
    await user.click(trigger);
    await user.type(screen.getByLabelText("询问一个具体的带团问题"), "现在怎么办？");
    await user.click(screen.getByRole("button", { name: "获取建议" }));
    await waitFor(() => expect(askDirectorHelp).toHaveBeenCalledOnce());
    const signal = vi.mocked(askDirectorHelp).mock.calls[0]?.[2]?.signal;

    await user.click(trigger);

    expect(signal?.aborted).toBe(true);
    expect(screen.queryByLabelText("询问一个具体的带团问题")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("aborts the active request and clears private state when the campaign changes", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockImplementationOnce(
      (_runId, _question, options) => rejectWhenAborted(options?.signal)
    );
    const { rerender } = render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(screen.getByLabelText("询问一个具体的带团问题"), "秘密问题");
    await user.click(screen.getByRole("button", { name: "获取建议" }));
    await waitFor(() => expect(askDirectorHelp).toHaveBeenCalledOnce());
    const signal = vi.mocked(askDirectorHelp).mock.calls[0]?.[2]?.signal;

    rerender(<DirectorHelpPanel campaignId="campaign/2" />);

    await waitFor(() => expect(signal?.aborted).toBe(true));
    expect(screen.getByLabelText("询问一个具体的带团问题")).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("AI 带团建议")).not.toBeInTheDocument();
  });

  it("aborts the active request when the panel unmounts", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockImplementationOnce(
      (_runId, _question, options) => rejectWhenAborted(options?.signal)
    );
    const { unmount } = render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(screen.getByLabelText("询问一个具体的带团问题"), "现在怎么办？");
    await user.click(screen.getByRole("button", { name: "获取建议" }));
    await waitFor(() => expect(askDirectorHelp).toHaveBeenCalledOnce());
    const signal = vi.mocked(askDirectorHelp).mock.calls[0]?.[2]?.signal;

    unmount();

    expect(signal?.aborted).toBe(true);
  });

  it("does not imply a check or canon fact when no supported action or source exists", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockResolvedValueOnce({
      ...advice,
      status: "no_evidence",
      confidence: "low",
      action: null,
      citations: []
    });
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(
      screen.getByLabelText("询问一个具体的带团问题"),
      "这个镇有没有飞机场？"
    );
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    expect(await screen.findByText("当前没有可安全建议的契约行动或检定；请根据上方问题补充提示或暂停推进。")).toBeVisible();
    expect(screen.getByText("没有可核验的来源，请不要把这条建议当作模组事实。")).toBeVisible();
  });

  it("labels a task-method suggestion without inventing a resolution policy", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockResolvedValueOnce({
      ...advice,
      action: {
        ...advice.action!,
        title: "分步营救被害人",
        policy: null,
        step_operator_ids: ["find-victim", "free-victim", "escape"]
      }
    });
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(
      screen.getByLabelText("询问一个具体的带团问题"),
      "怎样营救被害人？"
    );
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    expect(await screen.findByText("分步营救被害人")).toBeVisible();
    expect(screen.getByText("分步任务")).toBeVisible();
    expect(screen.getByText(/find-victim → free-victim → escape/)).toBeVisible();
  });

  it("makes a clarification question prominent", async () => {
    const user = userEvent.setup();
    vi.mocked(askDirectorHelp).mockResolvedValueOnce({
      ...advice,
      status: "clarify",
      follow_up_question: "你想从哪个出口离开？"
    });
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.type(
      screen.getByLabelText("询问一个具体的带团问题"),
      "玩家说要离开"
    );
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    expect(await screen.findByText("请先向玩家问清楚")).toBeVisible();
    expect(screen.getByText("你想从哪个出口离开？")).toBeVisible();
  });

  it("requires a non-empty question without making a request", async () => {
    const user = userEvent.setup();
    render(<DirectorHelpPanel campaignId="campaign/1" />);
    await user.click(screen.getByRole("button", { name: /需要帮助/ }));
    await user.click(screen.getByRole("button", { name: "获取建议" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请输入你想询问的带团问题");
    expect(getCurrentModuleRun).not.toHaveBeenCalled();
    expect(askDirectorHelp).not.toHaveBeenCalled();
  });
});

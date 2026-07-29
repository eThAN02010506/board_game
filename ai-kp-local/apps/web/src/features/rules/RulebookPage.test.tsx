import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  RuleSource
} from "../../api/types";
import { RulebookPage } from "./RulebookPage";

vi.mock("../../api/client", () => ({
  listRuleReviewCandidates: vi.fn(),
  requestBinary: vi.fn(),
  requestJson: vi.fn(),
  reviewRuleCandidate: vi.fn()
}));

const source: RuleSource = {
  id: "rulesource_1",
  ruleset_id: "coc7-keeper-cn-2002c",
  title: "守秘人规则书",
  source_filename: "keeper.pdf",
  source_hash: "a".repeat(64),
  page_count: 320,
  status: "ready",
  chunk_count: 80,
  rule_count: 1
};

const secondSource: RuleSource = {
  ...source,
  id: "rulesource_2",
  title: "第二份规则书",
  source_filename: "second.pdf",
  source_hash: "c".repeat(64)
};

const candidate: RuleReviewCandidate = {
  id: "rule_1",
  source_id: source.id,
  rule_key: "combat.major_wound",
  version: 1,
  rule_type: "damage",
  title: "重伤判定",
  status: "review_required",
  object_hash: "b".repeat(64),
  confidence: 0.9,
  object: {
    summary: "单次伤害达到最大生命值一半时造成重伤。",
    execution: { kind: "condition_effects" },
    citations: [{
      chunk_id: "chunk_1",
      page: 42,
      evidence_text: "单次伤害达到最大生命值一半时造成重伤。"
    }]
  },
  validation: {
    schema: { passed: true, errors: [] },
    source_binding: { passed: true, errors: [] },
    citations: { passed: true, errors: [] },
    conflicts: { passed: true, errors: [] },
    passed: true
  }
};

const secondCandidate: RuleReviewCandidate = {
  ...candidate,
  id: "rule_2",
  source_id: secondSource.id,
  rule_key: "sanity.loss",
  title: "理智损失",
  object_hash: "d".repeat(64)
};

const kpIdentity: AuthIdentity = {
  member_id: "member_kp",
  session_id: "session_1",
  campaign_id: "campaign_1",
  role: "kp",
  display_name: "KP",
  pc_id: null
};

const kpQueryResult: RuleQueryResult = {
  retrieval_backend: "lexical_fallback",
  source,
  chunks: [{
    id: "chunk_secret",
    page_start: 99,
    page_end: 99,
    chapter: "守秘人秘密",
    section: "幕后真相",
    text: "只有 KP 应当看到的规则秘密。"
  }],
  rules: []
};

describe("RulebookPage rule review", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/rulebooks/sources") return [source] as never;
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.mocked(requestBinary).mockResolvedValue(source as never);
    vi.mocked(listRuleReviewCandidates)
      .mockResolvedValueOnce([candidate])
      .mockResolvedValue([]);
    vi.mocked(reviewRuleCandidate).mockResolvedValue({
      ...candidate,
      status: "validated"
    });
  });

  it("lets a KP approve a candidate with a golden case", async () => {
    const user = userEvent.setup();
    render(<RulebookPage identity={kpIdentity} />);

    expect(await screen.findByRole("heading", { name: "规则候选审核" })).toBeVisible();
    expect(await screen.findByText("combat.major_wound")).toBeVisible();
    expect(screen.getAllByText(/单次伤害达到最大生命值一半/).length).toBeGreaterThan(1);
    expect(screen.getByText("schema")).toBeVisible();
    await user.click(screen.getByText("查看完整规则候选对象"));
    expect(screen.getByText(/"kind": "condition_effects"/)).toBeVisible();
    expect(screen.getByText(/只会进入待审核/)).toBeVisible();

    fireEvent.change(
      screen.getByLabelText("审核备注（combat.major_wound）"),
      { target: { value: "已与原文示例核对" } }
    );
    fireEvent.change(
      screen.getByLabelText("Golden inputs JSON（combat.major_wound）"),
      { target: { value: '{"damage":6,"max_hp":10}' } }
    );
    fireEvent.change(
      screen.getByLabelText("Expected JSON（combat.major_wound）"),
      { target: { value: '{"major_wound":true}' } }
    );
    await user.click(screen.getByRole("button", {
      name: "批准 combat.major_wound"
    }));

    await waitFor(() => expect(reviewRuleCandidate).toHaveBeenCalledWith(
      "rule_1",
      {
        decision: "approved",
        note: "已与原文示例核对",
        golden_cases: [{
          name: "combat.major_wound KP 审核用例",
          inputs: { damage: 6, max_hp: 10 },
          expected_output: { major_wound: true }
        }]
      }
    ));
    expect(await screen.findByText(/Golden 校验通过/)).toBeVisible();
  });

  it("rejects invalid golden JSON without sending a review", async () => {
    const user = userEvent.setup();
    render(<RulebookPage identity={kpIdentity} />);

    await screen.findByText("combat.major_wound");
    fireEvent.change(
      screen.getByLabelText("Golden inputs JSON（combat.major_wound）"),
      { target: { value: "[" } }
    );
    await user.click(screen.getByRole("button", {
      name: "批准 combat.major_wound"
    }));

    expect(await screen.findByText("Golden inputs必须是合法 JSON 对象。")).toBeVisible();
    expect(reviewRuleCandidate).not.toHaveBeenCalled();
  });

  it("lets a KP reject a candidate only with an audit note", async () => {
    const user = userEvent.setup();
    vi.mocked(reviewRuleCandidate).mockResolvedValueOnce({
      ...candidate,
      status: "quarantined"
    });
    render(<RulebookPage identity={kpIdentity} />);

    await screen.findByText("combat.major_wound");
    await user.click(screen.getByRole("button", {
      name: "拒绝 combat.major_wound"
    }));
    expect(await screen.findByText("拒绝规则候选时必须填写审核备注。")).toBeVisible();
    expect(reviewRuleCandidate).not.toHaveBeenCalled();

    fireEvent.change(
      screen.getByLabelText("审核备注（combat.major_wound）"),
      { target: { value: "引用范围不足，退回重做" } }
    );
    await user.click(screen.getByRole("button", {
      name: "拒绝 combat.major_wound"
    }));

    await waitFor(() => expect(reviewRuleCandidate).toHaveBeenCalledWith(
      "rule_1",
      {
        decision: "rejected",
        note: "引用范围不足，退回重做",
        golden_cases: []
      }
    ));
    expect(await screen.findByText(/已拒绝 combat.major_wound/)).toBeVisible();
  });

  it("does not request or render review candidates for a player", async () => {
    render(
      <RulebookPage
        identity={{ ...kpIdentity, member_id: "member_player", role: "player" }}
      />
    );

    expect(await screen.findByRole("heading", { name: "查询规则" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "规则书与索引" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "摄取流水线" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "规则候选审核" })).not.toBeInTheDocument();
    expect(requestJson).not.toHaveBeenCalled();
    expect(listRuleReviewCandidates).not.toHaveBeenCalled();
    expect(reviewRuleCandidate).not.toHaveBeenCalled();
  });

  it("waits for a KP identity before requesting administrator rule data", async () => {
    render(<RulebookPage identity={null} />);

    expect(await screen.findByRole("heading", { name: "查询规则" })).toBeVisible();
    expect(requestJson).not.toHaveBeenCalled();
    expect(listRuleReviewCandidates).not.toHaveBeenCalled();
  });

  it("clears a resolved KP-only query result when identity changes to player", async () => {
    const user = userEvent.setup();
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/rulebooks/sources") return [source] as never;
      if (url === "/rules/query") return kpQueryResult as never;
      throw new Error(`Unexpected request: ${url}`);
    });
    const { rerender } = render(<RulebookPage identity={kpIdentity} />);

    await user.click(screen.getByRole("button", { name: "查询原文与规则对象" }));
    expect(await screen.findByText("只有 KP 应当看到的规则秘密。")).toBeInTheDocument();

    rerender(
      <RulebookPage
        identity={{ ...kpIdentity, member_id: "member_player", role: "player" }}
      />
    );

    expect(screen.queryByText("只有 KP 应当看到的规则秘密。")).not.toBeInTheDocument();
  });

  it("clears review drafts when switching between KP identities", async () => {
    vi.mocked(listRuleReviewCandidates).mockResolvedValue([candidate]);
    const { rerender } = render(<RulebookPage identity={kpIdentity} />);

    const note = await screen.findByLabelText("审核备注（combat.major_wound）");
    fireEvent.change(note, { target: { value: "KP A 尚未完成的审核" } });
    expect(note).toHaveValue("KP A 尚未完成的审核");

    rerender(
      <RulebookPage
        identity={{ ...kpIdentity, member_id: "member_kp_b", display_name: "KP B" }}
      />
    );

    expect(await screen.findByLabelText("审核备注（combat.major_wound）")).toHaveValue("");
  });

  it("ignores a delayed KP query response after switching to player", async () => {
    const user = userEvent.setup();
    let resolveQuery: (value: RuleQueryResult) => void = () => undefined;
    const delayedQuery = new Promise<RuleQueryResult>((resolve) => {
      resolveQuery = resolve;
    });
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/rulebooks/sources") return [source] as never;
      if (url === "/rules/query") return delayedQuery as never;
      throw new Error(`Unexpected request: ${url}`);
    });
    const { rerender } = render(<RulebookPage identity={kpIdentity} />);

    await user.click(screen.getByRole("button", { name: "查询原文与规则对象" }));
    expect(await screen.findByText("正在查询原文与规则对象……")).toBeVisible();
    rerender(
      <RulebookPage
        identity={{ ...kpIdentity, member_id: "member_player", role: "player" }}
      />
    );
    await act(async () => resolveQuery(kpQueryResult));

    expect(screen.queryByText("只有 KP 应当看到的规则秘密。")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查询已审核规则" })).toBeEnabled();
  });

  it("shows player query errors and removes the previous result before retrying", async () => {
    const user = userEvent.setup();
    vi.mocked(requestJson)
      .mockResolvedValueOnce({
        ...kpQueryResult,
        chunks: [{ ...kpQueryResult.chunks[0], text: "玩家可见的旧查询结果。" }]
      } as never)
      .mockRejectedValueOnce(new Error("规则查询服务暂时不可用"));
    render(
      <RulebookPage
        identity={{ ...kpIdentity, member_id: "member_player", role: "player" }}
      />
    );

    await user.click(screen.getByRole("button", { name: "查询已审核规则" }));
    expect(await screen.findByText("玩家可见的旧查询结果。")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("问题"), {
      target: { value: "第二个问题" }
    });
    await user.click(screen.getByRole("button", { name: "查询已审核规则" }));

    expect(await screen.findByText("规则查询服务暂时不可用")).toBeVisible();
    expect(screen.queryByText("玩家可见的旧查询结果。")).not.toBeInTheDocument();
  });

  it("does not apply a delayed extraction refresh to another selected source", async () => {
    const user = userEvent.setup();
    let resolveExtraction: (value: {
      processed_count: number;
      accepted_count: number;
      rejected_count: number;
    }) => void = () => undefined;
    const delayedExtraction = new Promise<{
      processed_count: number;
      accepted_count: number;
      rejected_count: number;
    }>((resolve) => {
      resolveExtraction = resolve;
    });
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/rulebooks/sources") return [source, secondSource] as never;
      if (url.includes(`/rulebooks/sources/${source.id}/extract-rules`)) {
        return delayedExtraction as never;
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.mocked(listRuleReviewCandidates).mockImplementation(async (sourceId) => (
      sourceId === source.id ? [candidate] : [secondCandidate]
    ));
    render(<RulebookPage identity={kpIdentity} />);

    expect(await screen.findByText("combat.major_wound")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "抽取下一批 5 块" }));
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith(
      expect.stringContaining(`/rulebooks/sources/${source.id}/extract-rules`),
      { method: "POST" }
    ));

    await user.click(screen.getByRole("button", { name: /第二份规则书/ }));
    expect(await screen.findByText("sanity.loss")).toBeVisible();
    expect(screen.queryByText("combat.major_wound")).not.toBeInTheDocument();

    await act(async () => resolveExtraction({
      processed_count: 1,
      accepted_count: 1,
      rejected_count: 0
    }));
    await waitFor(() => expect(
      screen.getByRole("button", { name: "抽取下一批 5 块" })
    ).toBeEnabled());

    expect(screen.getByText("sanity.loss")).toBeVisible();
    expect(screen.queryByText("combat.major_wound")).not.toBeInTheDocument();
    expect(listRuleReviewCandidates).toHaveBeenCalledTimes(2);
  });
});

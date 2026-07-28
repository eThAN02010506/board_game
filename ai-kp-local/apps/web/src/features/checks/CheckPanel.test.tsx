import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuthIdentity, SessionMember, SkillCheck } from "../../api/types";
import { CheckPanel } from "./CheckPanel";

const kpIdentity: AuthIdentity = {
  member_id: "member_kp",
  session_id: "session_test",
  campaign_id: "campaign_test",
  role: "kp",
  display_name: "守密人",
  pc_id: null
};

const playerIdentity: AuthIdentity = {
  member_id: "member_player",
  session_id: "session_test",
  campaign_id: "campaign_test",
  role: "player",
  display_name: "林若川",
  pc_id: "pc_player"
};

const members: SessionMember[] = [
  {
    id: "member_player",
    session_id: "session_test",
    campaign_id: "campaign_test",
    role: "player",
    display_name: "林若川",
    pc_id: "pc_player",
    revoked_at: null
  },
  {
    id: "member_revoked",
    session_id: "session_test",
    campaign_id: "campaign_test",
    role: "player",
    display_name: "已离席玩家",
    pc_id: "pc_revoked",
    revoked_at: "2026-07-28T10:00:00Z"
  },
  {
    id: "member_other_kp",
    session_id: "session_test",
    campaign_id: "campaign_test",
    role: "kp",
    display_name: "协助守密人",
    pc_id: null,
    revoked_at: null
  }
];

function makeCheck(overrides: Partial<SkillCheck> = {}): SkillCheck {
  return {
    id: "check_test",
    campaign_id: "campaign_test",
    session_id: "session_test",
    proposal_id: null,
    player_action_id: null,
    requested_by_member_id: "member_kp",
    roller_member_id: "member_player",
    pc_id: "pc_player",
    investigator_id: "investigator_player",
    skill_key: "spot_hidden",
    skill_name: "侦查",
    target: 60,
    target_source: "investigator_skill",
    difficulty: "regular",
    bonus_dice: 0,
    hidden: false,
    allow_push: true,
    pushed_from_check_id: null,
    status: "requested",
    input_method: null,
    raw_dice: null,
    selected_roll: null,
    threshold: null,
    success_level: null,
    passed: null,
    ruleset_id: "coc7",
    ruleset_version: "1.0.0",
    source_reference: {
      source_id: "coc7-core",
      chapter: "技能检定",
      page_start: 82,
      page_end: 83,
      sections: ["常规检定"]
    },
    investigator_state_version: 3,
    override_reason: null,
    original_result: null,
    created_at: "2026-07-28T10:00:00Z",
    resolved_at: null,
    actions: [],
    ...overrides
  };
}

function renderPanel({
  identity = kpIdentity,
  checks = [],
  loading = false
}: {
  identity?: AuthIdentity | null;
  checks?: SkillCheck[];
  loading?: boolean;
} = {}) {
  const callbacks = {
    onRefresh: vi.fn(),
    onCreate: vi.fn(),
    onResolveDigital: vi.fn(),
    onResolvePhysical: vi.fn(),
    onGenerateConsequence: vi.fn(),
    onReplay: vi.fn(),
    onOverride: vi.fn(),
    onCancel: vi.fn(),
    onPush: vi.fn()
  };

  const view = render(
    <CheckPanel
      checks={checks}
      identity={identity}
      loading={loading}
      members={members}
      {...callbacks}
    />
  );

  return { ...view, ...callbacks };
}

describe("CheckPanel", () => {
  it("lets a KP assign a check only to active player members", () => {
    const { onCreate } = renderPanel();

    expect(screen.getByRole("option", { name: "林若川" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "已离席玩家" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "协助守密人" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("技能或属性"), {
      target: { value: "图书馆使用" }
    });
    fireEvent.change(screen.getByLabelText("玩家"), {
      target: { value: "member_player" }
    });
    fireEvent.change(screen.getByLabelText("目标值"), {
      target: { value: "65" }
    });
    fireEvent.change(screen.getByLabelText("难度"), {
      target: { value: "hard" }
    });
    fireEvent.change(screen.getByLabelText("奖惩骰"), {
      target: { value: "1" }
    });
    fireEvent.click(screen.getByLabelText("暗骰"));
    fireEvent.click(screen.getByRole("button", { name: "发布检定" }));

    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onCreate).toHaveBeenCalledWith({
      skill_name: "图书馆使用",
      difficulty: "hard",
      bonus_dice: 1,
      hidden: true,
      allow_push: true,
      roller_member_id: "member_player",
      pc_id: "pc_player",
      target: 65
    });
  });

  it("allows only the assigned player or KP to resolve a requested check", () => {
    const check = makeCheck();
    const { onResolveDigital, rerender } = renderPanel({
      identity: playerIdentity,
      checks: [check]
    });

    fireEvent.click(screen.getByRole("button", { name: "数字骰" }));
    expect(onResolveDigital).toHaveBeenCalledWith("check_test");

    rerender(
      <CheckPanel
        checks={[check]}
        identity={{ ...playerIdentity, member_id: "member_other_player" }}
        loading={false}
        members={members}
        onCancel={vi.fn()}
        onCreate={vi.fn()}
        onGenerateConsequence={vi.fn()}
        onOverride={vi.fn()}
        onPush={vi.fn()}
        onRefresh={vi.fn()}
        onReplay={vi.fn()}
        onResolveDigital={onResolveDigital}
        onResolvePhysical={vi.fn()}
      />
    );

    expect(screen.queryByRole("button", { name: "数字骰" })).not.toBeInTheDocument();
    expect(screen.queryByText("KP 裁定与状态操作")).not.toBeInTheDocument();
  });

  it("blocks repeated digital resolution while loading and parses physical dice input", () => {
    const check = makeCheck({ bonus_dice: 1 });
    const loadingView = renderPanel({
      identity: playerIdentity,
      checks: [check],
      loading: true
    });

    const digitalButton = screen.getByRole("button", { name: "数字骰" });
    expect(digitalButton).toBeDisabled();
    fireEvent.click(digitalButton);
    expect(loadingView.onResolveDigital).not.toHaveBeenCalled();

    loadingView.unmount();
    const { onResolvePhysical } = renderPanel({
      identity: playerIdentity,
      checks: [check]
    });
    fireEvent.click(screen.getByText("录入实体骰"));
    fireEvent.change(screen.getByLabelText("个位"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText(/十位骰/), {
      target: { value: "2， 4" }
    });
    fireEvent.click(screen.getByRole("button", { name: "确认实体骰" }));

    expect(onResolvePhysical).toHaveBeenCalledTimes(1);
    expect(onResolvePhysical).toHaveBeenCalledWith("check_test", 7, [2, 4]);
  });

  it("exposes replay, override, and push only after a failed check is resolved", () => {
    const resolvedCheck = makeCheck({
      status: "resolved",
      input_method: "digital",
      raw_dice: {
        ones_digit: 7,
        tens_digits: [8],
        candidates: [87]
      },
      selected_roll: 87,
      threshold: 60,
      success_level: "failure",
      passed: false,
      resolved_at: "2026-07-28T10:01:00Z"
    });
    const { onOverride, onPush, onReplay } = renderPanel({
      identity: kpIdentity,
      checks: [resolvedCheck]
    });

    expect(screen.getByText("失败", { selector: "strong" })).toBeVisible();
    expect(screen.getByText("D100 = 87 · 未通过难度")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重放校验" }));
    fireEvent.click(screen.getByText("KP 裁定与状态操作"));
    fireEvent.change(screen.getByLabelText("理由或后果"), {
      target: { value: "线索仍然可见" }
    });
    fireEvent.change(screen.getByLabelText("覆盖结果"), {
      target: { value: "hard" }
    });
    fireEvent.click(screen.getByRole("button", { name: "记录 KP 覆盖" }));
    fireEvent.click(screen.getByRole("button", { name: "创建孤注一掷" }));

    expect(onReplay).toHaveBeenCalledWith("check_test");
    expect(onOverride).toHaveBeenCalledWith(
      "check_test",
      "hard",
      true,
      "线索仍然可见"
    );
    expect(onPush).toHaveBeenCalledWith("check_test", "线索仍然可见");
  });

  it("uses the idempotent consequence endpoint for every terminal check", () => {
    const resolvedCheck = makeCheck({
      player_action_id: "action_test",
      status: "resolved",
      input_method: "digital",
      selected_roll: 24,
      threshold: 60,
      success_level: "hard",
      passed: true,
      resolved_at: "2026-07-28T10:01:00Z"
    });
    const resolvedView = renderPanel({
      identity: kpIdentity,
      checks: [resolvedCheck]
    });

    fireEvent.click(screen.getByRole("button", {
      name: "生成/打开检定后果草稿"
    }));
    expect(resolvedView.onGenerateConsequence).toHaveBeenCalledWith("check_test");

    resolvedView.unmount();
    const overriddenView = renderPanel({
      identity: kpIdentity,
      checks: [{ ...resolvedCheck, status: "overridden" }]
    });

    fireEvent.click(screen.getByRole("button", {
      name: "生成/打开检定后果草稿"
    }));
    expect(overriddenView.onGenerateConsequence).toHaveBeenCalledWith("check_test");
  });

  it("keeps consequence controls KP-only and disables generation while busy", () => {
    const resolvedCheck = makeCheck({
      player_action_id: "action_test",
      status: "resolved",
      selected_roll: 24,
      threshold: 60,
      success_level: "hard",
      passed: true
    });
    const playerView = renderPanel({
      identity: playerIdentity,
      checks: [resolvedCheck]
    });

    expect(
      screen.queryByRole("button", { name: "生成/打开检定后果草稿" })
    ).not.toBeInTheDocument();

    playerView.unmount();
    const kpView = renderPanel({
      identity: kpIdentity,
      checks: [resolvedCheck],
      loading: true
    });
    const generateButton = screen.getByRole("button", {
      name: "生成/打开检定后果草稿"
    });
    expect(generateButton).toBeDisabled();
    fireEvent.click(generateButton);
    expect(kpView.onGenerateConsequence).not.toHaveBeenCalled();
  });

  it("does not offer a consequence draft for a standalone check", () => {
    renderPanel({
      identity: kpIdentity,
      checks: [makeCheck({
        status: "resolved",
        selected_roll: 24,
        threshold: 60,
        success_level: "hard",
        passed: true
      })]
    });

    expect(
      screen.queryByRole("button", { name: "生成/打开检定后果草稿" })
    ).not.toBeInTheDocument();
  });

  it("waits for the full push chain and offers the action on its leaf", () => {
    const parent = makeCheck({
      id: "check_parent",
      player_action_id: "action_test",
      status: "resolved",
      selected_roll: 84,
      threshold: 60,
      success_level: "failure",
      passed: false
    });
    const child = makeCheck({
      id: "check_child",
      player_action_id: "action_test",
      pushed_from_check_id: "check_parent",
      allow_push: false
    });
    const pendingView = renderPanel({
      identity: kpIdentity,
      checks: [parent, child]
    });

    expect(
      screen.queryByRole("button", { name: "生成/打开检定后果草稿" })
    ).not.toBeInTheDocument();

    pendingView.unmount();
    const terminalView = renderPanel({
      identity: kpIdentity,
      checks: [
        parent,
        {
          ...child,
          status: "resolved",
          input_method: "digital",
          raw_dice: {ones_digit: 4, tens_digits: [2], candidates: [24]},
          selected_roll: 24,
          threshold: 60,
          success_level: "hard",
          passed: true,
          resolved_at: "2026-07-28T10:02:00Z"
        }
      ]
    });

    fireEvent.click(screen.getByRole("button", {
      name: "生成/打开检定后果草稿"
    }));
    expect(terminalView.onGenerateConsequence).toHaveBeenCalledWith("check_child");
  });
});

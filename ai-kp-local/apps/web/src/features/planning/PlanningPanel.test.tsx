import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Capability } from "../../api/types";
import { PlanningPanel } from "./PlanningPanel";

const capabilities: Capability[] = [
  {
    id: "module_library",
    label: "模组资料库",
    status: "available",
    phase: "MVP",
    audience: "kp",
    summary: "基础模组资料已经可用。",
    dependencies: [],
    acceptance: ["可读取模组资料。"]
  },
  {
    id: "semantic_memory_search",
    label: "语义记忆检索",
    status: "planned",
    phase: "F3",
    audience: "all",
    summary: "在现有词法检索之上增加语义召回。",
    dependencies: ["module_library"],
    acceptance: ["可解释每条召回结果。"]
  },
  {
    id: "npc_reappearance",
    label: "NPC 再登场判断",
    status: "partial",
    phase: "F2",
    audience: "kp",
    summary: "已经有候选评分，仍缺少完整解释界面。",
    dependencies: ["semantic_memory_search"],
    acceptance: ["KP 能查看候选与排除原因。"]
  }
];

describe("PlanningPanel", () => {
  it("shows only unfinished capabilities and resolves dependency labels", () => {
    render(
      <PlanningPanel
        activeNav="planning"
        capabilities={capabilities}
        error=""
        loading={false}
        onRetry={vi.fn()}
      />
    );

    expect(screen.queryByRole("heading", { name: "模组资料库" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "语义记忆检索" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "NPC 再登场判断" })).toBeVisible();
    expect(screen.getByText("模组资料库", { selector: ".capability-detail p" })).toBeVisible();
    expect(screen.getByText("部分可用")).toBeVisible();
    expect(screen.getByText("规划中")).toBeVisible();
  });

  it("leaves the implemented NPC route to its dedicated workspace", () => {
    const { container } = render(
      <PlanningPanel
        activeNav="npcs"
        capabilities={capabilities}
        error=""
        loading={false}
        onRetry={vi.fn()}
      />
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("renders loading and retry states without stale capability cards", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <PlanningPanel
        activeNav="planning"
        capabilities={capabilities}
        error=""
        loading
        onRetry={onRetry}
      />
    );

    expect(screen.getByText("正在读取本地能力目录…")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "语义记忆检索" })).not.toBeInTheDocument();

    rerender(
      <PlanningPanel
        activeNav="planning"
        capabilities={capabilities}
        error="后端暂时不可用"
        loading={false}
        onRetry={onRetry}
      />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("后端暂时不可用");
    expect(screen.queryByRole("heading", { name: "语义记忆检索" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试读取" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not render outside planning routes", () => {
    const { container } = render(
      <PlanningPanel
        activeNav="play"
        capabilities={capabilities}
        error=""
        loading={false}
        onRetry={vi.fn()}
      />
    );

    expect(container).toBeEmptyDOMElement();
  });
});

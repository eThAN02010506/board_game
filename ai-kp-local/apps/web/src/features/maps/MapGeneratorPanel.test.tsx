import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Campaign } from "../../api/types";
import {
  MapGeneratorPanel,
  parseMapList,
  parseMapRoutes
} from "./MapGeneratorPanel";

const campaign: Campaign = {
  id: "camp_test",
  title: "雾港 1931",
  system: "coc7",
  current_time: "1931-10-03 19:30"
};

describe("MapGeneratorPanel", () => {
  it("parses lists and rejects malformed route rows without silently dropping them", () => {
    expect(parseMapList("警局, 报社\n仓库")).toEqual(["警局", "报社", "仓库"]);
    expect(parseMapRoutes("警局 > 报社\n坏路线")).toEqual({
      routes: [["警局", "报社"]],
      errors: ["第 2 行路线格式无效，应为“起点 > 终点”。"]
    });
  });

  it("uses campaign time and submits a complete era-aware MapSpec brief", async () => {
    const onGenerate = vi.fn();
    render(
      <MapGeneratorPanel
        campaign={campaign}
        loading={false}
        onGenerate={onGenerate}
      />
    );

    expect(screen.getByLabelText("年代")).toHaveValue(1931);
    expect(screen.getByText(/KP 私密审查项和秘密不会进入提示词/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "生成结构地图" }));

    await waitFor(() => expect(onGenerate).toHaveBeenCalledTimes(1));
    expect(onGenerate).toHaveBeenCalledWith(
      expect.objectContaining({
        era_year: 1931,
        map_kind: "regional",
        locations: ["旧码头", "废弃仓库", "报社", "警局"],
        features: ["煤气路灯", "木制货箱", "有线电话"],
        required_elements: [
          "旧码头",
          "废弃仓库",
          "报社",
          "警局",
          "煤气路灯",
          "木制货箱",
          "有线电话"
        ]
      })
    );
  });

  it("blocks generation when a route references an unknown location", () => {
    render(
      <MapGeneratorPanel
        campaign={campaign}
        loading={false}
        onGenerate={vi.fn()}
      />
    );

    fireEvent.change(screen.getByLabelText(/^路线/), {
      target: { value: "旧码头 > 不存在的车站" }
    });

    expect(screen.getByText(/路线引用未知地点：不存在的车站/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成结构地图" })).toBeDisabled();
  });

  it("resets era context when switching campaigns and rejects malformed years", () => {
    const { rerender } = render(
      <MapGeneratorPanel
        campaign={campaign}
        loading={false}
        onGenerate={vi.fn()}
      />
    );
    fireEvent.change(screen.getByLabelText("地域文化"), {
      target: { value: "上一团的秘密地点" }
    });

    rerender(
      <MapGeneratorPanel
        campaign={{
          id: "camp_other",
          title: "无年代新团",
          system: "coc7",
          current_time: null
        }}
        loading={false}
        onGenerate={vi.fn()}
      />
    );

    expect(screen.getByLabelText("地域文化")).toHaveValue("美国马萨诸塞州");
    expect(screen.getByLabelText("年代")).toHaveValue(null);
    fireEvent.change(screen.getByLabelText("年代"), {
      target: { value: "999" }
    });
    expect(screen.getByText(/年代必须是 1000–2100/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成结构地图" })).toBeDisabled();
  });
});

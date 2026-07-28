import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SavedMap } from "../../api/types";
import { MapStage } from "./MapStage";

const map: SavedMap = {
  id: "map_test",
  campaign_id: "camp_test",
  title: "1928 年警局",
  prompt: "KP brief",
  status: "draft",
  width: 1000,
  height: 700,
  revision_id: "maprev_test",
  revision_no: 1,
  svg_text: "<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>",
  overlay_svg_text: "<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>",
  map_spec: {
    schema_version: "map-spec.v1",
    title: "1928 年警局",
    style: "investigation",
    map_kind: "floorplan",
    canvas: {
      width: 1000,
      height: 700,
      coordinate_unit: "logical_px",
      origin: "top_left"
    },
    era: {
      year: 1928,
      locale: "美国马萨诸塞州",
      season: "秋季",
      time_of_day: "夜晚",
      weather: "冷雨",
      public_architecture: ["红砖"],
      technology: ["有线电话"],
      forbidden_visuals: ["LED 灯"]
    },
    locations: [],
    connections: [],
    features: [],
    coverage: { required_element_names: ["入口"] }
  },
  validation: {
    schema_version: "map-spec.v1",
    valid: true,
    coverage: { required: 1, covered: 1, percent: 100 },
    issues: []
  },
  render: {
    background_asset_url: null,
    selected_asset_id: null,
    asset_status: "none",
    fallback_svg: true
  },
  image_generation: {
    available: false,
    model: null,
    safety: "player_safe_projection"
  },
  tokens: [
    {
      id: "token_test",
      map_id: "map_test",
      label: "林若川",
      actor_type: "pc",
      actor_id: "pc_test",
      location_name: "入口",
      color: "#2f6db3",
      version: 0,
      x: 250,
      y: 300
    }
  ]
};

describe("MapStage", () => {
  it("renders quality gates and tokens in one SVG coordinate system", () => {
    const { container } = render(
      <MapStage
        activeMap={map}
        hasIdentity
        maps={[map]}
        onOpenMap={vi.fn()}
        onRefresh={vi.fn()}
        onSetPublished={vi.fn()}
        role="kp"
      />
    );

    expect(screen.getAllByText("100%")[0]).toBeVisible();
    expect(screen.getByText(/玩家安全投影/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成安全背景候选" })).toBeDisabled();
    expect(container.querySelector("svg.map-canvas-svg")).toHaveAttribute(
      "viewBox",
      "0 0 1000 700"
    );
    expect(container.querySelector("g.map-token")).toHaveAttribute(
      "transform",
      "translate(250 300)"
    );
  });

  it("does not show KP image-generation controls in player view", () => {
    render(
      <MapStage
        activeMap={{ ...map, image_generation: undefined }}
        hasIdentity
        maps={[map]}
        onOpenMap={vi.fn()}
        onRefresh={vi.fn()}
        onSetPublished={vi.fn()}
        role="player"
      />
    );

    expect(screen.queryByText("时代背景候选")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审核通过并发布" })).not.toBeInTheDocument();
  });

  it("requires the previewed candidate to be selected before publishing", () => {
    const onSelectAsset = vi.fn();
    const assetBase = {
      map_id: map.id,
      revision_id: map.revision_id!,
      audience: "table" as const,
      kind: "background" as const,
      status: "ready" as const,
      generation_input_hash: "generation",
      content_hash: "content",
      mime_type: "image/png",
      width: 1024,
      height: 1024,
      provider: "fake",
      parameters: {},
      prompt_text: "",
      error_text: null,
      content_url: "",
      created_at: "2026-01-01T00:00:00Z"
    };
    const selectedAsset = {
      ...assetBase,
      id: "asset_a",
      model: "period-map-v1",
      seed: 7
    };
    const candidateAsset = {
      ...assetBase,
      id: "asset_b",
      model: "period-map-v2",
      seed: 8
    };
    render(
      <MapStage
        activeMap={{
          ...map,
          assets: [selectedAsset, candidateAsset],
          render: {
            background_asset_url: "",
            selected_asset_id: selectedAsset.id,
            asset_status: "ready",
            fallback_svg: false
          },
          image_generation: {
            available: true,
            model: "period-map-v2",
            safety: "player_safe_projection"
          }
        }}
        hasIdentity
        maps={[map]}
        onOpenMap={vi.fn()}
        onRefresh={vi.fn()}
        onSelectAsset={onSelectAsset}
        onSetPublished={vi.fn()}
        role="kp"
      />
    );

    fireEvent.click(
      screen.getByRole("button", { name: "预览 period-map-v2 Seed 8" })
    );
    expect(screen.getByRole("button", { name: "审核通过并发布" })).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", {
        name: "选用 period-map-v2 Seed 8 作为正式背景"
      })
    );
    expect(onSelectAsset).toHaveBeenCalledWith("asset_b");
  });

  it("hides publish controls on the composite play page even for a KP", () => {
    render(
      <MapStage
        activeMap={map}
        hasIdentity
        maps={[map]}
        onOpenMap={vi.fn()}
        onRefresh={vi.fn()}
        onSetPublished={vi.fn()}
        role="kp"
        showReviewControls={false}
      />
    );

    expect(screen.queryByRole("button", { name: "审核通过并发布" })).not.toBeInTheDocument();
  });

  it("provides public scene elements in the non-visual map summary", () => {
    render(
      <MapStage
        activeMap={{
          ...map,
          map_spec: {
            ...map.map_spec!,
            locations: [
              {
                id: "loc_lobby",
                name: "接待大厅",
                visibility: "table",
                position: { x: 200, y: 200 }
              }
            ],
            features: [
              {
                id: "feature_phone",
                name: "有线电话",
                location_id: "loc_lobby",
                visibility: "table",
                position: { x: 220, y: 220 }
              }
            ]
          }
        }}
        hasIdentity
        maps={[map]}
        onOpenMap={vi.fn()}
        onRefresh={vi.fn()}
        onSetPublished={vi.fn()}
        role="player"
      />
    );

    expect(screen.getByText(/场景元素：有线电话位于接待大厅/)).toBeInTheDocument();
  });

  it("allows an invalid legacy published map to be withdrawn", () => {
    render(
      <MapStage
        activeMap={{
          ...map,
          status: "published",
          revision_id: undefined,
          validation: {
            ...map.validation!,
            valid: false
          }
        }}
        hasIdentity
        maps={[map]}
        onOpenMap={vi.fn()}
        onRefresh={vi.fn()}
        onSetPublished={vi.fn()}
        role="kp"
      />
    );

    expect(screen.getByRole("button", { name: "收回为草稿" })).toBeEnabled();
  });
});

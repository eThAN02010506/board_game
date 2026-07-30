import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  getCurrentModuleRun,
  getModuleRunDirectorState,
  listModuleRuns,
  startModuleRun,
  updateModuleRun
} from "../../api/client";
import type { ModuleRecord, ModuleRun } from "../../api/types";
import { ModuleRunPanel } from "./ModuleRunPanel";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getCurrentModuleRun: vi.fn(),
    getModuleRunDirectorState: vi.fn(),
    listModuleRuns: vi.fn(),
    startModuleRun: vi.fn(),
    updateModuleRun: vi.fn()
  };
});

const modules: ModuleRecord[] = [{
  id: "mod_1",
  campaign_id: "camp_test",
  title: "雾港疑云",
  source_type: "docx",
  source_filename: "mist.docx",
  source_hash: "a".repeat(64),
  parser_version: "module-document.v1",
  created_at: "2026-07-28"
}];

const modulesWithSecond: ModuleRecord[] = [
  ...modules,
  {
    id: "mod_2",
    campaign_id: "camp_test",
    title: "钟楼余响",
    source_type: "pdf",
    source_filename: "clocktower.pdf",
    source_hash: "b".repeat(64),
    parser_version: "module-document.v1",
    created_at: "2026-07-29"
  }
];

const run: ModuleRun = {
  id: "modrun_1",
  campaign_id: "camp_test",
  module_id: "mod_1",
  module_title: "雾港疑云",
  module_source_hash: "a".repeat(64),
  status: "active",
  current_scene_key: "仓库",
  current_scene_title: "仓库",
  play_pace: "freeform",
  current_location_entity_id: null,
  scene_started_world_time: null,
  active_spoiler_tags: ["act-1"],
  state: { clock: 1 },
  version: 4,
  started_by_member_id: "member_kp",
  started_at: "2026-07-30 09:00:00",
  updated_at: "2026-07-30 09:00:00",
  completed_at: null
};

describe("ModuleRunPanel", () => {
  beforeEach(() => {
    vi.mocked(listModuleRuns).mockResolvedValue([run]);
    vi.mocked(getCurrentModuleRun).mockResolvedValue(run);
    vi.mocked(startModuleRun).mockResolvedValue(run);
    vi.mocked(updateModuleRun).mockResolvedValue({ ...run, version: 5 });
    vi.mocked(getModuleRunDirectorState).mockResolvedValue({
      run,
      entity_states: [],
      scene_events: [],
      entity_state_events: []
    });
  });

  async function editLegacyScene(
    user: ReturnType<typeof userEvent.setup>
  ) {
    await screen.findByText("高级运行范围与兼容状态");
    await user.click(screen.getByText("高级运行范围与兼容状态"));
    return screen.getByLabelText("当前场景");
  }

  it("saves the current KP run with its expected version", async () => {
    const user = userEvent.setup();
    render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modules}
        selectedModuleId="mod_1"
      />
    );

    const scene = await editLegacyScene(user);
    await user.clear(scene);
    await user.type(scene, "地下室");
    await user.click(screen.getByRole("button", { name: "保存运行进度" }));

    await waitFor(() => expect(updateModuleRun).toHaveBeenCalledWith(
      "modrun_1",
      expect.objectContaining({
        expected_version: 4,
        current_scene_key: "地下室",
        active_spoiler_tags: ["act-1"],
        state: { clock: 1 }
      })
    ));
  });

  it("reloads current and warns the KP after a 409 conflict", async () => {
    const user = userEvent.setup();
    const refreshed = { ...run, current_scene_key: "钟楼", version: 5 };
    vi.mocked(getCurrentModuleRun)
      .mockResolvedValueOnce(run)
      .mockResolvedValueOnce(refreshed);
    vi.mocked(listModuleRuns)
      .mockResolvedValueOnce([run])
      .mockResolvedValueOnce([refreshed]);
    vi.mocked(updateModuleRun).mockRejectedValueOnce(
      new ApiError("Module run changed; refresh it before applying this update", 409, "conflict")
    );

    render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modules}
        selectedModuleId="mod_1"
      />
    );

    await screen.findByRole("heading", { name: "场景导演" });
    await user.click(screen.getByRole("button", { name: "暂停运行" }));

    expect(await screen.findByText(/已重新载入当前状态/)).toBeVisible();
    await editLegacyScene(user);
    expect(screen.getByLabelText("当前场景")).toHaveValue("钟楼");
    expect(getCurrentModuleRun).toHaveBeenCalledTimes(2);
  });

  it("keeps a dirty draft while refreshing the version after a conflict", async () => {
    const user = userEvent.setup();
    const refreshed = { ...run, current_scene_key: "钟楼", version: 5 };
    vi.mocked(getCurrentModuleRun)
      .mockResolvedValueOnce(run)
      .mockResolvedValueOnce(refreshed);
    vi.mocked(listModuleRuns)
      .mockResolvedValueOnce([run])
      .mockResolvedValueOnce([refreshed]);
    vi.mocked(updateModuleRun).mockRejectedValueOnce(
      new ApiError("Module run changed; refresh it before applying this update", 409, "conflict")
    );

    render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modules}
        selectedModuleId="mod_1"
      />
    );

    const scene = await editLegacyScene(user);
    await user.clear(scene);
    await user.type(scene, "地下室");
    await user.click(screen.getByRole("button", { name: "保存运行进度" }));

    expect(await screen.findByText(/保留本地草稿/)).toBeVisible();
    expect(screen.getByLabelText("当前场景")).toHaveValue("地下室");
    expect(screen.getByText("版本").closest("div")).toHaveTextContent("5");

    await user.click(screen.getByRole("button", { name: "保存运行进度" }));
    await waitFor(() => expect(updateModuleRun).toHaveBeenNthCalledWith(
      2,
      "modrun_1",
      expect.objectContaining({
        expected_version: 5,
        current_scene_key: "地下室"
      })
    ));
  });

  it("keeps a dirty draft when the KP manually refreshes", async () => {
    const user = userEvent.setup();
    const refreshed = { ...run, current_scene_key: "钟楼", version: 5 };
    vi.mocked(getCurrentModuleRun)
      .mockResolvedValueOnce(run)
      .mockResolvedValueOnce(refreshed);
    vi.mocked(listModuleRuns)
      .mockResolvedValueOnce([run])
      .mockResolvedValueOnce([refreshed]);

    render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modules}
        selectedModuleId="mod_1"
      />
    );

    const scene = await editLegacyScene(user);
    await user.clear(scene);
    await user.type(scene, "地下室");
    await user.click(screen.getByRole("button", { name: "刷新模组运行" }));

    expect(await screen.findByText(/本地未保存草稿已保留/)).toBeVisible();
    expect(screen.getByLabelText("当前场景")).toHaveValue("地下室");
    expect(screen.getByText("版本").closest("div")).toHaveTextContent("5");
  });

  it("detaches a dirty draft when the active run ended during conflict recovery", async () => {
    const user = userEvent.setup();
    const paused = { ...run, status: "paused" as const, version: 5 };
    vi.mocked(getCurrentModuleRun)
      .mockResolvedValueOnce(run)
      .mockResolvedValueOnce(null);
    vi.mocked(listModuleRuns)
      .mockResolvedValueOnce([run])
      .mockResolvedValueOnce([paused]);
    vi.mocked(updateModuleRun).mockRejectedValueOnce(
      new ApiError("Module run changed; refresh it before applying this update", 409, "conflict")
    );

    render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modules}
        selectedModuleId="mod_1"
      />
    );

    const scene = await editLegacyScene(user);
    await user.clear(scene);
    await user.type(scene, "地下室");
    await user.click(screen.getByRole("button", { name: "保存运行进度" }));

    expect(await screen.findByRole("region", { name: "已保留的冲突草稿" })).toBeVisible();
    expect(screen.getByText("地下室")).toBeVisible();
    expect(screen.getByLabelText("已保留草稿的运行状态 JSON")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("当前场景")).toHaveValue("");
  });

  it("does not treat an unrelated 409 as an optimistic concurrency conflict", async () => {
    const user = userEvent.setup();
    vi.mocked(updateModuleRun).mockRejectedValueOnce(
      new ApiError("数据库约束冲突", 409, "integrity_conflict")
    );

    render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modules}
        selectedModuleId="mod_1"
      />
    );

    await screen.findByRole("heading", { name: "场景导演" });
    await user.click(screen.getByRole("button", { name: "暂停运行" }));

    expect(await screen.findByText("数据库约束冲突")).toBeVisible();
    expect(getCurrentModuleRun).toHaveBeenCalledOnce();
  });

  it("detaches a dirty draft when a successful status change ends the active run", async () => {
    const user = userEvent.setup();
    const paused = { ...run, status: "paused" as const, version: 5 };
    vi.mocked(updateModuleRun).mockResolvedValueOnce(paused);
    vi.mocked(getCurrentModuleRun)
      .mockResolvedValueOnce(run)
      .mockResolvedValueOnce(null);
    vi.mocked(listModuleRuns)
      .mockResolvedValueOnce([run])
      .mockResolvedValueOnce([paused]);

    render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modules}
        selectedModuleId="mod_1"
      />
    );

    const scene = await editLegacyScene(user);
    await user.clear(scene);
    await user.type(scene, "地下室");
    await user.click(screen.getByRole("button", { name: "暂停运行" }));

    expect(await screen.findByText(/未保存草稿已移到只读保留区/)).toBeVisible();
    expect(screen.getByRole("region", { name: "已保留的冲突草稿" })).toHaveTextContent("地下室");
    expect(screen.getByLabelText("当前场景")).toHaveValue("");
  });

  it("gives each history restore button a descriptive accessible name", async () => {
    const paused = {
      ...run,
      id: "modrun_old",
      status: "paused" as const,
      version: 3
    };
    vi.mocked(listModuleRuns).mockResolvedValueOnce([run, paused]);

    render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modules}
        selectedModuleId="mod_1"
      />
    );

    const history = await screen.findByText("运行历史（2）");
    await userEvent.click(history);
    expect(screen.getByRole("button", {
      name: "恢复《雾港疑云》（开始于 2026-07-30 09:00:00）"
    })).toBeVisible();
  });

  it("binds a no-run draft to its module and never starts another module with it", async () => {
    const user = userEvent.setup();
    vi.mocked(listModuleRuns).mockResolvedValue([]);
    vi.mocked(getCurrentModuleRun).mockResolvedValue(null);

    const { rerender } = render(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modulesWithSecond}
        selectedModuleId="mod_1"
      />
    );
    await screen.findByText("当前没有活动模组，可从模组版本中选择并开始。");
    await user.type(screen.getByLabelText("当前场景"), "甲模组地下室");

    rerender(
      <ModuleRunPanel
        campaignId="camp_test"
        modules={modulesWithSecond}
        selectedModuleId="mod_2"
      />
    );

    expect(screen.getByLabelText("当前场景")).toHaveValue("");
    expect(screen.getByRole("region", { name: "已保留的冲突草稿" })).toHaveTextContent(
      "甲模组地下室"
    );
    await user.click(screen.getByRole("button", { name: "开始《钟楼余响》" }));

    await waitFor(() => expect(startModuleRun).toHaveBeenCalledWith(
      "camp_test",
      expect.objectContaining({
        module_id: "mod_2",
        current_scene_key: null,
        active_spoiler_tags: [],
        state: {}
      })
    ));
    expect(startModuleRun).not.toHaveBeenCalledWith(
      "camp_test",
      expect.objectContaining({ current_scene_key: "甲模组地下室" })
    );
  });
});

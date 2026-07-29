import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getCurrentModuleRun,
  listModuleRuns,
  requestBinary,
  requestBlob,
  requestJson,
  startModuleRun,
  updateModuleRun
} from "../../api/client";
import { ModuleLibraryPage } from "./ModuleLibraryPage";

vi.mock("../../api/client", () => ({
  getCurrentModuleRun: vi.fn(),
  listModuleRuns: vi.fn(),
  requestBinary: vi.fn(),
  requestBlob: vi.fn(),
  requestJson: vi.fn(),
  startModuleRun: vi.fn(),
  updateModuleRun: vi.fn()
}));

const campaign = {
  id: "camp_test",
  title: "雾港 1928",
  system: "coc7",
  current_time: null
};

const identity = {
  member_id: "member_kp",
  session_id: "session_test",
  campaign_id: "camp_test",
  role: "kp" as const,
  display_name: "OldOnes",
  pc_id: null
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("ModuleLibraryPage", () => {
  beforeEach(() => {
    vi.mocked(getCurrentModuleRun).mockResolvedValue(null);
    vi.mocked(listModuleRuns).mockResolvedValue([]);
    vi.mocked(startModuleRun).mockResolvedValue({} as never);
    vi.mocked(updateModuleRun).mockResolvedValue({} as never);
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url.startsWith("/module-analysis/capabilities")) {
        return {
          tesseract: { available: true, version: "tesseract 5", languages: ["eng"] },
          vision: { configured: true, model: "vision-local" }
        };
      }
      if (url.endsWith("/module-imports")) return [];
      if (url.endsWith("/modules")) {
        return [{
          id: "mod_1",
          campaign_id: "camp_test",
          title: "雾港疑云",
          source_type: "docx",
          source_filename: "mist.docx",
          source_hash: "a".repeat(64),
          parser_version: "module-document.v1",
          created_at: "2026-07-28"
        }];
      }
      if (url.includes("/chunks")) {
        return [{
          id: "chunk_1",
          module_id: "mod_1",
          title: "第一章",
          text: "码头仓库中藏着一张旧照片。",
          visibility: "kp",
          content_kind: "text",
          page_start: null,
          page_end: null,
          paragraph_start: 2,
          paragraph_end: 2,
          source_locator: "docx:paragraph:2",
          order_index: 0
        }];
      }
      if (url.endsWith("/assets")) return [];
      if (url.endsWith("/knowledge/candidates")) return [];
      if (url.endsWith("/entities")) return [];
      if (url.endsWith("/relations")) return [];
      return {};
    });
    vi.mocked(requestBlob).mockResolvedValue(new Blob());
    vi.mocked(requestBinary).mockResolvedValue({
      id: "modjob_1",
      campaign_id: "camp_test",
      title: "新模组",
      source_filename: "new.docx",
      source_type: "docx",
      source_hash: "b".repeat(64),
      status: "queued",
      stage: "queued",
      progress_current: 0,
      progress_total: 0,
      attempt_count: 0,
      error_text: null,
      module_id: null,
      created_at: "2026-07-28",
      updated_at: "2026-07-28"
    });
  });

  it("loads a KP-only module and starts a supported document import", async () => {
    const user = userEvent.setup();
    render(<ModuleLibraryPage campaign={campaign} identity={identity} />);

    expect(await screen.findByText("雾港疑云")).toBeVisible();
    expect(await screen.findByText("码头仓库中藏着一张旧照片。")).toBeVisible();

    const input = screen.getByLabelText(/选择 PDF、DOC 或 DOCX/);
    expect(input).toHaveAttribute("accept", expect.stringContaining(".doc,"));
    const file = new File(["docx"], "new.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    });
    await user.upload(input, file);

    await waitFor(() => expect(requestBinary).toHaveBeenCalled());
    expect(vi.mocked(requestBinary).mock.calls[0]?.[0]).toContain(
      "/campaigns/camp_test/module-imports"
    );
  });

  it("does not enable import without the current campaign KP identity", () => {
    render(<ModuleLibraryPage campaign={campaign} identity={null} />);

    expect(screen.getByLabelText(/选择 PDF、DOC 或 DOCX/)).toBeDisabled();
    expect(screen.getByText(/请先在“团与权限”页面/)).toBeVisible();
  });

  it("does not request or render module-run secrets for a player", () => {
    render(
      <ModuleLibraryPage
        campaign={campaign}
        identity={{ ...identity, member_id: "member_player", role: "player" }}
      />
    );

    expect(screen.queryByRole("heading", { name: "当前模组运行" })).not.toBeInTheDocument();
    expect(listModuleRuns).not.toHaveBeenCalled();
    expect(getCurrentModuleRun).not.toHaveBeenCalled();
    expect(requestJson).not.toHaveBeenCalled();
  });

  it("clears module content and ignores a search response from the previous module", async () => {
    const user = userEvent.setup();
    const oldSearch = deferred<Array<{
      source_type: "chunk";
      source_id: string;
      module_id: string;
      visibility: string;
      spoiler_tag: null;
      source_locator: string;
      title: string;
      text: string;
    }>>();
    const nextChunks = deferred<Array<Record<string, unknown>>>();
    const nextAssets = deferred<never[]>();
    const nextCandidates = deferred<never[]>();
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url.startsWith("/module-analysis/capabilities")) {
        return {
          tesseract: { available: true, version: "tesseract 5", languages: ["eng"] },
          vision: { configured: true, model: "vision-local" }
        };
      }
      if (url.endsWith("/module-imports")) return [];
      if (url === "/campaigns/camp_test/modules") {
        return [
          {
            id: "mod_1",
            campaign_id: "camp_test",
            title: "模组甲",
            source_type: "docx",
            source_filename: "a.docx",
            source_hash: "a".repeat(64),
            parser_version: "module-document.v1",
            created_at: "2026-07-28"
          },
          {
            id: "mod_2",
            campaign_id: "camp_test",
            title: "模组乙",
            source_type: "pdf",
            source_filename: "b.pdf",
            source_hash: "b".repeat(64),
            parser_version: "module-document.v1",
            created_at: "2026-07-29"
          }
        ];
      }
      if (url.startsWith("/modules/mod_1/search")) return oldSearch.promise;
      if (url === "/modules/mod_1/chunks?view=kp") {
        return [{
          id: "chunk_a",
          module_id: "mod_1",
          title: "甲章节",
          text: "只属于模组甲的秘密正文",
          visibility: "kp",
          content_kind: "text",
          page_start: null,
          page_end: null,
          paragraph_start: 1,
          paragraph_end: 1,
          source_locator: "docx:paragraph:1",
          order_index: 0
        }];
      }
      if (url === "/modules/mod_2/chunks?view=kp") return nextChunks.promise;
      if (url === "/modules/mod_2/assets") return nextAssets.promise;
      if (url === "/modules/mod_2/knowledge/candidates") return nextCandidates.promise;
      if (url.endsWith("/assets") || url.endsWith("/knowledge/candidates")) return [];
      if (url.endsWith("/entities") || url.endsWith("/relations")) return [];
      return {};
    });

    render(<ModuleLibraryPage campaign={campaign} identity={identity} />);
    expect(await screen.findByText("只属于模组甲的秘密正文")).toBeVisible();

    await user.type(
      screen.getByPlaceholderText("检索原文、图片 OCR 与已批准知识"),
      "旧模组查询"
    );
    await user.click(screen.getByRole("button", { name: /^检索$/ }));
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith(
      expect.stringContaining("/modules/mod_1/search?q=")
    ));

    await user.click(screen.getByRole("button", { name: /^模组乙/ }));
    expect(screen.queryByText("只属于模组甲的秘密正文")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("检索原文、图片 OCR 与已批准知识")).toHaveValue("");

    await act(async () => {
      oldSearch.resolve([{
        source_type: "chunk",
        source_id: "search_a",
        module_id: "mod_1",
        visibility: "kp",
        spoiler_tag: null,
        source_locator: "docx:paragraph:9",
        title: "甲搜索结果",
        text: "迟到的甲模组搜索秘密"
      }]);
      await Promise.resolve();
    });
    expect(screen.queryByText("迟到的甲模组搜索秘密")).not.toBeInTheDocument();

    await act(async () => {
      nextChunks.resolve([{
        id: "chunk_b",
        module_id: "mod_2",
        title: "乙章节",
        text: "只属于模组乙的正文",
        visibility: "kp",
        content_kind: "text",
        page_start: 2,
        page_end: 2,
        paragraph_start: null,
        paragraph_end: null,
        source_locator: "pdf:page:2",
        order_index: 0
      }]);
      nextAssets.resolve([]);
      nextCandidates.resolve([]);
      await Promise.resolve();
    });
    expect(await screen.findByText("只属于模组乙的正文")).toBeVisible();
  });

  it("does not let a late explicit refresh overwrite the newly selected module", async () => {
    const user = userEvent.setup();
    const oldChunks = deferred<Array<Record<string, unknown>>>();
    const oldAssets = deferred<never[]>();
    const oldCandidates = deferred<never[]>();
    let oldChunkLoads = 0;
    vi.mocked(requestJson).mockImplementation(async (url, options) => {
      if (url.startsWith("/module-analysis/capabilities")) {
        return {
          tesseract: { available: true, version: "tesseract 5", languages: ["eng"] },
          vision: { configured: true, model: "vision-local" }
        };
      }
      if (url.endsWith("/module-imports")) return [];
      if (url === "/campaigns/camp_test/modules") {
        return [
          {
            id: "mod_1",
            campaign_id: "camp_test",
            title: "模组甲",
            source_type: "docx",
            source_filename: "a.docx",
            source_hash: "a".repeat(64),
            parser_version: "module-document.v1",
            created_at: "2026-07-28"
          },
          {
            id: "mod_2",
            campaign_id: "camp_test",
            title: "模组乙",
            source_type: "pdf",
            source_filename: "b.pdf",
            source_hash: "b".repeat(64),
            parser_version: "module-document.v1",
            created_at: "2026-07-29"
          }
        ];
      }
      if (url === "/modules/mod_1/knowledge/extract?limit=5" && options?.method === "POST") {
        return { processed_count: 1, accepted_count: 1 };
      }
      if (url === "/modules/mod_1/chunks?view=kp") {
        oldChunkLoads += 1;
        if (oldChunkLoads > 1) return oldChunks.promise;
        return [{
          id: "chunk_a",
          module_id: "mod_1",
          title: "甲章节",
          text: "甲初始正文",
          visibility: "kp",
          content_kind: "text",
          source_locator: "docx:paragraph:1",
          order_index: 0
        }];
      }
      if (url === "/modules/mod_1/assets") {
        return oldChunkLoads > 1 ? oldAssets.promise : [];
      }
      if (url === "/modules/mod_1/knowledge/candidates") {
        return oldChunkLoads > 1 ? oldCandidates.promise : [];
      }
      if (url === "/modules/mod_2/chunks?view=kp") {
        return [{
          id: "chunk_b",
          module_id: "mod_2",
          title: "乙章节",
          text: "乙当前正文",
          visibility: "kp",
          content_kind: "text",
          source_locator: "pdf:page:1",
          order_index: 0
        }];
      }
      if (url.endsWith("/assets") || url.endsWith("/knowledge/candidates")) return [];
      if (url.endsWith("/entities") || url.endsWith("/relations")) return [];
      return {};
    });

    render(<ModuleLibraryPage campaign={campaign} identity={identity} />);
    expect(await screen.findByText("甲初始正文")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /提取 5 块/ }));
    await waitFor(() => expect(oldChunkLoads).toBe(2));

    await user.click(screen.getByRole("button", { name: /^模组乙/ }));
    expect(await screen.findByText("乙当前正文")).toBeVisible();

    await act(async () => {
      oldChunks.resolve([{
        id: "chunk_a_late",
        module_id: "mod_1",
        title: "甲章节",
        text: "迟到的甲刷新正文",
        visibility: "kp",
        content_kind: "text",
        source_locator: "docx:paragraph:2",
        order_index: 1
      }]);
      oldAssets.resolve([]);
      oldCandidates.resolve([]);
      await Promise.resolve();
    });
    expect(screen.queryByText("迟到的甲刷新正文")).not.toBeInTheDocument();
    expect(screen.getByText("乙当前正文")).toBeVisible();
  });
});

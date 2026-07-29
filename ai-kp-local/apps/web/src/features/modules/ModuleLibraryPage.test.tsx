import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestBinary, requestBlob, requestJson } from "../../api/client";
import { ModuleLibraryPage } from "./ModuleLibraryPage";

vi.mock("../../api/client", () => ({
  requestBinary: vi.fn(),
  requestBlob: vi.fn(),
  requestJson: vi.fn()
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

describe("ModuleLibraryPage", () => {
  beforeEach(() => {
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

  it("loads a KP-only module and starts a DOCX import", async () => {
    const user = userEvent.setup();
    render(<ModuleLibraryPage campaign={campaign} identity={identity} />);

    expect(await screen.findByText("雾港疑云")).toBeVisible();
    expect(await screen.findByText("码头仓库中藏着一张旧照片。")).toBeVisible();

    const file = new File(["docx"], "new.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    });
    await user.upload(screen.getByLabelText(/选择 PDF 或 DOCX/), file);

    await waitFor(() => expect(requestBinary).toHaveBeenCalled());
    expect(vi.mocked(requestBinary).mock.calls[0]?.[0]).toContain(
      "/campaigns/camp_test/module-imports"
    );
  });

  it("does not enable import without the current campaign KP identity", () => {
    render(<ModuleLibraryPage campaign={campaign} identity={null} />);

    expect(screen.getByLabelText(/选择 PDF 或 DOCX/)).toBeDisabled();
    expect(screen.getByText(/请先在“团与权限”页面/)).toBeVisible();
  });
});

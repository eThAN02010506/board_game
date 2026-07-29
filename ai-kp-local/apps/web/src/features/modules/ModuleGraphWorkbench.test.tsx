import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { requestJson } from "../../api/client";
import { ModuleGraphWorkbench } from "./ModuleGraphWorkbench";

vi.mock("../../api/client", () => ({
  requestJson: vi.fn()
}));

const candidate = {
  id: "modknow_anchor",
  module_id: "mod_1",
  kind: "module_anchor" as const,
  title: "航海日志",
  statement: "仓库照片揭示航海日志。",
  rationale: "",
  confidence: 1,
  visibility: "kp",
  spoiler_tag: null,
  status: "approved" as const,
  review_note: null,
  citations: []
};

describe("ModuleGraphWorkbench", () => {
  it("loads graph evidence and runs a deterministic reachability check", async () => {
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url.endsWith("/entities")) {
        return [
          {
            id: "entry",
            module_id: "mod_1",
            entity_type: "location",
            name: "仓库",
            description: "",
            visibility: "kp",
            spoiler_tag: null,
            source_candidate_id: candidate.id
          },
          {
            id: "anchor",
            module_id: "mod_1",
            entity_type: "anchor",
            name: "航海日志",
            description: "",
            visibility: "kp",
            spoiler_tag: null,
            source_candidate_id: candidate.id
          }
        ];
      }
      if (url.endsWith("/relations")) return [];
      if (url.endsWith("/graph/reachability")) {
        return {
          module_id: "mod_1",
          entry_entity_ids: ["entry"],
          reached_entity_ids: ["entry", "anchor"],
          anchors: [
            {
              id: "anchor",
              module_id: "mod_1",
              entity_type: "anchor",
              name: "航海日志",
              description: "",
              visibility: "kp",
              spoiler_tag: null,
              source_candidate_id: candidate.id,
              reachable: true
            }
          ],
          all_anchors_reachable: true,
          has_conflicts: false,
          safe: true,
          conflicts: []
        };
      }
      return {};
    });
    const user = userEvent.setup();
    render(
      <ModuleGraphWorkbench
        candidates={[candidate]}
        moduleId="mod_1"
        onMessage={vi.fn()}
      />
    );

    const checkboxes = await screen.findAllByRole("checkbox");
    await user.click(checkboxes[0]);
    await user.click(screen.getByRole("button", { name: /检查剧情锚点/ }));

    expect(await screen.findByText("所有剧情锚点可达且没有显式冲突")).toBeVisible();
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith(
      "/modules/mod_1/graph/reachability",
      expect.objectContaining({ method: "POST" })
    ));
  });

  it("requires approved knowledge before graph authoring", async () => {
    vi.mocked(requestJson).mockImplementation(async () => []);
    render(
      <ModuleGraphWorkbench
        candidates={[]}
        moduleId="mod_empty"
        onMessage={vi.fn()}
      />
    );

    expect(await screen.findByText(/先在上方批准至少一条/)).toBeVisible();
    expect(screen.getByRole("button", { name: "保存实体" })).toBeDisabled();
  });
});

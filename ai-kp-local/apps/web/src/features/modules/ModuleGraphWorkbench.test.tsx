import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("ModuleGraphWorkbench", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

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

  it("ignores a late graph load after switching modules", async () => {
    const oldEntities = deferred<Array<Record<string, unknown>>>();
    const oldRelations = deferred<never[]>();
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/modules/mod_1/entities") return oldEntities.promise;
      if (url === "/modules/mod_1/relations") return oldRelations.promise;
      if (url === "/modules/mod_2/entities") {
        return [{
          id: "entity_b",
          module_id: "mod_2",
          entity_type: "location",
          name: "乙模组警察局",
          description: "",
          visibility: "kp",
          spoiler_tag: null,
          source_candidate_id: candidate.id
        }];
      }
      if (url === "/modules/mod_2/relations") return [];
      return {};
    });

    const { rerender } = render(
      <ModuleGraphWorkbench
        candidates={[candidate]}
        moduleId="mod_1"
        onMessage={vi.fn()}
      />
    );
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith("/modules/mod_1/entities"));

    rerender(
      <ModuleGraphWorkbench
        candidates={[{ ...candidate, module_id: "mod_2" }]}
        moduleId="mod_2"
        onMessage={vi.fn()}
      />
    );
    expect((await screen.findAllByText("乙模组警察局")).length).toBeGreaterThan(0);

    await act(async () => {
      oldEntities.resolve([{
        id: "entity_a_late",
        module_id: "mod_1",
        entity_type: "location",
        name: "迟到的甲模组仓库",
        description: "",
        visibility: "kp",
        spoiler_tag: null,
        source_candidate_id: candidate.id
      }]);
      oldRelations.resolve([]);
      await Promise.resolve();
    });
    expect(screen.queryByText("迟到的甲模组仓库")).not.toBeInTheDocument();
    expect(screen.getAllByText("乙模组警察局").length).toBeGreaterThan(0);
  });

  it("clears graph and report and ignores late reachability after a module switch", async () => {
    const user = userEvent.setup();
    const lateReport = deferred<Record<string, unknown>>();
    let reachabilityCalls = 0;
    const safeReport = {
      module_id: "mod_1",
      entry_entity_ids: ["entry"],
      reached_entity_ids: ["entry", "anchor"],
      anchors: [{
        id: "anchor",
        module_id: "mod_1",
        entity_type: "anchor",
        name: "甲剧情锚点",
        description: "",
        visibility: "kp",
        spoiler_tag: null,
        source_candidate_id: candidate.id,
        reachable: true
      }],
      all_anchors_reachable: true,
      has_conflicts: false,
      safe: true,
      conflicts: []
    };
    vi.mocked(requestJson).mockImplementation(async (url) => {
      if (url === "/modules/mod_1/entities") {
        return [{
          id: "entry",
          module_id: "mod_1",
          entity_type: "location",
          name: "甲入口",
          description: "",
          visibility: "kp",
          spoiler_tag: null,
          source_candidate_id: candidate.id
        }];
      }
      if (url === "/modules/mod_1/relations") return [];
      if (url === "/modules/mod_1/graph/reachability") {
        reachabilityCalls += 1;
        return reachabilityCalls === 1 ? safeReport : lateReport.promise;
      }
      if (url === "/modules/mod_2/entities" || url === "/modules/mod_2/relations") return [];
      return {};
    });

    const { rerender } = render(
      <ModuleGraphWorkbench
        candidates={[candidate]}
        moduleId="mod_1"
        onMessage={vi.fn()}
      />
    );
    await user.click((await screen.findAllByRole("checkbox"))[0]);
    await user.click(screen.getByRole("button", { name: /检查剧情锚点/ }));
    expect(await screen.findByText("所有剧情锚点可达且没有显式冲突")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /检查剧情锚点/ }));
    await waitFor(() => expect(reachabilityCalls).toBe(2));
    rerender(
      <ModuleGraphWorkbench
        candidates={[{ ...candidate, module_id: "mod_2" }]}
        moduleId="mod_2"
        onMessage={vi.fn()}
      />
    );
    expect(screen.queryByText("甲入口")).not.toBeInTheDocument();
    expect(screen.queryByText("所有剧情锚点可达且没有显式冲突")).not.toBeInTheDocument();

    await act(async () => {
      lateReport.resolve(safeReport);
      await Promise.resolve();
    });
    expect(screen.queryByText("所有剧情锚点可达且没有显式冲突")).not.toBeInTheDocument();
  });
});

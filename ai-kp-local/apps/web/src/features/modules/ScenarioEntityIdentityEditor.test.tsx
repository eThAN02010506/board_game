import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { compileScenarioContract, requestJson } from "../../api/client";
import type { ScenarioContractVersion } from "../../api/types";
import { ScenarioEntityIdentityEditor } from "./ScenarioEntityIdentityEditor";

vi.mock("../../api/client", () => ({ compileScenarioContract: vi.fn(), requestJson: vi.fn() }));

const version = {
  id: "v1", module_id: "module/one",
  contract: {
    contract_id: "c1", entities: [{ entity_id: "log", title: "日志", entity_type: "item", source_refs: [{ source_block_id: "block" }] }],
    operators: [{ operator_id: "read", success_commands: [{ kind: "set_fact", path: "read", value: true }] }]
  }
} as unknown as ScenarioContractVersion;

describe("ScenarioEntityIdentityEditor", () => {
  beforeEach(() => vi.resetAllMocks());

  it("requires explicit identity selection and preserves the rest of the contract", async () => {
    vi.mocked(requestJson).mockResolvedValue([
      { id: "source-a", name: "日志", entity_type: "item" },
      { id: "source-b", name: "日志", entity_type: "item" }
    ]);
    vi.mocked(compileScenarioContract).mockResolvedValue({ version: { id: "v2" } } as never);
    const onSaved = vi.fn().mockResolvedValue(undefined);
    render(<ScenarioEntityIdentityEditor version={version} disabled={false} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole("button", { name: "关联模组原文实体" }));
    const select = await screen.findByRole("combobox", { name: "日志 原文身份" });
    expect(select).toHaveValue("");
    fireEvent.change(select, { target: { value: "source-b" } });
    fireEvent.click(screen.getByRole("button", { name: "保存身份关联草稿" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(requestJson).toHaveBeenCalledWith("/modules/module%2Fone/entities");
    expect(compileScenarioContract).toHaveBeenCalledWith("module/one", {
      ...version.contract,
      entities: [{ ...version.contract.entities[0], module_entity_id: "source-b" }]
    });
    expect(version.contract.entities[0].module_entity_id).toBeUndefined();
  });

  it("blocks edits while a run is bound", () => {
    render(<ScenarioEntityIdentityEditor version={version} disabled onSaved={vi.fn()} />);
    expect(screen.getByRole("button", { name: "关联模组原文实体" })).toBeDisabled();
    expect(requestJson).not.toHaveBeenCalled();
  });
});

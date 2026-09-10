import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "../../api/client";
import { ModelSettingsPage } from "./ModelSettingsPage";

vi.mock("../../api/client", () => ({ requestJson: vi.fn() }));

const requestJsonMock = vi.mocked(requestJson);
const settings = {
  provider_type: "openai_compatible",
  base_url: "http://192.168.1.97:8001/v1",
  model: "gpt-oss-20b",
  local_model_path: null,
  local_port: 8011,
  semantic_profile: "small",
  api_key_configured: false,
  persisted: true,
  version: 1,
  runtime: {
    state: "stopped",
    available: false,
    pid: null,
    return_code: null,
    model_path: null,
    port: null,
    log_path: ""
  }
};

describe("ModelSettingsPage", () => {
  beforeEach(() => {
    requestJsonMock.mockReset();
  });

  it("persists an explicit large-model semantic capability profile", async () => {
    requestJsonMock.mockImplementation(async (url, init) => {
      if (url === "/model-settings" && init?.method === "PUT") {
        return { ...settings, semantic_profile: "large" };
      }
      if (url === "/model-settings") return settings;
      if (url === "/image-model-settings") {
        return {
          base_url: null,
          model: "",
          timeout_seconds: 300,
          api_key_configured: false,
          persisted: false
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<ModelSettingsPage />);

    expect(await screen.findByText(/当前执行世代：v1/)).toBeTruthy();

    const profile = await screen.findByRole("combobox", {
      name: /语义能力档位/
    });
    fireEvent.change(profile, { target: { value: "large" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并使用" }));

    await waitFor(() => {
      const call = requestJsonMock.mock.calls.find(
        ([url, init]) => url === "/model-settings" && init?.method === "PUT"
      );
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        semantic_profile: "large"
      });
    });
  });
});

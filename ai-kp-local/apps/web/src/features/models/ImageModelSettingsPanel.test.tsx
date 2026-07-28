import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "../../api/client";
import { ImageModelSettingsPanel } from "./ImageModelSettingsPanel";

vi.mock("../../api/client", () => ({
  requestJson: vi.fn()
}));

const requestJsonMock = vi.mocked(requestJson);

describe("ImageModelSettingsPanel", () => {
  beforeEach(() => {
    requestJsonMock.mockReset();
  });

  it("loads a redacted persisted image-model configuration", async () => {
    requestJsonMock.mockResolvedValueOnce({
      base_url: "http://127.0.0.1:8188/v1",
      model: "flux-period-map",
      timeout_seconds: 420,
      api_key_configured: true,
      persisted: true
    });

    render(<ImageModelSettingsPanel />);

    expect(await screen.findByText("已读取本地保存的图片模型设置。")).toBeVisible();
    expect(screen.getByPlaceholderText("http://127.0.0.1:8188/v1")).toHaveValue(
      "http://127.0.0.1:8188/v1"
    );
    expect(screen.getByLabelText("图片模型 ID")).toHaveValue("flux-period-map");
    expect(screen.getByLabelText("API Key")).toHaveAttribute(
      "placeholder",
      "已保存；留空表示保持不变"
    );
  });

  it("discovers actual model IDs and saves the selected image provider", async () => {
    requestJsonMock
      .mockResolvedValueOnce({
        base_url: null,
        model: "",
        timeout_seconds: 300,
        api_key_configured: false,
        persisted: false
      })
      .mockResolvedValueOnce({
        models: ["flux-period-map", "sdxl"],
        normalized_base_url: "http://127.0.0.1:8188/v1"
      })
      .mockResolvedValueOnce({
        base_url: "http://127.0.0.1:8188/v1",
        model: "flux-period-map",
        timeout_seconds: 300,
        api_key_configured: false,
        persisted: true
      });

    render(<ImageModelSettingsPanel />);
    await screen.findByText("尚未单独配置图片模型；地图会继续使用确定性 SVG。");

    fireEvent.click(screen.getByRole("button", { name: "检测图片服务模型" }));
    expect(await screen.findByRole("button", { name: "flux-period-map" })).toBeVisible();
    expect(screen.getByLabelText("图片模型 ID")).toHaveValue("flux-period-map");

    fireEvent.click(screen.getByRole("button", { name: "保存图片模型" }));

    await waitFor(() => {
      expect(requestJsonMock).toHaveBeenCalledWith(
        "/image-model-settings",
        expect.objectContaining({ method: "PUT" })
      );
    });
    const saveCall = requestJsonMock.mock.calls.find(
      ([url, init]) => url === "/image-model-settings" && init?.method === "PUT"
    );
    expect(saveCall).toBeDefined();
    expect(JSON.parse(String(saveCall?.[1]?.body))).toMatchObject({
      base_url: "http://127.0.0.1:8188/v1",
      model: "flux-period-map",
      timeout_seconds: 300
    });
    expect(await screen.findByText(
      "图片模型设置已保存；重新打开地图即可生成背景候选。"
    )).toBeVisible();
  });
});

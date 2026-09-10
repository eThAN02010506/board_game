// @vitest-environment node

import { afterEach, describe, expect, test, vi } from "vitest";
import { createOpenAiCompatiblePlayerDriverModel } from "./adaptivePlayerDriver";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createOpenAiCompatiblePlayerDriverModel", () => {
  test("uses bounded GPT-OSS JSON mode without inventing authorization", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const headers = init?.headers as Record<string, string>;
      expect(headers.Authorization).toBeUndefined();
      const body = JSON.parse(String(init?.body));
      expect(body).toMatchObject({
        model: "local-gpt-oss-20b",
        max_tokens: 1_024,
        temperature: 1,
        top_p: 1,
        response_format: { type: "json_object" },
        chat_template_kwargs: { reasoning_effort: "low" }
      });
      return new Response(JSON.stringify({
        choices: [{ message: { content: '{"action":"查看门锁"}' } }]
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const model = createOpenAiCompatiblePlayerDriverModel({
      baseUrl: "http://192.168.1.97:8001/v1",
      apiKey: "",
      modelId: "local-gpt-oss-20b"
    });

    await expect(model({
      system: "Return JSON.",
      user: "Choose one action.",
      maxOutputCharacters: 1_600
    })).resolves.toBe('{"action":"查看门锁"}');
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

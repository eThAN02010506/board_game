import { describe, expect, it } from "vitest";

import {
  isPageAllowedForRole,
  pageFromPath,
  resolveWorkspaceRoute,
  routeByPage
} from "./router";

describe("workspace routing", () => {
  it("maps unknown paths to the play workspace", () => {
    expect(pageFromPath("/unknown")).toBe("play");
  });

  it("resolves known pages to their route metadata", () => {
    expect(routeByPage("models")).toMatchObject({
      id: "models",
      path: "/models"
    });
  });

  it("keeps KP and unauthenticated users on the requested workspace", () => {
    expect(resolveWorkspaceRoute("models", "kp").id).toBe("models");
    expect(resolveWorkspaceRoute("models", null).id).toBe("models");
  });

  it("redirects players away from KP-only workspaces", () => {
    expect(isPageAllowedForRole("models", "player")).toBe(false);
    expect(resolveWorkspaceRoute("models", "player").id).toBe("play");
  });

  it("allows players to use table-safe workspaces", () => {
    expect(isPageAllowedForRole("maps", "player")).toBe(true);
    expect(resolveWorkspaceRoute("maps", "player").id).toBe("maps");
  });
});

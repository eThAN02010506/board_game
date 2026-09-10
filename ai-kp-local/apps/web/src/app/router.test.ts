import { describe, expect, it } from "vitest";

import {
  isPageAllowedForRole,
  pageFromPath,
  resolveWorkspaceRoute,
  routeByPage,
  visibleWorkspaceRoutes
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

  it("keeps KP on the requested workspace and limits guests to onboarding", () => {
    expect(resolveWorkspaceRoute("models", "kp").id).toBe("models");
    expect(resolveWorkspaceRoute("models", null).id).toBe("campaigns");
    expect(visibleWorkspaceRoutes(null).map((route) => route.id)).toEqual(["campaigns"]);
  });

  it("gives observers only the explicit read-only workspaces", () => {
    expect(visibleWorkspaceRoutes("observer").map((route) => route.id)).toEqual([
      "play",
      "campaigns",
      "handouts"
    ]);
    expect(resolveWorkspaceRoute("maps", "observer").id).toBe("play");
    expect(resolveWorkspaceRoute("models", "observer").id).toBe("play");
  });

  it("redirects players away from KP-only workspaces", () => {
    expect(isPageAllowedForRole("models", "player")).toBe(false);
    expect(routeByPage("models").access).toBe("kp");
    expect(resolveWorkspaceRoute("models", "player").id).toBe("play");
  });

  it("allows players to use table-safe workspaces", () => {
    expect(isPageAllowedForRole("maps", "player")).toBe(true);
    expect(routeByPage("maps").access).toBe("shared");
    expect(resolveWorkspaceRoute("maps", "player").id).toBe("maps");
  });

  it("derives player navigation from route access metadata", () => {
    expect(visibleWorkspaceRoutes("player").map((route) => route.id)).toEqual([
      "play",
      "campaigns",
      "investigators",
      "maps",
      "handouts"
    ]);
  });
});

// @vitest-environment node

import { mkdtemp, stat } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { ManagedBackendProcess, redactManagedBackendLog } from "./backendProcess";

const projectRoot = path.resolve(import.meta.dirname, "../../../..");
const managers: ManagedBackendProcess[] = [];

afterEach(async () => {
  await Promise.all(managers.map((manager) => manager.cleanup()));
  managers.length = 0;
});

describe("ManagedBackendProcess", () => {
  test(
    "owns uvicorn, proves restart identity, preserves the absolute SQLite store, and cleans up idempotently",
    async () => {
      const port = await unusedPort();
      const temporary = await mkdtemp(path.join(os.tmpdir(), "ai-kp-managed-backend-"));
      const databasePath = path.join(temporary, "state.sqlite3");
      const manager = new ManagedBackendProcess({
        port,
        databasePath,
        projectRoot,
        startTimeoutMs: 30_000,
        stopTimeoutMs: 10_000
      });
      managers.push(manager);

      expect(path.isAbsolute(manager.databasePath)).toBe(true);
      expect(manager.executable).toBe(path.join(projectRoot, ".venv", "bin", "python"));
      expect(manager.argv).toEqual([
        "-m",
        "uvicorn",
        "ai_kp.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        String(port)
      ]);
      expect(manager.shell).toBe(false);
      const first = await manager.start();
      expect(first.os_pid).toBe(manager.pid);
      await expect(fetch(`${manager.backendUrl}/debug/runtime-identity`)).resolves.toMatchObject({
        status: 403
      });
      await expect(stat(manager.databasePath)).resolves.toMatchObject({});

      const restart = await manager.restart();
      expect(restart.before.process_instance_id).toBe(first.process_instance_id);
      expect(restart.after.process_instance_id).not.toBe(first.process_instance_id);
      expect(restart.after.os_pid).toBe(manager.pid);
      expect(restart.after.persistent_store_id).toBe(first.persistent_store_id);
      expect(restart.stop.clean).toBe(true);
      expect(Date.parse(restart.started_at)).toBeGreaterThanOrEqual(
        Date.parse(restart.stopped_at)
      );

      const stopped = await manager.stop();
      expect(stopped).toMatchObject({ clean: true, forced: false, alreadyStopped: false });
      await expect(fetch(`${manager.backendUrl}/health`)).rejects.toThrow();
      await expect(manager.cleanup()).resolves.toMatchObject({
        clean: true,
        forced: false,
        alreadyStopped: true
      });
    },
    60_000
  );

  test("fails closed without spawning when the requested port is occupied", async () => {
    const blocker = net.createServer();
    await listen(blocker);
    const address = blocker.address();
    if (!address || typeof address === "string") throw new Error("Expected TCP address");
    const manager = new ManagedBackendProcess({
      port: address.port,
      databasePath: path.join(os.tmpdir(), "must-not-be-created.sqlite3"),
      projectRoot,
      startTimeoutMs: 1_000
    });
    managers.push(manager);

    try {
      await expect(manager.start()).rejects.toThrow(/already occupied.*refusing to reuse/i);
      expect(manager.pid).toBeNull();
    } finally {
      await new Promise<void>((resolve, reject) =>
        blocker.close((error) => (error ? reject(error) : resolve()))
      );
    }
  });

  test("fails closed on a relative SQLite path", async () => {
    expect(
      () =>
        new ManagedBackendProcess({
          port: 18_765,
          databasePath: ".playwright/relative.sqlite3",
          projectRoot
        })
    ).toThrow(/databasePath must be absolute/);
  });

  test("redacts configured and recognizable credentials from diagnostic logs", () => {
    const secret = "super-secret-admin-token";
    const log = [
      `admin=${secret}`,
      "api_key=another-sensitive-value",
      "Authorization: Bearer-value",
      "https://username:password@example.invalid/path"
    ].join("\n");

    const redacted = redactManagedBackendLog(log, [secret]);
    expect(redacted).not.toContain(secret);
    expect(redacted).not.toContain("another-sensitive-value");
    expect(redacted).not.toContain("Bearer-value");
    expect(redacted).not.toContain("username:password");
  });

  test(
    "strongly stops after the graceful deadline and reports clean=false",
    async () => {
      const port = await unusedPort();
      const temporary = await mkdtemp(path.join(os.tmpdir(), "ai-kp-forced-backend-"));
      const manager = new ManagedBackendProcess({
        port,
        databasePath: path.join(temporary, "state.sqlite3"),
        projectRoot,
        startTimeoutMs: 30_000,
        stopTimeoutMs: 0
      });
      managers.push(manager);
      await manager.start();

      await expect(manager.stop()).resolves.toMatchObject({
        clean: false,
        forced: true,
        alreadyStopped: false
      });
      await expect(fetch(`${manager.backendUrl}/health`)).rejects.toThrow();
    },
    45_000
  );
});

async function unusedPort(): Promise<number> {
  const server = net.createServer();
  await listen(server);
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Expected TCP address");
  const { port } = address;
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve()))
  );
  return port;
}

function listen(server: net.Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
}

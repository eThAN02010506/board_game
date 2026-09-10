import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdir } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";

const LOOPBACK_HOST = "127.0.0.1";
const DEFAULT_START_TIMEOUT_MS = 120_000;
const DEFAULT_STOP_TIMEOUT_MS = 10_000;
const FORCE_STOP_TIMEOUT_MS = 5_000;
const POLL_INTERVAL_MS = 50;
const MAX_LOG_CHARS = 16_000;

export interface BackendRuntimeIdentity {
  process_instance_id: string;
  os_pid: number;
  persistent_store_id: string;
  schema_version: number;
  status: "ready";
}

export interface ManagedBackendOptions {
  port: number;
  databasePath: string;
  projectRoot?: string;
  frontendUrl?: string;
  startTimeoutMs?: number;
  stopTimeoutMs?: number;
}

export interface BackendStopResult {
  clean: boolean;
  forced: boolean;
  alreadyStopped: boolean;
  exitCode: number | null;
  signal: NodeJS.Signals | null;
}

export interface BackendRestartResult {
  before: BackendRuntimeIdentity;
  after: BackendRuntimeIdentity;
  stop: BackendStopResult;
  stopped_at: string;
  started_at: string;
}

interface ExitResult {
  code: number | null;
  signal: NodeJS.Signals | null;
}

interface RunningBackend {
  child: ChildProcessWithoutNullStreams;
  exit: Promise<ExitResult>;
  identity: BackendRuntimeIdentity | null;
}

/**
 * Owns exactly one uvicorn process for restart-sensitive Playwright evidence.
 *
 * This intentionally does not accept a command or argv override. A test must
 * not turn the harness into a general-purpose shell execution surface.
 */
export class ManagedBackendProcess {
  readonly port: number;
  readonly databasePath: string;
  readonly projectRoot: string;
  readonly backendUrl: string;
  readonly executable: string;
  readonly argv: readonly string[];
  readonly shell = false;

  private readonly frontendUrl: string;
  private readonly adminToken: string;
  private readonly sensitiveEnvironmentValues: readonly string[];
  private readonly startTimeoutMs: number;
  private readonly stopTimeoutMs: number;
  private running: RunningBackend | null = null;
  private lastStop: BackendStopResult | null = null;
  private logTail = "";

  constructor(options: ManagedBackendOptions) {
    if (!Number.isInteger(options.port) || options.port < 1 || options.port > 65_535) {
      throw new Error(`Managed backend port must be an integer from 1 to 65535; received ${options.port}`);
    }
    if (!path.isAbsolute(options.databasePath)) {
      throw new Error("Managed backend databasePath must be absolute");
    }
    const defaultProjectRoot = fileURLToPath(new URL("../../../..", import.meta.url));
    this.projectRoot = path.resolve(options.projectRoot ?? defaultProjectRoot);
    this.databasePath = path.normalize(options.databasePath);
    this.port = options.port;
    this.backendUrl = `http://${LOOPBACK_HOST}:${this.port}`;
    this.executable = path.join(this.projectRoot, ".venv", "bin", "python");
    this.argv = Object.freeze([
      "-m",
      "uvicorn",
      "ai_kp.api.main:app",
      "--host",
      LOOPBACK_HOST,
      "--port",
      String(this.port)
    ]);
    this.frontendUrl = options.frontendUrl ?? "http://127.0.0.1:5174";
    this.adminToken = randomBytes(32).toString("hex");
    this.sensitiveEnvironmentValues = Object.entries(process.env)
      .filter(([name, value]) => value && /(?:api.?key|token|secret|password|credential)/i.test(name))
      .map(([, value]) => value as string)
      .filter((value) => value.length >= 6);
    this.startTimeoutMs = positiveTimeout(options.startTimeoutMs, DEFAULT_START_TIMEOUT_MS, "startTimeoutMs");
    this.stopTimeoutMs = positiveTimeout(options.stopTimeoutMs, DEFAULT_STOP_TIMEOUT_MS, "stopTimeoutMs");
  }

  get identity(): BackendRuntimeIdentity | null {
    return this.running?.identity ?? null;
  }

  get pid(): number | null {
    return this.running?.child.pid ?? null;
  }

  /**
   * Grants one browser page administrator access through the same visible
   * control used by a local KP. The generated credential remains encapsulated
   * by the managed process and is never returned to the test or its evidence.
   */
  async authorizeAdministratorPage(page: Page): Promise<void> {
    if (!this.running?.identity) {
      throw new Error("Managed backend must be ready before authorizing an administrator page");
    }
    const adminInput = page.getByLabel("管理员口令（LAN/HTTPS 部署时使用）");
    const visible = await adminInput.waitFor({ state: "visible", timeout: 30_000 })
      .then(() => true)
      .catch(() => false);
    if (!visible) {
      // A cold Vite load can render the route shell before the selected panel.
      // Reload once before involving the credential in an input action, so a
      // visibility timeout cannot echo the generated value into diagnostics.
      await page.reload();
      await adminInput.waitFor({ state: "visible", timeout: 30_000 });
    }
    await adminInput.fill(this.adminToken, { timeout: 5_000 });
    await page.getByRole("button", { name: "应用/清除管理员口令" }).click();
  }

  async start(): Promise<BackendRuntimeIdentity> {
    if (this.running) {
      throw new Error("Managed backend is already started");
    }
    await assertPortAvailable(this.port);
    await mkdir(path.dirname(this.databasePath), { recursive: true });

    const child = spawn(this.executable, this.argv, {
      cwd: this.projectRoot,
      env: this.backendEnvironment(),
      shell: this.shell,
      stdio: ["ignore", "pipe", "pipe"]
    });
    if (!child.pid) {
      child.kill("SIGKILL");
      throw new Error("Managed backend spawn returned no child PID");
    }

    this.logTail = "";
    child.stdout.on("data", (chunk: Buffer) => this.appendLog(chunk));
    child.stderr.on("data", (chunk: Buffer) => this.appendLog(chunk));
    const exit = new Promise<ExitResult>((resolve) => {
      child.once("exit", (code, signal) => resolve({ code, signal }));
    });
    this.running = { child, exit, identity: null };
    this.lastStop = null;

    try {
      const identity = await this.waitForOwnedIdentity(child, exit);
      this.running.identity = identity;
      return identity;
    } catch (error) {
      await this.forceDiscardChild(child, exit);
      const detail = this.logTail.trim();
      throw new Error(
        `Managed backend failed to start${detail ? `\nuvicorn output:\n${detail}` : ""}`,
        { cause: error }
      );
    }
  }

  async restart(): Promise<BackendRestartResult> {
    const before = this.running?.identity;
    if (!before) {
      throw new Error("Managed backend must be ready before restart");
    }
    const stop = await this.stop();
    const stoppedAt = new Date().toISOString();
    const after = await this.start();
    const startedAt = new Date().toISOString();
    if (after.process_instance_id === before.process_instance_id) {
      await this.stop();
      throw new Error("Backend restart reused the previous process instance identity");
    }
    if (after.persistent_store_id !== before.persistent_store_id) {
      await this.stop();
      throw new Error("Backend restart changed the persistent SQLite store identity");
    }
    return {
      before,
      after,
      stop,
      stopped_at: stoppedAt,
      started_at: startedAt
    };
  }

  async stop(): Promise<BackendStopResult> {
    const running = this.running;
    if (!running) {
      return this.lastStop
        ? { ...this.lastStop, alreadyStopped: true }
        : {
            clean: true,
            forced: false,
            alreadyStopped: true,
            exitCode: null,
            signal: null
          };
    }

    running.child.kill("SIGTERM");
    let forced = false;
    let exitResult: ExitResult;
    try {
      exitResult = await withTimeout(
        Promise.all([running.exit, this.waitForHealthDown()]).then(([exit]) => exit),
        this.stopTimeoutMs,
        "graceful backend shutdown"
      );
    } catch {
      forced = true;
      running.child.kill("SIGKILL");
      exitResult = await withTimeout(
        Promise.all([running.exit, this.waitForHealthDown()]).then(([exit]) => exit),
        FORCE_STOP_TIMEOUT_MS,
        "forced backend shutdown"
      );
    }
    if (this.running?.child === running.child) this.running = null;

    const result: BackendStopResult = {
      clean: !forced,
      forced,
      alreadyStopped: false,
      exitCode: exitResult.code,
      signal: exitResult.signal
    };
    this.lastStop = result;
    return result;
  }

  async cleanup(): Promise<BackendStopResult> {
    return this.stop();
  }

  private backendEnvironment(): NodeJS.ProcessEnv {
    const workRoot = path.dirname(this.databasePath);
    const inheritedEnvironment = Object.fromEntries(
      Object.entries(process.env).filter(
        ([name]) => !/(?:api.?key|token|secret|password|credential|authorization)/i.test(name)
      )
    );
    return {
      ...inheritedEnvironment,
      AI_KP_BACKUP_ROOT: path.join(workRoot, "backups"),
      AI_KP_CORS_ORIGINS: this.frontendUrl,
      AI_KP_DB_PATH: this.databasePath,
      AI_KP_DEPLOYMENT_MODE: "local",
      AI_KP_LOCAL_ADMIN_ENABLED: "false",
      AI_KP_ADMIN_TOKEN: this.adminToken,
      AI_KP_LLM_BASE_URL: "http://127.0.0.1:9/v1",
      AI_KP_LLM_MODEL: "e2e-unavailable-model",
      AI_KP_MAP_ASSET_ROOT: path.join(workRoot, "map-assets"),
      AI_KP_MODULE_ASSET_ROOT: path.join(workRoot, "module-assets"),
      AI_KP_RULEBOOK_INDEX_ROOT: path.join(workRoot, "rag"),
      PYTHONPATH: path.join(this.projectRoot, "src")
    };
  }

  private async waitForOwnedIdentity(
    child: ChildProcessWithoutNullStreams,
    exit: Promise<ExitResult>
  ): Promise<BackendRuntimeIdentity> {
    const deadline = Date.now() + this.startTimeoutMs;
    while (Date.now() < deadline) {
      const earlyExit = await settledValue(exit);
      if (earlyExit) {
        throw new Error(
          `uvicorn exited before readiness (code=${earlyExit.code}, signal=${earlyExit.signal})`
        );
      }
      const identity = await this.fetchRuntimeIdentity();
      if (identity) {
        if (identity.os_pid !== child.pid) {
          throw new Error(
            `Port ${this.port} belongs to PID ${identity.os_pid}, not spawned child PID ${child.pid}`
          );
        }
        return identity;
      }
      await delay(POLL_INTERVAL_MS);
    }
    throw new Error(`Timed out waiting ${this.startTimeoutMs}ms for backend runtime identity`);
  }

  private async fetchRuntimeIdentity(): Promise<BackendRuntimeIdentity | null> {
    try {
      const response = await fetch(`${this.backendUrl}/debug/runtime-identity`, {
        headers: { "X-AI-KP-Admin-Token": this.adminToken },
        signal: AbortSignal.timeout(1_000)
      });
      if (!response.ok) return null;
      const candidate: unknown = await response.json();
      return isRuntimeIdentity(candidate) ? candidate : null;
    } catch {
      return null;
    }
  }

  private async waitForHealthDown(): Promise<void> {
    while (true) {
      try {
        const response = await fetch(`${this.backendUrl}/health`, {
          signal: AbortSignal.timeout(250)
        });
        await response.body?.cancel();
      } catch {
        return;
      }
      await delay(POLL_INTERVAL_MS);
    }
  }

  private async forceDiscardChild(
    child: ChildProcessWithoutNullStreams,
    exit: Promise<ExitResult>
  ): Promise<void> {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    await withTimeout(exit, FORCE_STOP_TIMEOUT_MS, "failed-start child cleanup");
    if (this.running?.child === child) this.running = null;
  }

  private appendLog(chunk: Buffer): void {
    const redacted = redactManagedBackendLog(chunk.toString("utf8"), [
      this.adminToken,
      ...this.sensitiveEnvironmentValues
    ]);
    this.logTail = (this.logTail + redacted).slice(-MAX_LOG_CHARS);
  }
}

export function redactManagedBackendLog(log: string, sensitiveValues: readonly string[]): string {
  let redacted = log;
  for (const value of sensitiveValues) {
    if (value.length >= 6) redacted = redacted.split(value).join("[REDACTED]");
  }
  return redacted
    .replace(
      /((?:api.?key|token|secret|password|credential|authorization)["'\s:=]+)([^\s,"'}]+)/gi,
      "$1[REDACTED]"
    )
    .replace(/(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi, "$1[REDACTED]@");
}

function positiveTimeout(value: number | undefined, fallback: number, name: string): number {
  const timeout = value ?? fallback;
  if (!Number.isFinite(timeout) || timeout < 0) {
    throw new Error(`${name} must be a non-negative finite number`);
  }
  return timeout;
}

function isRuntimeIdentity(value: unknown): value is BackendRuntimeIdentity {
  if (!value || typeof value !== "object") return false;
  const identity = value as Record<string, unknown>;
  return (
    typeof identity.process_instance_id === "string" &&
    identity.process_instance_id.length > 0 &&
    Number.isInteger(identity.os_pid) &&
    (identity.os_pid as number) > 0 &&
    typeof identity.persistent_store_id === "string" &&
    identity.persistent_store_id.length > 0 &&
    Number.isInteger(identity.schema_version) &&
    identity.status === "ready"
  );
}

async function assertPortAvailable(port: number): Promise<void> {
  const occupied = await new Promise<boolean>((resolve, reject) => {
    const socket = net.createConnection({ host: LOOPBACK_HOST, port });
    socket.setTimeout(500);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", (error: NodeJS.ErrnoException) => {
      socket.destroy();
      if (error.code === "ECONNREFUSED") resolve(false);
      else reject(error);
    });
    socket.once("timeout", () => {
      socket.destroy();
      reject(new Error(`Timed out checking backend port ${port}`));
    });
  });
  if (occupied) {
    throw new Error(`Backend port ${port} is already occupied; refusing to reuse an unowned process`);
  }
}

async function settledValue<T>(promise: Promise<T>): Promise<T | null> {
  const pending = Symbol("pending");
  const value = await Promise.race([promise, Promise.resolve(pending)]);
  return value === pending ? null : value;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function withTimeout<T>(promise: Promise<T>, milliseconds: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`Timed out waiting for ${label}`)), milliseconds);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

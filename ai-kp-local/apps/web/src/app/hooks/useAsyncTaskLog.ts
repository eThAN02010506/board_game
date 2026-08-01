import { useRef, useState } from "react";

type UseAsyncTaskLogOptions<Scope> = {
  captureScope: () => Scope;
  isCurrentScope: (scope: Scope) => boolean;
  reportSilentFailure: (label: string) => void;
  stringify: (value: unknown) => string;
};

export function useAsyncTaskLog<Scope>(
  options: UseAsyncTaskLogOptions<Scope>
) {
  const [log, setLog] = useState("准备就绪。先连接后端，或直接创建一个测试团。");
  const [loading, setLoading] = useState(false);
  const logRequestVersion = useRef(0);
  const pendingRequestCount = useRef(0);

  function showLog(message: string) {
    logRequestVersion.current += 1;
    setLog(message);
  }

  async function run<T>(
    label: string,
    action: () => Promise<T>,
    onError?: (error: unknown) => void
  ): Promise<T | undefined> {
    const scope = options.captureScope();
    const logRequest = ++logRequestVersion.current;
    pendingRequestCount.current += 1;
    setLoading(true);
    setLog(`${label}...`);
    try {
      const result = await action();
      if (!options.isCurrentScope(scope)) return undefined;
      if (logRequestVersion.current === logRequest) setLog(options.stringify(result));
      return result;
    } catch (error) {
      if (!options.isCurrentScope(scope)) return undefined;
      onError?.(error);
      if (logRequestVersion.current === logRequest) {
        setLog(error instanceof Error ? error.message : String(error));
      }
      return undefined;
    } finally {
      pendingRequestCount.current = Math.max(0, pendingRequestCount.current - 1);
      setLoading(pendingRequestCount.current > 0);
    }
  }

  async function perform<T>(
    label: string,
    action: () => Promise<T>,
    silent = false
  ): Promise<T | undefined> {
    if (!silent) return run(label, action);
    const scope = options.captureScope();
    try {
      const result = await action();
      return options.isCurrentScope(scope) ? result : undefined;
    } catch {
      if (options.isCurrentScope(scope)) options.reportSilentFailure(label);
      return undefined;
    }
  }

  return {
    loading,
    log,
    perform,
    run,
    showLog
  };
}

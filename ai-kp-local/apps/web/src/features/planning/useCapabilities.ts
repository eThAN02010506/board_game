import { useCallback, useEffect, useRef, useState } from "react";

import { fetchCapabilities } from "../../api/client";
import type { Capability } from "../../api/types";

export function useCapabilities() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const requestVersion = useRef(0);

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const result = await fetchCapabilities();
      if (requestVersion.current === version) setCapabilities(result);
    } catch {
      if (requestVersion.current !== version) return;
      setCapabilities([]);
      setError("无法读取后端能力目录。请确认本地后端已启动，然后重试。");
    } finally {
      if (requestVersion.current === version) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      requestVersion.current += 1;
    };
  }, [refresh]);

  return { capabilities, error, loading, refresh };
}

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => Array.from(values.keys())[index] ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, String(value))
  };
}

if (!globalThis.localStorage) {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: memoryStorage()
  });
}

if (!globalThis.sessionStorage) {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: memoryStorage()
  });
}

if (!URL.createObjectURL) {
  URL.createObjectURL = () => "blob:test-map-asset";
}

if (!URL.revokeObjectURL) {
  URL.revokeObjectURL = () => undefined;
}

import type { Capability, Role } from "./types";

export const apiBase = "/api";

type CredentialSnapshot = {
  accessToken: string;
  adminToken: string;
  role: Role | "";
  pcId: string | null;
  playerToken: string;
};

const credentials: CredentialSnapshot = {
  accessToken: "",
  adminToken: "",
  role: "",
  pcId: null,
  playerToken: ""
};

export const credentialBridge = {
  session(accessToken: string, role: Role | "" = "", pcId: string | null = null) {
    credentials.accessToken = accessToken;
    credentials.role = role;
    credentials.pcId = pcId;
  },
  admin(adminToken: string) {
    credentials.adminToken = adminToken;
  },
  player(playerToken: string) {
    credentials.playerToken = playerToken;
  },
  snapshot(): Readonly<CredentialSnapshot> {
    return { ...credentials };
  }
};

async function sendJson<T>(
  url: string,
  init: RequestInit | undefined,
  accessToken: string,
  adminToken: string
): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  if (adminToken) {
    headers.set("X-AI-KP-Admin-Token", adminToken);
  }
  if (credentials.playerToken) {
    headers.set("X-AI-KP-Player-Token", credentials.playerToken);
  }
  const response = await fetch(`${apiBase}${url}`, { ...init, headers });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  return sendJson(url, init, credentials.accessToken, credentials.adminToken);
}

export function requestJsonWithAccessToken<T>(
  url: string,
  accessToken: string,
  init?: RequestInit
): Promise<T> {
  return sendJson(url, init, accessToken, credentials.adminToken);
}

export async function requestBlob(url: string, signal?: AbortSignal): Promise<Blob> {
  const headers = new Headers();
  if (credentials.accessToken) {
    headers.set("Authorization", `Bearer ${credentials.accessToken}`);
  }
  if (credentials.adminToken) {
    headers.set("X-AI-KP-Admin-Token", credentials.adminToken);
  }
  const response = await fetch(`${apiBase}${url}`, { headers, signal });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.blob();
}

export function fetchCapabilities(): Promise<Capability[]> {
  return requestJson<Capability[]>("/capabilities");
}

export async function requestFile<T>(url: string, file: File): Promise<T> {
  return requestBinary(url, file, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
}

export async function requestBinary<T>(
  url: string,
  file: File,
  contentType = "application/octet-stream"
): Promise<T> {
  const headers = new Headers();
  headers.set("Content-Type", contentType);
  headers.set("X-File-Name", encodeURIComponent(file.name));
  if (credentials.accessToken) {
    headers.set("Authorization", `Bearer ${credentials.accessToken}`);
  }
  if (credentials.adminToken) {
    headers.set("X-AI-KP-Admin-Token", credentials.adminToken);
  }
  if (credentials.playerToken) {
    headers.set("X-AI-KP-Player-Token", credentials.playerToken);
  }
  const response = await fetch(`${apiBase}${url}`, { method: "POST", headers, body: file });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

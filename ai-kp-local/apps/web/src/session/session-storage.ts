import type { Role } from "../api/types";

const tokenStoragePrefix = "ai-kp-session-token:";
const mapSelectionStoragePrefix = "ai-kp-active-map:";
const adminTokenStorageKey = "ai-kp-admin-token";

export function readAdminToken() {
  return sessionStorage.getItem(adminTokenStorageKey) ?? "";
}

export function writeAdminToken(token: string) {
  if (token) sessionStorage.setItem(adminTokenStorageKey, token);
  else sessionStorage.removeItem(adminTokenStorageKey);
}

export function readCampaignToken(campaignId: string) {
  return sessionStorage.getItem(`${tokenStoragePrefix}${campaignId}`) ?? "";
}

export function writeCampaignToken(campaignId: string, token: string) {
  sessionStorage.setItem(`${tokenStoragePrefix}${campaignId}`, token);
}

export function removeCampaignToken(campaignId: string) {
  sessionStorage.removeItem(`${tokenStoragePrefix}${campaignId}`);
}

export function listStoredCampaignTokens() {
  return Object.keys(sessionStorage)
    .filter((key) => key.startsWith(tokenStoragePrefix))
    .map((key) => ({
      campaignId: key.slice(tokenStoragePrefix.length),
      token: sessionStorage.getItem(key) ?? ""
    }));
}

function mapSelectionKey(campaignId: string, role: Role | "") {
  return `${mapSelectionStoragePrefix}${campaignId}:${role || "anonymous"}`;
}

export function readActiveMapId(campaignId: string, role: Role | "") {
  return sessionStorage.getItem(mapSelectionKey(campaignId, role)) ?? "";
}

export function writeActiveMapId(campaignId: string, role: Role | "", mapId: string) {
  sessionStorage.setItem(mapSelectionKey(campaignId, role), mapId);
}

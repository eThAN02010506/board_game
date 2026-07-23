import {
  createContext,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
  useContext,
  useState
} from "react";

import { credentialBridge } from "../api/client";
import {
  readAdminToken,
  readPlayerProfileToken,
  writeAdminToken,
  writePlayerProfileToken
} from "../session/session-storage";

type CredentialContextValue = {
  adminToken: string;
  persistAdminToken: () => string;
  rememberPlayerToken: (token: string) => void;
  setAdminToken: Dispatch<SetStateAction<string>>;
};

const CredentialContext = createContext<CredentialContextValue | null>(null);

function initializeCredentials() {
  const adminToken = readAdminToken();
  credentialBridge.admin(adminToken);
  credentialBridge.player(readPlayerProfileToken());
  return adminToken;
}

export function CredentialProvider({ children }: { children: ReactNode }) {
  const [adminToken, setAdminToken] = useState(initializeCredentials);

  function persistAdminToken() {
    const normalized = adminToken.trim();
    credentialBridge.admin(normalized);
    writeAdminToken(normalized);
    if (normalized !== adminToken) setAdminToken(normalized);
    return normalized;
  }

  function rememberPlayerToken(token: string) {
    credentialBridge.player(token);
    writePlayerProfileToken(token);
  }

  return (
    <CredentialContext.Provider
      value={{ adminToken, persistAdminToken, rememberPlayerToken, setAdminToken }}
    >
      {children}
    </CredentialContext.Provider>
  );
}

export function useCredentials() {
  const context = useContext(CredentialContext);
  if (!context) throw new Error("useCredentials must be used inside CredentialProvider");
  return context;
}

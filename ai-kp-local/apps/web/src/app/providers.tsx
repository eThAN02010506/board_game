import type { ReactNode } from "react";

import { CredentialProvider } from "../auth/credentials";

export function AppProviders({ children }: { children: ReactNode }) {
  return <CredentialProvider>{children}</CredentialProvider>;
}

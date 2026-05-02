import { useState } from "react";
import { LoginView } from "./LoginView";
import { CollectionsView } from "./CollectionsView";
import { loadCreds, type Credentials } from "@/lib/webdav";

export function App() {
  const [creds, setCreds] = useState<Credentials | null>(loadCreds());

  if (!creds) {
    return <LoginView onLoginSuccess={() => setCreds(loadCreds())} />;
  }

  return <CollectionsView creds={creds} onLogout={() => setCreds(null)} />;
}

import { useEffect, useState } from "react";
import { LoginView } from "./LoginView";
import { CollectionsView } from "./CollectionsView";
import { CollectionDetailView } from "./CollectionDetailView";
import {
  detectProxiedSession,
  loadCreds,
  clearCreds,
  type Collection,
  type Credentials,
} from "@/lib/webdav";

type View =
  | { kind: "login" }
  | { kind: "collections" }
  | { kind: "detail"; collection: Collection };

type AuthState =
  | { phase: "detecting" }
  | { phase: "ready"; creds: Credentials | null };

export function App() {
  const [auth, setAuth] = useState<AuthState>({ phase: "detecting" });
  const [view, setView] = useState<View>({ kind: "collections" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Prefer cached Basic Auth creds (htpasswd flow): if user logged in
      // before in this tab, keep them.
      const cached = loadCreds();
      if (cached) {
        if (!cancelled) setAuth({ phase: "ready", creds: cached });
        return;
      }
      // Otherwise check if reverse-proxy auth is in effect.
      const proxied = await detectProxiedSession();
      if (!cancelled) setAuth({ phase: "ready", creds: proxied });
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (auth.phase === "detecting") {
    // Single fetch round-trip; matches the bootstrap fallback styling.
    return null;
  }

  const creds = auth.creds;

  if (!creds) {
    return (
      <LoginView
        onLoginSuccess={() => {
          setAuth({ phase: "ready", creds: loadCreds() });
          setView({ kind: "collections" });
        }}
      />
    );
  }

  if (view.kind === "detail") {
    return (
      <CollectionDetailView
        creds={creds}
        collection={view.collection}
        onBack={() => setView({ kind: "collections" })}
      />
    );
  }

  return (
    <CollectionsView
      creds={creds}
      onLogout={() => {
        clearCreds();
        // For Basic Auth: clear stored creds and go to login.
        // For proxy auth: there's nothing useful to "log out" client-side
        // since the proxy controls the session - reload and re-detect.
        if (creds.password === null) {
          window.location.reload();
        } else {
          setAuth({ phase: "ready", creds: null });
          setView({ kind: "login" });
        }
      }}
      onOpenCollection={(c) => setView({ kind: "detail", collection: c })}
    />
  );
}

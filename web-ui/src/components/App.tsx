import { useState } from "react";
import { LoginView } from "./LoginView";
import { CollectionsView } from "./CollectionsView";
import { CollectionDetailView } from "./CollectionDetailView";
import { loadCreds, type Collection, type Credentials } from "@/lib/webdav";

type View =
  | { kind: "login" }
  | { kind: "collections" }
  | { kind: "detail"; collection: Collection };

export function App() {
  const [creds, setCreds] = useState<Credentials | null>(loadCreds());
  const [view, setView] = useState<View>({ kind: "collections" });

  if (!creds) {
    return (
      <LoginView
        onLoginSuccess={() => {
          setCreds(loadCreds());
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
        setCreds(null);
        setView({ kind: "login" });
      }}
      onOpenCollection={(c) => setView({ kind: "detail", collection: c })}
    />
  );
}

import { useEffect, useState } from "react";
import {
  CalendarDays,
  Contact,
  ListTodo,
  BookOpen,
  Rss,
  RefreshCw,
  LogOut,
  Copy,
  Check,
  Trash2,
  Download,
  Plus,
  Loader2,
  Inbox,
} from "lucide-react";
import {
  listCollections,
  deleteCollection,
  publicUrlFor,
  clearCreds,
  type Collection,
  type CollectionType,
  type Credentials,
} from "@/lib/webdav";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
  creds: Credentials;
  onLogout: () => void;
}

const TYPE_LABELS: Record<CollectionType, string> = {
  ADDRESSBOOK: "Address book",
  CALENDAR_JOURNAL_TASKS: "Calendar · Journal · Tasks",
  CALENDAR_JOURNAL: "Calendar · Journal",
  CALENDAR_TASKS: "Calendar · Tasks",
  JOURNAL_TASKS: "Journal · Tasks",
  CALENDAR: "Calendar",
  JOURNAL: "Journal",
  TASKS: "Tasks",
  WEBCAL: "Web calendar",
  PRINCIPAL: "Principal",
};

function TypeIcon({ type }: { type: CollectionType }) {
  if (type === "ADDRESSBOOK") return <Contact className="size-4" />;
  if (type === "TASKS" || type === "JOURNAL_TASKS" || type === "CALENDAR_TASKS")
    return <ListTodo className="size-4" />;
  if (type === "JOURNAL") return <BookOpen className="size-4" />;
  if (type === "WEBCAL") return <Rss className="size-4" />;
  return <CalendarDays className="size-4" />;
}

function CollectionCard({
  c,
  onDelete,
}: {
  c: Collection;
  onDelete: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const url = publicUrlFor(c.href);

  async function copyUrl() {
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <Card className="overflow-hidden transition-shadow hover:shadow-md">
      <div
        className="h-1.5"
        style={{ background: c.color || "var(--color-primary)" }}
      />
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold truncate" title={c.displayname}>
              {c.displayname || c.href.split("/").filter(Boolean).pop()}
            </h3>
            <div className="mt-1 flex flex-wrap gap-2 items-center">
              <Badge variant="secondary" className="gap-1">
                <TypeIcon type={c.type} />
                {TYPE_LABELS[c.type]}
              </Badge>
              <span className="text-xs text-[var(--color-muted-foreground)]">
                {c.contentcount} item{c.contentcount === "1" ? "" : "s"}
              </span>
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {c.description && (
          <p className="text-sm text-[var(--color-muted-foreground)] line-clamp-2">
            {c.description}
          </p>
        )}
        <div className="flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-muted)]/40 px-2.5 py-1.5">
          <code className="flex-1 text-xs truncate text-[var(--color-muted-foreground)]" title={url}>
            {url}
          </code>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={copyUrl}
            title={copied ? "Copied!" : "Copy URL"}
          >
            {copied ? <Check className="size-3.5 text-green-600" /> : <Copy className="size-3.5" />}
          </Button>
        </div>
        <div className="flex gap-1">
          <Button asChild={false} variant="outline" size="sm" className="flex-1" onClick={() => window.open(url)}>
            <span><Download /> Download</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onDelete}
            title="Delete collection"
            className="text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10"
          >
            <Trash2 />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function CollectionsView({ creds, onLogout }: Props) {
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Collection | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      setCollections(await listCollections(creds));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleLogout() {
    clearCreds();
    onLogout();
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deleteCollection(creds, pendingDelete.href);
      setPendingDelete(null);
      setConfirmText("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="min-h-screen bg-[var(--color-background)]">
      <header className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-background)]/80 backdrop-blur supports-[backdrop-filter]:bg-[var(--color-background)]/60">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <div className="flex size-8 items-center justify-center rounded-md bg-[var(--color-primary)] text-[var(--color-primary-foreground)] shrink-0">
              <CalendarDays className="size-4" />
            </div>
            <div className="min-w-0">
              <div className="font-semibold leading-tight">moreradicale</div>
              <div className="text-xs text-[var(--color-muted-foreground)] truncate">
                Signed in as <span className="font-mono">{creds.user}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={refresh}
              disabled={refreshing}
              title="Refresh"
            >
              <RefreshCw className={refreshing ? "animate-spin" : ""} />
            </Button>
            <Button variant="ghost" size="icon" onClick={handleLogout} title="Sign out">
              <LogOut />
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 sm:px-6 py-6 space-y-4">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {collections === null ? (
          <div className="flex items-center justify-center py-20 text-[var(--color-muted-foreground)]">
            <Loader2 className="size-5 animate-spin mr-2" /> Loading your collections...
          </div>
        ) : collections.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center text-center py-16 gap-3">
              <Inbox className="size-10 text-[var(--color-muted-foreground)]" />
              <div>
                <h2 className="font-semibold">No collections yet</h2>
                <p className="text-sm text-[var(--color-muted-foreground)] mt-1">
                  Create a calendar or address book to get started.
                </p>
              </div>
              <Button>
                <Plus /> New collection
              </Button>
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">
                {collections.length} collection{collections.length === 1 ? "" : "s"}
              </h2>
              <Button>
                <Plus /> New
              </Button>
            </div>
            <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
              {collections.map((c) => (
                <CollectionCard
                  key={c.href}
                  c={c}
                  onDelete={() => {
                    setPendingDelete(c);
                    setConfirmText("");
                  }}
                />
              ))}
            </div>
          </>
        )}
      </main>

      {pendingDelete && (
        <div
          className="fixed inset-0 z-20 flex items-center justify-center p-4 bg-black/50"
          onClick={() => !deleting && setPendingDelete(null)}
        >
          <Card className="w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <CardHeader>
              <h2 className="text-lg font-semibold flex items-center gap-2">
                <Trash2 className="text-[var(--color-destructive)]" /> Delete collection
              </h2>
              <p className="text-sm text-[var(--color-muted-foreground)]">
                This permanently deletes{" "}
                <strong>{pendingDelete.displayname || pendingDelete.href}</strong> and all its
                items. This cannot be undone.
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm">
                Type <code className="rounded bg-[var(--color-muted)] px-1.5 py-0.5 text-xs">DELETE</code> to confirm:
              </p>
              <input
                autoFocus
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                className="flex h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-1 text-sm font-mono"
              />
              <div className="flex gap-2 justify-end">
                <Button
                  variant="ghost"
                  onClick={() => setPendingDelete(null)}
                  disabled={deleting}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  disabled={confirmText !== "DELETE" || deleting}
                  onClick={confirmDelete}
                >
                  {deleting ? <Loader2 className="animate-spin" /> : <Trash2 />}
                  Delete
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

import { useEffect, useState, type FormEvent } from "react";
import {
  Loader2,
  UserPlus,
  X,
  Eye,
  Pencil,
  Check,
  Clock,
  XCircle,
} from "lucide-react";
import {
  listShares,
  addShare,
  removeShare,
  type Collection,
  type Credentials,
  type Share,
  type ShareAccess,
} from "@/lib/webdav";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogBody,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
  open: boolean;
  onClose: () => void;
  creds: Credentials;
  collection: Collection;
}

function StatusBadge({ status }: { status: Share["status"] }) {
  if (status === "accepted") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
        <Check className="size-3" /> Accepted
      </span>
    );
  }
  if (status === "declined") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-[var(--color-destructive)]">
        <XCircle className="size-3" /> Declined
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
      <Clock className="size-3" /> Pending
    </span>
  );
}

function AccessIcon({ access }: { access: ShareAccess }) {
  return access === "read-write" ? (
    <Pencil className="size-3.5" />
  ) : (
    <Eye className="size-3.5" />
  );
}

export function ShareDialog({ open, onClose, creds, collection }: Props) {
  const [shares, setShares] = useState<Share[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Add-share form
  const [newUser, setNewUser] = useState("");
  const [newAccess, setNewAccess] = useState<ShareAccess>("read");
  const [newSummary, setNewSummary] = useState("");

  async function refresh() {
    setError(null);
    try {
      setShares(await listShares(creds, collection.href));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    if (!open) return;
    setShares(null);
    setError(null);
    setNewUser("");
    setNewAccess("read");
    setNewSummary("");
    setBusy(false);
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, collection.href]);

  async function handleAdd(e: FormEvent) {
    e.preventDefault();
    if (!newUser) return;
    setBusy(true);
    setError(null);
    try {
      await addShare(creds, collection.href, newUser.trim(), newAccess, newSummary);
      setNewUser("");
      setNewSummary("");
      setNewAccess("read");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(sharee: string) {
    setBusy(true);
    setError(null);
    try {
      await removeShare(creds, collection.href, sharee);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={busy ? () => {} : onClose} dismissible={!busy}>
      <DialogHeader>
        <DialogTitle>Share calendar</DialogTitle>
        <DialogDescription>
          Give other users access to <strong>{collection.displayname || "this calendar"}</strong>.
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="space-y-4">
        {/* Current shares */}
        <div className="space-y-2">
          <Label>Current shares</Label>
          {shares === null ? (
            <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)] py-2">
              <Loader2 className="size-4 animate-spin" /> Loading...
            </div>
          ) : shares.length === 0 ? (
            <div className="text-sm text-[var(--color-muted-foreground)] py-2 px-3 rounded-md border border-dashed border-[var(--color-border)]">
              Not shared with anyone yet.
            </div>
          ) : (
            <ul className="space-y-1.5">
              {shares.map((s) => (
                <li
                  key={s.sharee}
                  className="flex items-center gap-3 rounded-md border border-[var(--color-border)] p-2.5"
                >
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-[var(--color-secondary)] text-[var(--color-secondary-foreground)]">
                    <AccessIcon access={s.access} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium truncate" title={s.commonName || s.sharee}>
                      {s.commonName || s.sharee}
                    </div>
                    <div className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
                      <span className="font-mono truncate">{s.sharee}</span>
                      <span>·</span>
                      <span>{s.access === "read-write" ? "Can edit" : "Read only"}</span>
                      <span>·</span>
                      <StatusBadge status={s.status} />
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10"
                    onClick={() => handleRemove(s.sharee)}
                    disabled={busy}
                    title="Remove share"
                  >
                    <X />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Add new */}
        <form onSubmit={handleAdd} className="space-y-2">
          <Label>Add user</Label>
          <div className="flex gap-2">
            <Input
              type="text"
              value={newUser}
              onChange={(e) => setNewUser(e.target.value)}
              placeholder="username"
              disabled={busy}
              required
              className="flex-1"
            />
            <Select
              value={newAccess}
              onChange={(e) => setNewAccess(e.target.value as ShareAccess)}
              disabled={busy}
              className="w-36"
            >
              <option value="read">Read only</option>
              <option value="read-write">Can edit</option>
            </Select>
          </div>
          <Input
            type="text"
            value={newSummary}
            onChange={(e) => setNewSummary(e.target.value)}
            placeholder="Optional message (e.g. 'Sharing my work calendar')"
            disabled={busy}
          />
          <Button type="submit" disabled={busy || !newUser} className="w-full">
            {busy ? <Loader2 className="animate-spin" /> : <UserPlus />}
            {busy ? "Sharing..." : "Share"}
          </Button>
        </form>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </DialogBody>
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
          Done
        </Button>
      </DialogFooter>
    </Dialog>
  );
}

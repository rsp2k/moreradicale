import { useState, useEffect, type FormEvent } from "react";
import { Loader2, Plus, Save } from "lucide-react";
import {
  createCollection,
  updateCollectionProps,
  uuid,
  type Collection,
  type CollectionType,
  type Credentials,
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
  onDone: () => void;
  creds: Credentials;
  /** When provided, dialog is in edit mode for the given collection. */
  editing?: Collection;
}

const TYPE_OPTIONS: { value: CollectionType; label: string }[] = [
  { value: "CALENDAR", label: "Calendar (events)" },
  { value: "TASKS", label: "Tasks (to-dos)" },
  { value: "JOURNAL", label: "Journal" },
  { value: "CALENDAR_TASKS", label: "Calendar + tasks" },
  { value: "CALENDAR_JOURNAL", label: "Calendar + journal" },
  { value: "CALENDAR_JOURNAL_TASKS", label: "Calendar + journal + tasks" },
  { value: "ADDRESSBOOK", label: "Address book (contacts)" },
  { value: "WEBCAL", label: "Subscribed calendar (webcal)" },
];

const DEFAULT_COLOR = "#3b82f6"; // blue-500

export function CollectionFormDialog({ open, onClose, onDone, creds, editing }: Props) {
  const isEdit = Boolean(editing);
  const [type, setType] = useState<CollectionType>("CALENDAR");
  const [href, setHref] = useState("");
  const [displayname, setDisplayname] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState(DEFAULT_COLOR);
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Reset form whenever dialog opens (or switches editing target)
  useEffect(() => {
    if (!open) return;
    if (editing) {
      setType(editing.type);
      setHref(editing.href.split("/").filter(Boolean).pop() ?? "");
      setDisplayname(editing.displayname);
      setDescription(editing.description);
      setColor(editing.color || DEFAULT_COLOR);
      setSource(editing.source);
    } else {
      setType("CALENDAR");
      setHref(uuid());
      setDisplayname("");
      setDescription("");
      setColor(DEFAULT_COLOR);
      setSource("");
    }
    setError(null);
    setBusy(false);
  }, [open, editing]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (isEdit && editing) {
        await updateCollectionProps(creds, editing.href, editing.type, {
          displayname,
          description,
          color,
          source: editing.type === "WEBCAL" ? source : undefined,
        });
      } else {
        await createCollection(creds, type, href || uuid(), {
          displayname,
          description,
          color,
          source: type === "WEBCAL" ? source : undefined,
        });
      }
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const showSource = type === "WEBCAL";

  return (
    <Dialog open={open} onClose={busy ? () => {} : onClose} dismissible={!busy}>
      <DialogHeader>
        <DialogTitle>{isEdit ? "Edit collection" : "New collection"}</DialogTitle>
        <DialogDescription>
          {isEdit
            ? "Update the metadata for this collection."
            : "Create a new calendar, address book, or task list."}
        </DialogDescription>
      </DialogHeader>
      <form onSubmit={handleSubmit}>
        <DialogBody className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="cf-type">Type</Label>
            <Select
              id="cf-type"
              value={type}
              onChange={(e) => setType(e.target.value as CollectionType)}
              disabled={isEdit}
            >
              {TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
            {isEdit && (
              <p className="text-xs text-[var(--color-muted-foreground)]">
                Type can't be changed after creation.
              </p>
            )}
          </div>

          {!isEdit && (
            <div className="space-y-1.5">
              <Label htmlFor="cf-href">Path</Label>
              <Input
                id="cf-href"
                type="text"
                value={href}
                onChange={(e) => setHref(e.target.value)}
                placeholder="auto-generated"
              />
              <p className="text-xs text-[var(--color-muted-foreground)]">
                Used in the URL: <code>/{creds.user}/{href || "..."}/</code>
              </p>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="cf-name">Display name</Label>
            <Input
              id="cf-name"
              type="text"
              value={displayname}
              onChange={(e) => setDisplayname(e.target.value)}
              placeholder="My calendar"
              autoFocus={isEdit}
              required
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cf-desc">Description</Label>
            <Input
              id="cf-desc"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="optional"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cf-color">Color</Label>
            <div className="flex items-center gap-2">
              <input
                id="cf-color"
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                className="h-9 w-12 rounded border border-[var(--color-border)] cursor-pointer"
              />
              <Input
                type="text"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                className="flex-1 font-mono"
              />
            </div>
          </div>

          {showSource && (
            <div className="space-y-1.5">
              <Label htmlFor="cf-source">Source URL</Label>
              <Input
                id="cf-source"
                type="url"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder="https://example.com/calendar.ics"
                required
              />
            </div>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy || !displayname}>
            {busy ? <Loader2 className="animate-spin" /> : isEdit ? <Save /> : <Plus />}
            {busy ? "Saving..." : isEdit ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

import { useEffect, useState, type FormEvent } from "react";
import { Loader2, Plus, CalendarDays, ListTodo, BookOpen } from "lucide-react";
import {
  uploadItem,
  uuid,
  type Collection,
  type Credentials,
  type CollectionType,
} from "@/lib/webdav";
import { serializeVEvent, serializeVTodo, serializeVJournal } from "@/lib/ical";
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
  collection: Collection;
}

type Kind = "VEVENT" | "VTODO" | "VJOURNAL";

/** Decide which item kinds make sense based on the collection's
 *  supported component set. */
function allowedKinds(type: CollectionType): Kind[] {
  switch (type) {
    case "CALENDAR_JOURNAL_TASKS":
      return ["VEVENT", "VTODO", "VJOURNAL"];
    case "CALENDAR_JOURNAL":
      return ["VEVENT", "VJOURNAL"];
    case "CALENDAR_TASKS":
      return ["VEVENT", "VTODO"];
    case "JOURNAL_TASKS":
      return ["VTODO", "VJOURNAL"];
    case "TASKS":
      return ["VTODO"];
    case "JOURNAL":
      return ["VJOURNAL"];
    case "CALENDAR":
    case "WEBCAL":
      return ["VEVENT"];
    default:
      return ["VEVENT"]; // fallback (caller blocks address books)
  }
}

const KIND_LABEL: Record<Kind, string> = {
  VEVENT: "Event",
  VTODO: "Task",
  VJOURNAL: "Journal",
};

const KIND_ICON: Record<Kind, React.ReactNode> = {
  VEVENT: <CalendarDays className="size-4" />,
  VTODO: <ListTodo className="size-4" />,
  VJOURNAL: <BookOpen className="size-4" />,
};

/** Format Date for HTML5 datetime-local input (uses local timezone). */
function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    d.getFullYear() +
    "-" +
    pad(d.getMonth() + 1) +
    "-" +
    pad(d.getDate()) +
    "T" +
    pad(d.getHours()) +
    ":" +
    pad(d.getMinutes())
  );
}

function toDateInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

export function ItemFormDialog({ open, onClose, onDone, creds, collection }: Props) {
  const kinds = allowedKinds(collection.type);
  const [kind, setKind] = useState<Kind>(kinds[0]);
  const [summary, setSummary] = useState("");
  const [allDay, setAllDay] = useState(false);
  const [dtstart, setDtstart] = useState("");
  const [dtend, setDtend] = useState("");
  const [location, setLocation] = useState("");
  const [description, setDescription] = useState("");
  const [due, setDue] = useState("");
  const [status, setStatus] = useState<"NEEDS-ACTION" | "IN-PROCESS" | "COMPLETED" | "CANCELLED">("NEEDS-ACTION");
  const [priority, setPriority] = useState<"" | "1" | "5" | "9">("");
  // Journal-specific
  const [journalDate, setJournalDate] = useState("");
  const [journalStatus, setJournalStatus] = useState<"DRAFT" | "FINAL" | "CANCELLED">("FINAL");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    const now = new Date();
    const inOneHour = new Date(now.getTime() + 60 * 60 * 1000);
    const inTwoHours = new Date(now.getTime() + 2 * 60 * 60 * 1000);
    setKind(kinds[0]);
    setSummary("");
    setAllDay(false);
    setDtstart(toLocalInput(inOneHour));
    setDtend(toLocalInput(inTwoHours));
    setLocation("");
    setDescription("");
    setDue("");
    setStatus("NEEDS-ACTION");
    setPriority("");
    setJournalDate(toDateInput(now));
    setJournalStatus("FINAL");
    setError(null);
    setBusy(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, collection.href]);

  function buildContent(): { ics: string; filename: string } {
    const id = uuid();
    if (kind === "VEVENT") {
      const startD = allDay ? new Date(dtstart + "T00:00:00") : new Date(dtstart);
      const endD = dtend
        ? allDay ? new Date(dtend + "T00:00:00") : new Date(dtend)
        : undefined;
      const ics = serializeVEvent({
        uid: id,
        summary,
        dtstart: startD,
        dtend: endD,
        allDay,
        location: location || undefined,
        description: description || undefined,
      });
      return { ics, filename: `${id}.ics` };
    }
    if (kind === "VJOURNAL") {
      const ics = serializeVJournal({
        uid: id,
        summary,
        dtstart: new Date(journalDate + "T00:00:00"),
        allDay: true,
        status: journalStatus,
        description: description || undefined,
      });
      return { ics, filename: `${id}.ics` };
    }
    const dueD = due ? new Date(due) : undefined;
    const ics = serializeVTodo({
      uid: id,
      summary,
      due: dueD,
      status,
      priority: priority ? parseInt(priority, 10) : undefined,
      description: description || undefined,
    });
    return { ics, filename: `${id}.ics` };
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { ics, filename } = buildContent();
      await uploadItem(
        creds,
        collection.href,
        filename,
        ics,
        "text/calendar; charset=utf-8"
      );
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={busy ? () => {} : onClose} dismissible={!busy}>
      <DialogHeader>
        <DialogTitle>New item</DialogTitle>
        <DialogDescription>
          Add to <strong>{collection.displayname || "this collection"}</strong>.
        </DialogDescription>
      </DialogHeader>
      <form onSubmit={handleSubmit}>
        <DialogBody className="space-y-3">
          {kinds.length > 1 && (
            <div className="space-y-1.5">
              <Label>Type</Label>
              <div
                className="grid gap-2"
                style={{ gridTemplateColumns: `repeat(${kinds.length}, minmax(0, 1fr))` }}
              >
                {kinds.map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setKind(k)}
                    className={`flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors ${
                      kind === k
                        ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                        : "border-[var(--color-border)] hover:bg-[var(--color-muted)]"
                    }`}
                  >
                    {KIND_ICON[k]} {KIND_LABEL[k]}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="if-summary">{kind === "VJOURNAL" ? "Title" : "Summary"}</Label>
            <Input
              id="if-summary"
              type="text"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder={
                kind === "VEVENT" ? "Coffee with Margaret"
                : kind === "VTODO" ? "Polish the new UI"
                : "Sprint retro notes"
              }
              required
              autoFocus
            />
          </div>

          {kind === "VEVENT" ? (
            <>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={allDay}
                  onChange={(e) => {
                    setAllDay(e.target.checked);
                    // Convert existing values when toggling
                    if (e.target.checked && dtstart) setDtstart(dtstart.split("T")[0]);
                    if (e.target.checked && dtend) setDtend(dtend.split("T")[0]);
                  }}
                  className="size-4 rounded border-[var(--color-border)]"
                />
                All-day
              </label>

              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1.5">
                  <Label htmlFor="if-start">Starts</Label>
                  <Input
                    id="if-start"
                    type={allDay ? "date" : "datetime-local"}
                    value={dtstart}
                    onChange={(e) => setDtstart(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="if-end">Ends</Label>
                  <Input
                    id="if-end"
                    type={allDay ? "date" : "datetime-local"}
                    value={dtend}
                    onChange={(e) => setDtend(e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="if-loc">Location</Label>
                <Input
                  id="if-loc"
                  type="text"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="optional"
                />
              </div>
            </>
          ) : kind === "VJOURNAL" ? (
            <>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1.5">
                  <Label htmlFor="if-jdate">Date</Label>
                  <Input
                    id="if-jdate"
                    type="date"
                    value={journalDate}
                    onChange={(e) => setJournalDate(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="if-jstatus">Status</Label>
                  <Select
                    id="if-jstatus"
                    value={journalStatus}
                    onChange={(e) => setJournalStatus(e.target.value as typeof journalStatus)}
                  >
                    <option value="DRAFT">Draft</option>
                    <option value="FINAL">Final</option>
                    <option value="CANCELLED">Cancelled</option>
                  </Select>
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1.5">
                  <Label htmlFor="if-due">Due</Label>
                  <Input
                    id="if-due"
                    type="datetime-local"
                    value={due}
                    onChange={(e) => setDue(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="if-status">Status</Label>
                  <Select
                    id="if-status"
                    value={status}
                    onChange={(e) => setStatus(e.target.value as typeof status)}
                  >
                    <option value="NEEDS-ACTION">Needs action</option>
                    <option value="IN-PROCESS">In progress</option>
                    <option value="COMPLETED">Completed</option>
                    <option value="CANCELLED">Cancelled</option>
                  </Select>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="if-prio">Priority</Label>
                <Select
                  id="if-prio"
                  value={priority}
                  onChange={(e) => setPriority(e.target.value as typeof priority)}
                >
                  <option value="">No priority</option>
                  <option value="1">High</option>
                  <option value="5">Medium</option>
                  <option value="9">Low</option>
                </Select>
              </div>
            </>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="if-desc">
              {kind === "VJOURNAL" ? "Body" : "Description"}
            </Label>
            <textarea
              id="if-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={
                kind === "VJOURNAL"
                  ? "What happened? Long-form notes go here."
                  : "optional notes"
              }
              rows={kind === "VJOURNAL" ? 8 : 3}
              required={kind === "VJOURNAL"}
              className="flex w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm shadow-xs placeholder:text-[var(--color-muted-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] focus-visible:ring-offset-1"
            />
          </div>

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
          <Button type="submit" disabled={busy || !summary}>
            {busy ? <Loader2 className="animate-spin" /> : <Plus />}
            {busy ? "Creating..." : "Create"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

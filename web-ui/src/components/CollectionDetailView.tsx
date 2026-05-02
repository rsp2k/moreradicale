import { useEffect, useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  Clock,
  MapPin,
  User,
  ListTodo,
  BookOpen,
  Contact,
  RefreshCw,
  ChevronRight,
  Loader2,
  Inbox,
  Trash2,
  AlignLeft,
  Plus,
} from "lucide-react";
import { ItemFormDialog } from "./ItemFormDialog";
import {
  listItems,
  getItem,
  deleteItem,
  type Collection,
  type CollectionItem,
  type Credentials,
} from "@/lib/webdav";
import {
  parseIcal,
  findItemComponent,
  getValue,
  getProp,
  formatIcalDate,
  decodeText,
  type IcalComponent,
} from "@/lib/ical";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
  creds: Credentials;
  collection: Collection;
  onBack: () => void;
}

interface LoadedItem {
  meta: CollectionItem;
  content?: string;
  parsed?: IcalComponent | null;
  loading: boolean;
  error?: string;
}

function fieldIcon(name: string) {
  if (name === "DTSTART" || name === "DTEND") return <Clock className="size-3.5 shrink-0 text-[var(--color-muted-foreground)]" />;
  if (name === "LOCATION") return <MapPin className="size-3.5 shrink-0 text-[var(--color-muted-foreground)]" />;
  if (name === "ORGANIZER" || name === "ATTENDEE" || name === "FN") return <User className="size-3.5 shrink-0 text-[var(--color-muted-foreground)]" />;
  if (name === "DESCRIPTION") return <AlignLeft className="size-3.5 shrink-0 text-[var(--color-muted-foreground)]" />;
  return null;
}

function fieldLabel(name: string): string {
  switch (name) {
    case "DTSTART": return "Starts";
    case "DTEND": return "Ends";
    case "DUE": return "Due";
    case "LOCATION": return "Location";
    case "ORGANIZER": return "Organizer";
    case "DESCRIPTION": return "Description";
    case "STATUS": return "Status";
    case "PRIORITY": return "Priority";
    case "FN": return "Name";
    case "EMAIL": return "Email";
    case "TEL": return "Phone";
    case "ORG": return "Organization";
    default: return name;
  }
}

function formatPropValue(name: string, value: string): string {
  if (["DTSTART", "DTEND", "DUE", "DTSTAMP", "LAST-MODIFIED", "CREATED"].includes(name)) {
    return formatIcalDate(value);
  }
  if (name === "ORGANIZER" || name.startsWith("ATTENDEE")) {
    return value.replace(/^MAILTO:/i, "");
  }
  return decodeText(value);
}

const COMP_ICON: Record<string, React.ReactNode> = {
  VEVENT: <CalendarDays className="size-4" />,
  VTODO: <ListTodo className="size-4" />,
  VJOURNAL: <BookOpen className="size-4" />,
  VCARD: <Contact className="size-4" />,
};

const COMP_LABEL: Record<string, string> = {
  VEVENT: "Event",
  VTODO: "Task",
  VJOURNAL: "Journal entry",
  VCARD: "Contact",
};

const VISIBLE_FIELDS_PRIORITY = [
  "DTSTART",
  "DTEND",
  "DUE",
  "LOCATION",
  "ORGANIZER",
  "DESCRIPTION",
  "STATUS",
  "PRIORITY",
  "EMAIL",
  "TEL",
  "ORG",
];

function ItemRow({
  item,
  onClick,
  expanded,
  onDelete,
  onCollapse,
}: {
  item: LoadedItem;
  onClick: () => void;
  expanded: boolean;
  onDelete: () => void;
  onCollapse: () => void;
}) {
  const comp = item.parsed;
  const compName = comp?.name ?? "VEVENT";
  const summary =
    comp && (getValue(comp, "SUMMARY") || getValue(comp, "FN") || getValue(comp, "DESCRIPTION").split("\n")[0]);

  // Pick a one-line metadata hint
  let hint: string | null = null;
  if (comp) {
    const dtstart = getValue(comp, "DTSTART");
    const due = getValue(comp, "DUE");
    const email = getValue(comp, "EMAIL");
    if (dtstart) hint = formatIcalDate(dtstart);
    else if (due) hint = `Due ${formatIcalDate(due)}`;
    else if (email) hint = email;
  }

  return (
    <Card className={expanded ? "ring-2 ring-[var(--color-primary)]/40" : "transition-shadow hover:shadow-md cursor-pointer"}>
      <button
        type="button"
        className="w-full text-left p-4 flex items-start gap-3"
        onClick={expanded ? onCollapse : onClick}
      >
        <div className="mt-0.5 flex size-8 items-center justify-center rounded-md bg-[var(--color-secondary)] text-[var(--color-secondary-foreground)] shrink-0">
          {COMP_ICON[compName] ?? <CalendarDays className="size-4" />}
        </div>
        <div className="min-w-0 flex-1">
          {item.loading ? (
            <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="size-3.5 animate-spin" /> Loading...
            </div>
          ) : item.error ? (
            <div className="text-sm text-[var(--color-destructive)]">{item.error}</div>
          ) : (
            <>
              <div className="font-medium truncate" title={summary || ""}>
                {summary || <span className="text-[var(--color-muted-foreground)]">(untitled)</span>}
              </div>
              <div className="mt-1 flex items-center gap-2 flex-wrap">
                <Badge variant="outline" className="gap-1">
                  {COMP_ICON[compName]} {COMP_LABEL[compName] ?? compName}
                </Badge>
                {hint && (
                  <span className="text-xs text-[var(--color-muted-foreground)]">{hint}</span>
                )}
              </div>
            </>
          )}
        </div>
        <ChevronRight
          className={`size-4 shrink-0 transition-transform text-[var(--color-muted-foreground)] ${expanded ? "rotate-90" : ""}`}
        />
      </button>

      {expanded && comp && (
        <CardContent className="border-t border-[var(--color-border)] pt-4 space-y-3">
          {VISIBLE_FIELDS_PRIORITY.map((fname) => {
            const p = getProp(comp, fname);
            if (!p?.value) return null;
            return (
              <div key={fname} className="flex items-start gap-2 text-sm">
                {fieldIcon(fname)}
                <div className="min-w-0 flex-1">
                  <div className="text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">
                    {fieldLabel(fname)}
                  </div>
                  <div className="whitespace-pre-wrap break-words">
                    {formatPropValue(fname, p.value)}
                  </div>
                </div>
              </div>
            );
          })}

          <details className="text-xs">
            <summary className="cursor-pointer text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
              Show raw {comp.name === "VCARD" ? "vCard" : "iCalendar"}
            </summary>
            <pre className="mt-2 overflow-x-auto rounded-md bg-[var(--color-muted)] p-3 text-[11px] leading-relaxed font-mono">
              {item.content}
            </pre>
          </details>

          <div className="flex justify-end pt-1">
            <Button
              size="sm"
              variant="ghost"
              className="text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10"
              onClick={onDelete}
            >
              <Trash2 /> Delete this item
            </Button>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

export function CollectionDetailView({ creds, collection, onBack }: Props) {
  const [items, setItems] = useState<LoadedItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedHref, setExpandedHref] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);

  const supportsItemAuthor =
    collection.type !== "ADDRESSBOOK" && collection.type !== "WEBCAL";

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      const metas = await listItems(creds, collection.href);
      // Show metadata immediately; eager-fetch contents in parallel.
      setItems(metas.map((meta) => ({ meta, loading: true })));
      const results = await Promise.allSettled(
        metas.map((meta) => getItem(creds, meta.href))
      );
      setItems(
        metas.map((meta, i) => {
          const r = results[i];
          if (r.status === "fulfilled") {
            const roots = parseIcal(r.value);
            return {
              meta,
              content: r.value,
              parsed: findItemComponent(roots),
              loading: false,
            };
          }
          return {
            meta,
            loading: false,
            error: r.reason instanceof Error ? r.reason.message : String(r.reason),
          };
        })
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collection.href]);

  function expandItem(href: string) {
    setExpandedHref(href);
  }

  async function handleDeleteItem(item: LoadedItem) {
    if (!confirm("Delete this item permanently?")) return;
    try {
      await deleteItem(creds, item.meta.href, item.meta.etag);
      setExpandedHref(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="min-h-screen bg-[var(--color-background)]">
      <header className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-background)]/80 backdrop-blur supports-[backdrop-filter]:bg-[var(--color-background)]/60">
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-3 flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={onBack} title="Back">
            <ArrowLeft />
          </Button>
          <div
            className="size-8 rounded-md shrink-0"
            style={{ background: collection.color || "var(--color-primary)" }}
          />
          <div className="min-w-0 flex-1">
            <div className="font-semibold leading-tight truncate">
              {collection.displayname || collection.href}
            </div>
            <div className="text-xs text-[var(--color-muted-foreground)] truncate">
              {items ? `${items.length} item${items.length === 1 ? "" : "s"}` : "Loading..."}
            </div>
          </div>
          {supportsItemAuthor && (
            <Button
              size="sm"
              onClick={() => setComposing(true)}
              title="Add a new event or task"
            >
              <Plus /> New
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={refresh}
            disabled={refreshing}
            title="Refresh"
          >
            <RefreshCw className={refreshing ? "animate-spin" : ""} />
          </Button>
        </div>
      </header>

      <ItemFormDialog
        open={composing}
        onClose={() => setComposing(false)}
        onDone={() => {
          setComposing(false);
          refresh();
        }}
        creds={creds}
        collection={collection}
      />

      <main className="mx-auto max-w-4xl px-4 sm:px-6 py-6 space-y-3">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {items === null ? (
          <div className="flex items-center justify-center py-20 text-[var(--color-muted-foreground)]">
            <Loader2 className="size-5 animate-spin mr-2" /> Loading items...
          </div>
        ) : items.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center text-center py-16 gap-3">
              <Inbox className="size-10 text-[var(--color-muted-foreground)]" />
              <div>
                <h2 className="font-semibold">No items yet</h2>
                <p className="text-sm text-[var(--color-muted-foreground)] mt-1">
                  {supportsItemAuthor
                    ? "Create your first item, or add some via your CalDAV/CardDAV client."
                    : "Add items using your CalDAV/CardDAV client."}
                </p>
              </div>
              {supportsItemAuthor && (
                <Button onClick={() => setComposing(true)}>
                  <Plus /> Add item
                </Button>
              )}
            </CardContent>
          </Card>
        ) : (
          items.map((it) => (
            <ItemRow
              key={it.meta.href}
              item={it}
              expanded={expandedHref === it.meta.href}
              onClick={() => expandItem(it.meta.href)}
              onCollapse={() => setExpandedHref(null)}
              onDelete={() => handleDeleteItem(it)}
            />
          ))
        )}
      </main>
    </div>
  );
}

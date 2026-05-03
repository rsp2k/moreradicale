import { useEffect, useState } from "react";
import {
  Bell,
  Check,
  X,
  ChevronDown,
  ChevronUp,
  CalendarPlus,
  CalendarX,
  Reply,
  Loader2,
} from "lucide-react";
import {
  listNotifications,
  acceptShare,
  declineShare,
  dismissNotification,
  type Credentials,
  type ShareNotification,
} from "@/lib/webdav";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";

interface Props {
  creds: Credentials;
  /**
   * Increment to ask the banner to re-fetch from the server (e.g. when the
   * parent's WebSocket fires a change event). Initial mount also fetches.
   */
  refreshNonce?: number;
  /** Called after a successful accept so the parent can refresh its collection list. */
  onChanged?: () => void;
}

function relativeTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const ms = Date.now() - d.getTime();
  const s = Math.round(ms / 1000);
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.round(h / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString();
}

function NotificationIcon({ kind }: { kind: ShareNotification["kind"] }) {
  if (kind === "invite-notification")
    return <CalendarPlus className="size-4 text-emerald-600" />;
  if (kind === "invite-deleted")
    return <CalendarX className="size-4 text-red-600" />;
  return <Reply className="size-4 text-blue-600" />;
}

function NotificationRow({
  n,
  busy,
  onAccept,
  onDecline,
  onDismiss,
}: {
  n: ShareNotification;
  busy: boolean;
  onAccept: () => void;
  onDecline: () => void;
  onDismiss: () => void;
}) {
  const isInvite = n.kind === "invite-notification";
  const sharerLabel = n.sharerCommonName || n.sharerUsername || "someone";
  const calendarLabel = n.sharedCollectionName || n.sharedCollectionPath || "a calendar";

  let title: React.ReactNode;
  let body: React.ReactNode = null;
  if (isInvite) {
    title = (
      <>
        <span className="font-medium">{sharerLabel}</span> shared{" "}
        <span className="font-medium">{calendarLabel}</span> with you
      </>
    );
    body = (
      <div className="flex items-center gap-2 mt-1">
        <Badge variant={n.accessLevel === "read-write" ? "default" : "secondary"}>
          {n.accessLevel === "read-write" ? "Can edit" : "Read only"}
        </Badge>
        {n.comment && (
          <span className="text-[var(--color-muted-foreground)] italic truncate">
            "{n.comment}"
          </span>
        )}
      </div>
    );
  } else if (n.kind === "invite-reply") {
    const who = n.replyFrom || "someone";
    const verb = n.replyStatus === "accepted" ? "accepted" : "declined";
    title = (
      <>
        <span className="font-medium">{who}</span> {verb} your share of{" "}
        <span className="font-medium">{calendarLabel}</span>
      </>
    );
  } else {
    title = (
      <>
        <span className="font-medium">{sharerLabel}</span> revoked your access to{" "}
        <span className="font-medium">{calendarLabel}</span>
      </>
    );
  }

  return (
    <li className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
      <div className="mt-0.5 shrink-0">
        <NotificationIcon kind={n.kind} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm leading-snug">{title}</div>
        {body}
        <div className="text-xs text-[var(--color-muted-foreground)] mt-1">
          {relativeTime(n.createdAt)}
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        {isInvite ? (
          <>
            <Button
              size="sm"
              variant="default"
              disabled={busy}
              onClick={onAccept}
              title="Accept invitation"
            >
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
              <span className="hidden sm:inline ml-1">Accept</span>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={onDecline}
              title="Decline invitation"
            >
              <X className="size-4" />
              <span className="hidden sm:inline ml-1">Decline</span>
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={onDismiss}
            title="Dismiss"
          >
            {busy ? <Loader2 className="size-4 animate-spin" /> : <X className="size-4" />}
          </Button>
        )}
      </div>
    </li>
  );
}

export function NotificationsBanner({ creds, refreshNonce, onChanged }: Props) {
  const [notifications, setNotifications] = useState<ShareNotification[] | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [busyHref, setBusyHref] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const list = await listNotifications(creds);
      setNotifications(list);
      // Auto-expand if there are pending invitations the user hasn't seen.
      // If everything is informational (replies/revocations), keep collapsed
      // until the user clicks - they're already aware of those changes.
      if (list.some((n) => n.kind === "invite-notification") && !expanded) {
        setExpanded(true);
      }
    } catch (e) {
      // Don't surface PROPFIND failures loudly - notifications are not critical
      // and 404 is normal for users with no shares yet (already handled in the
      // client). Anything else: log to console for debugging.
      console.debug("Failed to load notifications:", e);
      setNotifications([]);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [creds.user, refreshNonce]);

  async function handleAccept(n: ShareNotification) {
    setBusyHref(n.href);
    setError(null);
    try {
      await acceptShare(creds, n);
      await refresh();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyHref(null);
    }
  }

  async function handleDecline(n: ShareNotification) {
    setBusyHref(n.href);
    setError(null);
    try {
      await declineShare(creds, n);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyHref(null);
    }
  }

  async function handleDismiss(n: ShareNotification) {
    setBusyHref(n.href);
    setError(null);
    try {
      await dismissNotification(creds, n);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyHref(null);
    }
  }

  if (!notifications || notifications.length === 0) return null;

  const inviteCount = notifications.filter((n) => n.kind === "invite-notification").length;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden">
      <button
        onClick={() => setExpanded((x) => !x)}
        className="w-full flex items-center justify-between gap-2 px-4 py-3 hover:bg-[var(--color-accent)]/40 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <div className="relative">
            <Bell className="size-4" />
            {inviteCount > 0 && (
              <span className="absolute -top-1 -right-1 size-2 rounded-full bg-emerald-500" />
            )}
          </div>
          <span className="font-medium text-sm">
            {notifications.length} notification{notifications.length === 1 ? "" : "s"}
          </span>
          {inviteCount > 0 && (
            <Badge variant="default" className="ml-1">
              {inviteCount} pending invite{inviteCount === 1 ? "" : "s"}
            </Badge>
          )}
        </div>
        {expanded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)] px-4 py-2">
          {error && (
            <Alert variant="destructive" className="mb-2">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <ul className="divide-y divide-[var(--color-border)]/60">
            {notifications.map((n) => (
              <NotificationRow
                key={n.href}
                n={n}
                busy={busyHref === n.href}
                onAccept={() => handleAccept(n)}
                onDecline={() => handleDecline(n)}
                onDismiss={() => handleDismiss(n)}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}


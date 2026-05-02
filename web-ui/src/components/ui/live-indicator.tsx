import { cn } from "@/lib/utils";

type Mode = "ws" | "poll" | "paused";

interface Props {
  /** "ws" = real-time WebSocket connected.
   *  "poll" = polling fallback (WebSocket unavailable).
   *  "paused" = backgrounded tab. */
  mode: Mode;
  className?: string;
}

const COPY: Record<Mode, { label: string; title: string; dot: string }> = {
  ws: {
    label: "Live",
    title: "Real-time sync via WebSocket",
    dot: "bg-emerald-500",
  },
  poll: {
    label: "Polling",
    title: "Auto-refreshing every 30s (WebSocket unavailable)",
    dot: "bg-amber-500",
  },
  paused: {
    label: "Paused",
    title: "This tab is in the background",
    dot: "bg-[var(--color-muted-foreground)]/50",
  },
};

export function LiveIndicator({ mode, className }: Props) {
  const c = COPY[mode];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs text-[var(--color-muted-foreground)]",
        className
      )}
      title={c.title}
    >
      <span className="relative inline-flex size-2">
        {mode === "ws" && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500/70" />
        )}
        <span className={cn("relative inline-flex size-2 rounded-full", c.dot)} />
      </span>
      {c.label}
    </span>
  );
}

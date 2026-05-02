import { cn } from "@/lib/utils";

interface Props {
  active: boolean;
  className?: string;
}

/** Tiny "live" pill: green dot pulsing when active, gray when paused. */
export function LiveIndicator({ active, className }: Props) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs text-[var(--color-muted-foreground)]",
        className
      )}
      title={
        active
          ? "Live: auto-refreshing every 30 seconds"
          : "Paused: this tab is in the background"
      }
    >
      <span className="relative inline-flex size-2">
        {active && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500/70" />
        )}
        <span
          className={cn(
            "relative inline-flex size-2 rounded-full",
            active ? "bg-emerald-500" : "bg-[var(--color-muted-foreground)]/50"
          )}
        />
      </span>
      {active ? "Live" : "Paused"}
    </span>
  );
}

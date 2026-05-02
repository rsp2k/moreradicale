import { useEffect, useRef, useState } from "react";

interface PollOptions {
  /** Interval in milliseconds. Default 30s. */
  intervalMs?: number;
  /** Whether polling is enabled. Default true. */
  enabled?: boolean;
}

/**
 * Calls `tick` periodically while the tab is visible.
 *
 * Pauses when the page is hidden (browser tab in background) per the
 * Page Visibility API. Returns whether it's currently in the active
 * polling state, useful for a small "Live" indicator.
 */
export function usePoll(tick: () => void, opts: PollOptions = {}): boolean {
  const { intervalMs = 30_000, enabled = true } = opts;
  const tickRef = useRef(tick);
  const [active, setActive] = useState(false);

  // Keep the latest tick callback without re-running the effect.
  useEffect(() => {
    tickRef.current = tick;
  }, [tick]);

  useEffect(() => {
    if (!enabled) {
      setActive(false);
      return;
    }
    let timer: ReturnType<typeof setInterval> | null = null;

    function start() {
      if (timer) return;
      setActive(true);
      timer = setInterval(() => {
        tickRef.current();
      }, intervalMs);
    }
    function stop() {
      if (timer) clearInterval(timer);
      timer = null;
      setActive(false);
    }

    function onVisibility() {
      if (document.hidden) stop();
      else start();
    }

    if (document.hidden) setActive(false);
    else start();

    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [enabled, intervalMs]);

  return active;
}

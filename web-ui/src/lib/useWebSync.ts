import { useEffect, useRef, useState } from "react";
import type { Credentials } from "@/lib/webdav";

type Status = "connecting" | "open" | "fallback-poll";

interface Options {
  /** Path to subscribe to, e.g. "/admin/" or "/admin/calendar.ics/" */
  path: string;
  creds: Credentials;
  /** Called when the server pushes a change notification. */
  onChange: () => void;
  /** Polling fallback interval in ms when WebSocket fails (default 30s). */
  fallbackIntervalMs?: number;
}

/**
 * Real-time subscription via /.websync, with polling fallback.
 *
 * Returns a status string the UI can show via LiveIndicator:
 * - "open": WebSocket is connected and subscribed; server pushes
 * - "connecting": handshake in progress (or paused while tab is hidden)
 * - "fallback-poll": WebSocket failed; falling back to setInterval polling
 *
 * Pauses on document.hidden (Page Visibility API) and resumes on focus.
 */
export function useWebSync({
  path,
  creds,
  onChange,
  fallbackIntervalMs = 30_000,
}: Options): Status {
  const [status, setStatus] = useState<Status>("connecting");
  const onChangeRef = useRef(onChange);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;

    function startPolling(reason: string) {
      if (pollTimer) return;
      console.debug("useWebSync: polling fallback (%s)", reason);
      setStatus("fallback-poll");
      pollTimer = setInterval(() => {
        if (!document.hidden) onChangeRef.current();
      }, fallbackIntervalMs);
    }

    function stopPolling() {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
    }

    function connect() {
      if (cancelled) return;
      if (document.hidden) {
        // Don't open a connection while the tab is backgrounded.
        setStatus("connecting");
        return;
      }
      // Proxy auth path (creds.password === null): server reads
      // X-Remote-User from the upstream proxy.
      // htpasswd path: send first-message auth.
      const wsUrl = `wss://${window.location.host}/.websync`;
      try {
        ws = new WebSocket(wsUrl);
      } catch (e) {
        startPolling("WebSocket constructor threw");
        return;
      }
      setStatus("connecting");

      ws.onopen = () => {
        if (creds.password !== null) {
          ws!.send(
            JSON.stringify({
              action: "auth",
              user: creds.user,
              password: creds.password,
            })
          );
        }
        // Proxy auth: server already knows who we are; subscribe directly.
        if (creds.password === null) {
          ws!.send(JSON.stringify({ action: "subscribe", path }));
          setStatus("open");
        }
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.status === "authenticated") {
            ws!.send(JSON.stringify({ action: "subscribe", path }));
            setStatus("open");
          } else if (data.type === "change" || data.type === "delete" || data.type === "create" || data.type === "update") {
            onChangeRef.current();
          }
          // Ignore status:subscribed, action:pong, etc.
        } catch {
          // Malformed message - ignore.
        }
      };

      ws.onerror = () => {
        // Will be followed by onclose; let onclose decide whether to fall back.
      };

      ws.onclose = (e) => {
        ws = null;
        if (cancelled) return;
        // Distinguish "auth failed / server closed" (don't keep retrying)
        // from "transport hiccup" (try to reconnect).
        if (e.code === 1008 || e.code === 1003) {
          // Auth failure or service disabled - fall back to polling permanently.
          startPolling(`ws closed code=${e.code} (${e.reason || "no reason"})`);
        } else {
          // Reconnect after a short delay if still mounted and tab visible.
          setTimeout(() => {
            if (!cancelled && !document.hidden) connect();
          }, 1500);
        }
      };
    }

    function onVisibility() {
      if (cancelled) return;
      if (document.hidden) {
        if (ws) {
          ws.close(1000, "tab hidden");
          ws = null;
        }
        stopPolling();
        setStatus("connecting");
      } else {
        if (!ws) connect();
      }
    }

    document.addEventListener("visibilitychange", onVisibility);
    connect();

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
      stopPolling();
      if (ws) {
        ws.close(1000, "unmount");
        ws = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, creds.user, creds.password, fallbackIntervalMs]);

  return status;
}

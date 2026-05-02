import { useMemo } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { parseIcalDate } from "@/lib/ical";
import type { IcalComponent } from "@/lib/ical";

interface PlacedEvent {
  href: string;
  summary: string;
  start: Date;
  end: Date;
  allDay: boolean;
}

interface Props {
  /** All-day items separated from timed items in the rendering. */
  components: { href: string; comp: IcalComponent }[];
  /** Anchor date - the week shown contains this date. */
  anchor: Date;
  onAnchorChange: (d: Date) => void;
  onItemClick: (href: string) => void;
}

const HOUR_HEIGHT = 36; // px per hour
const DAY_START_HOUR = 6; // start grid at 6am
const DAY_END_HOUR = 23;  // end at 11pm
const VISIBLE_HOURS = DAY_END_HOUR - DAY_START_HOUR; // 17

const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function startOfWeek(d: Date): Date {
  const r = new Date(d);
  r.setHours(0, 0, 0, 0);
  r.setDate(r.getDate() - r.getDay()); // sunday-start
  return r;
}

function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function placeEvents(components: Props["components"]): PlacedEvent[] {
  const out: PlacedEvent[] = [];
  for (const { href, comp } of components) {
    if (comp.name !== "VEVENT") continue;
    const summary = comp.properties.find((p) => p.name === "SUMMARY")?.value || "(untitled)";
    const dtstartProp = comp.properties.find((p) => p.name === "DTSTART");
    const dtendProp = comp.properties.find((p) => p.name === "DTEND");
    if (!dtstartProp) continue;
    const allDay = dtstartProp.params["VALUE"] === "DATE";
    const start = parseIcalDate(dtstartProp.value);
    if (!start) continue;
    let end = dtendProp ? parseIcalDate(dtendProp.value) : null;
    if (!end) {
      // Default: 1 hour for timed, 1 day for all-day
      end = new Date(start);
      if (allDay) end.setDate(end.getDate() + 1);
      else end.setHours(end.getHours() + 1);
    }
    out.push({ href, summary, start, end, allDay });
  }
  return out;
}

function eventColor(href: string): string {
  // Stable hash → hue for visual differentiation
  let h = 0;
  for (let i = 0; i < href.length; i++) h = (h * 31 + href.charCodeAt(i)) | 0;
  return `hsl(${Math.abs(h) % 360}, 65%, 55%)`;
}

export function CalendarGridView({ components, anchor, onAnchorChange, onItemClick }: Props) {
  const events = useMemo(() => placeEvents(components), [components]);

  const weekStart = startOfWeek(anchor);
  const days = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));

  // Bucket events by day
  const allDayByDay: PlacedEvent[][] = days.map(() => []);
  const timedByDay: PlacedEvent[][] = days.map(() => []);
  for (const ev of events) {
    for (let i = 0; i < 7; i++) {
      if (sameDay(ev.start, days[i])) {
        if (ev.allDay) allDayByDay[i].push(ev);
        else timedByDay[i].push(ev);
        break;
      }
    }
  }

  const today = new Date();
  const monthLabel = weekStart.toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });

  return (
    <div className="space-y-3">
      {/* Toolbar: month label + prev/today/next */}
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{monthLabel}</h3>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onAnchorChange(addDays(weekStart, -7))}
            title="Previous week"
          >
            <ChevronLeft />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onAnchorChange(new Date())}
          >
            Today
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onAnchorChange(addDays(weekStart, 7))}
            title="Next week"
          >
            <ChevronRight />
          </Button>
        </div>
      </div>

      {/* Header: day-of-week + date */}
      <div className="grid border border-[var(--color-border)] rounded-md overflow-hidden bg-[var(--color-card)]">
        <div
          className="grid border-b border-[var(--color-border)]"
          style={{ gridTemplateColumns: "3rem repeat(7, 1fr)" }}
        >
          <div className="p-1.5 text-xs text-[var(--color-muted-foreground)]"></div>
          {days.map((d, i) => {
            const isToday = sameDay(d, today);
            return (
              <div
                key={i}
                className={`p-2 text-center text-xs border-l border-[var(--color-border)] ${
                  isToday ? "bg-[var(--color-primary)]/10" : ""
                }`}
              >
                <div className="text-[var(--color-muted-foreground)]">{DAY_LABELS[i]}</div>
                <div
                  className={`font-semibold mt-0.5 ${
                    isToday ? "text-[var(--color-primary)]" : ""
                  }`}
                >
                  {d.getDate()}
                </div>
              </div>
            );
          })}
        </div>

        {/* All-day strip (only show if any all-day events this week) */}
        {allDayByDay.some((day) => day.length > 0) && (
          <div
            className="grid border-b border-[var(--color-border)] bg-[var(--color-muted)]/30"
            style={{ gridTemplateColumns: "3rem repeat(7, 1fr)" }}
          >
            <div className="p-1.5 text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)] flex items-end pb-1">
              all-day
            </div>
            {allDayByDay.map((dayEvents, i) => (
              <div
                key={i}
                className="border-l border-[var(--color-border)] p-1 space-y-0.5 min-h-[2rem]"
              >
                {dayEvents.map((ev) => (
                  <button
                    key={ev.href}
                    type="button"
                    onClick={() => onItemClick(ev.href)}
                    className="block w-full text-left rounded px-1.5 py-0.5 text-xs text-white truncate hover:opacity-90"
                    style={{ background: eventColor(ev.href) }}
                    title={ev.summary}
                  >
                    {ev.summary}
                  </button>
                ))}
              </div>
            ))}
          </div>
        )}

        {/* Hour grid */}
        <div className="relative" style={{ height: VISIBLE_HOURS * HOUR_HEIGHT }}>
          <div
            className="absolute inset-0 grid"
            style={{ gridTemplateColumns: "3rem repeat(7, 1fr)" }}
          >
            {/* Hour labels column */}
            <div className="border-r border-[var(--color-border)] relative">
              {Array.from({ length: VISIBLE_HOURS }, (_, i) => {
                const h = DAY_START_HOUR + i;
                const label = new Date(2000, 0, 1, h).toLocaleTimeString(undefined, {
                  hour: "numeric",
                });
                return (
                  <div
                    key={i}
                    className="border-b border-[var(--color-border)] text-[10px] text-[var(--color-muted-foreground)] px-1 pt-0.5"
                    style={{ height: HOUR_HEIGHT }}
                  >
                    {label}
                  </div>
                );
              })}
            </div>

            {/* Day columns */}
            {days.map((d, dayIndex) => {
              const isToday = sameDay(d, today);
              return (
                <div
                  key={dayIndex}
                  className={`relative border-l border-[var(--color-border)] ${
                    isToday ? "bg-[var(--color-primary)]/5" : ""
                  }`}
                >
                  {/* Hour grid lines */}
                  {Array.from({ length: VISIBLE_HOURS }, (_, i) => (
                    <div
                      key={i}
                      className="border-b border-[var(--color-border)]/50"
                      style={{ height: HOUR_HEIGHT }}
                    />
                  ))}
                  {/* Events */}
                  {timedByDay[dayIndex].map((ev) => {
                    const startMin =
                      ev.start.getHours() * 60 + ev.start.getMinutes();
                    const endMin =
                      ev.end.getHours() * 60 + ev.end.getMinutes();
                    const top =
                      ((startMin - DAY_START_HOUR * 60) / 60) * HOUR_HEIGHT;
                    const height = Math.max(
                      18,
                      ((endMin - startMin) / 60) * HOUR_HEIGHT - 2
                    );
                    if (top < 0 || top > VISIBLE_HOURS * HOUR_HEIGHT) return null;
                    return (
                      <button
                        key={ev.href}
                        type="button"
                        onClick={() => onItemClick(ev.href)}
                        className="absolute left-0.5 right-0.5 rounded px-1 py-0.5 text-[11px] text-white text-left hover:opacity-90 overflow-hidden cursor-pointer"
                        style={{
                          top,
                          height,
                          background: eventColor(ev.href),
                        }}
                        title={`${ev.summary}\n${ev.start.toLocaleTimeString()} - ${ev.end.toLocaleTimeString()}`}
                      >
                        <div className="font-medium truncate">{ev.summary}</div>
                        {height > 30 && (
                          <div className="text-[10px] opacity-90 truncate">
                            {ev.start.toLocaleTimeString(undefined, {
                              hour: "numeric",
                              minute: "2-digit",
                            })}
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

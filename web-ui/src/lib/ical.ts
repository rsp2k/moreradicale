// Minimal iCalendar (RFC 5545) and vCard (RFC 6350) parser.
// Handles the common fields the admin UI needs for display.
// Doesn't try to be complete - real-world clients should use ical.js.

export interface IcalProperty {
  name: string;
  params: Record<string, string>;
  value: string;
}

export interface IcalComponent {
  name: string;
  properties: IcalProperty[];
  components: IcalComponent[];
}

/** Unfold lines per RFC 5545 §3.1: lines starting with space/tab continue prior. */
function unfoldLines(text: string): string[] {
  const raw = text.replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  for (const line of raw) {
    if (line.startsWith(" ") || line.startsWith("\t")) {
      if (out.length > 0) out[out.length - 1] += line.slice(1);
    } else {
      out.push(line);
    }
  }
  return out.filter((l) => l.length > 0);
}

function parseLine(line: string): IcalProperty {
  // NAME;PARAM=foo;PARAM2=bar:VALUE
  const colonIdx = line.indexOf(":");
  if (colonIdx === -1) return { name: line, params: {}, value: "" };
  const lhs = line.slice(0, colonIdx);
  const value = line.slice(colonIdx + 1);
  const segs = lhs.split(";");
  const name = segs[0].toUpperCase();
  const params: Record<string, string> = {};
  for (let i = 1; i < segs.length; i++) {
    const eq = segs[i].indexOf("=");
    if (eq === -1) continue;
    params[segs[i].slice(0, eq).toUpperCase()] = segs[i].slice(eq + 1);
  }
  return { name, params, value };
}

export function parseIcal(text: string): IcalComponent[] {
  const lines = unfoldLines(text);
  const stack: IcalComponent[] = [];
  const top: IcalComponent[] = [];
  for (const line of lines) {
    const prop = parseLine(line);
    if (prop.name === "BEGIN") {
      const c: IcalComponent = { name: prop.value.toUpperCase(), properties: [], components: [] };
      if (stack.length > 0) stack[stack.length - 1].components.push(c);
      else top.push(c);
      stack.push(c);
    } else if (prop.name === "END") {
      stack.pop();
    } else if (stack.length > 0) {
      stack[stack.length - 1].properties.push(prop);
    }
  }
  return top;
}

export function getProp(c: IcalComponent, name: string): IcalProperty | undefined {
  return c.properties.find((p) => p.name === name);
}

export function getValue(c: IcalComponent, name: string): string {
  return getProp(c, name)?.value ?? "";
}

/** Find the inner component (VEVENT, VTODO, VJOURNAL, or VCARD root). */
export function findItemComponent(roots: IcalComponent[]): IcalComponent | null {
  for (const root of roots) {
    if (root.name === "VCARD") return root;
    for (const c of root.components) {
      if (["VEVENT", "VTODO", "VJOURNAL"].includes(c.name)) return c;
    }
  }
  return null;
}

/** Parse iCal date/datetime: 19970714 or 19970714T173000Z or 19970714T173000.
 *
 * DATE-only values (no time) are parsed as LOCAL midnight to avoid the
 * timezone-shift bug where a date entered as "May 2" displays as "May 1"
 * for users west of UTC.
 */
export function parseIcalDate(value: string): Date | null {
  if (!value) return null;
  const m = value.match(/^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z?))?$/);
  if (!m) return null;
  const [, y, mo, d, h, mi, s, z] = m;
  if (!h) {
    return new Date(parseInt(y, 10), parseInt(mo, 10) - 1, parseInt(d, 10));
  }
  return new Date(`${y}-${mo}-${d}T${h}:${mi}:${s}${z || ""}`);
}

export function formatIcalDate(value: string): string {
  const d = parseIcalDate(value);
  if (!d || isNaN(d.getTime())) return value;
  const date = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  if (!value.includes("T")) return date;
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${date} · ${time}`;
}

/** Decode escape sequences from iCal/vCard text values. */
export function decodeText(value: string): string {
  return value
    .replace(/\\n/gi, "\n")
    .replace(/\\,/g, ",")
    .replace(/\\;/g, ";")
    .replace(/\\\\/g, "\\");
}

// -------- Serialization (for composing new items) --------

/** Escape text per RFC 5545 §3.3.11 for TEXT-typed values. */
export function encodeText(value: string): string {
  return value
    .replace(/\\/g, "\\\\")
    .replace(/;/g, "\\;")
    .replace(/,/g, "\\,")
    .replace(/\r\n|\r|\n/g, "\\n");
}

/** Format a Date as iCal UTC date-time: 20260515T140000Z */
export function formatIcalDateTimeUTC(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    d.getUTCFullYear().toString() +
    pad(d.getUTCMonth() + 1) +
    pad(d.getUTCDate()) +
    "T" +
    pad(d.getUTCHours()) +
    pad(d.getUTCMinutes()) +
    pad(d.getUTCSeconds()) +
    "Z"
  );
}

/** Format a Date as iCal DATE: 20260515 (no time component, all-day). */
export function formatIcalDateValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return d.getFullYear().toString() + pad(d.getMonth() + 1) + pad(d.getDate());
}

/**
 * Fold a single iCal content line per RFC 5545 §3.1: lines >75 chars
 * are split with CRLF + leading whitespace. Browsers/servers normalize,
 * but proper folding helps with stricter parsers.
 */
function foldLine(line: string): string {
  if (line.length <= 75) return line;
  const chunks: string[] = [];
  let i = 0;
  while (i < line.length) {
    const len = i === 0 ? 75 : 74; // continuation lines start with space
    chunks.push(line.slice(i, i + len));
    i += len;
  }
  return chunks.join("\r\n ");
}

/**
 * Common RRULE shorthand. Maps to a serialized RRULE: line.
 * - "" or "NONE": no recurrence (do not emit RRULE)
 * - "DAILY": every day
 * - "WEEKLY": every week on the day-of-week of DTSTART
 * - "WEEKDAYS": Monday through Friday
 * - "MONTHLY": every month on the day-of-month of DTSTART
 * - "YEARLY": every year on the date of DTSTART
 */
export type RecurrencePreset =
  | ""
  | "DAILY"
  | "WEEKLY"
  | "WEEKDAYS"
  | "MONTHLY"
  | "YEARLY";

export function serializeRrule(preset: RecurrencePreset): string | null {
  switch (preset) {
    case "DAILY": return "FREQ=DAILY";
    case "WEEKLY": return "FREQ=WEEKLY";
    case "WEEKDAYS": return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR";
    case "MONTHLY": return "FREQ=MONTHLY";
    case "YEARLY": return "FREQ=YEARLY";
    default: return null;
  }
}

/** Parse an RRULE value into a known preset, or null if it doesn't match. */
export function detectRrulePreset(rrule: string): RecurrencePreset | null {
  if (!rrule) return "";
  const parts = Object.fromEntries(
    rrule
      .split(";")
      .map((p) => p.split("="))
      .filter((kv) => kv.length === 2)
      .map(([k, v]) => [k.toUpperCase(), v.toUpperCase()])
  );
  const freq = parts["FREQ"];
  const byday = parts["BYDAY"];
  if (freq === "DAILY" && !byday) return "DAILY";
  if (freq === "WEEKLY" && !byday) return "WEEKLY";
  if (freq === "WEEKLY" && byday === "MO,TU,WE,TH,FR") return "WEEKDAYS";
  if (freq === "MONTHLY" && !byday) return "MONTHLY";
  if (freq === "YEARLY" && !byday) return "YEARLY";
  return null;
}

/** Human-readable label for an RRULE. Falls back to the raw rule for unknown patterns. */
export function describeRrule(rrule: string): string {
  const preset = detectRrulePreset(rrule);
  switch (preset) {
    case "": return "";
    case "DAILY": return "Repeats daily";
    case "WEEKLY": return "Repeats weekly";
    case "WEEKDAYS": return "Repeats every weekday";
    case "MONTHLY": return "Repeats monthly";
    case "YEARLY": return "Repeats yearly";
    case null: return `Repeats (${rrule})`;
  }
}

export interface VEventProps {
  uid: string;
  summary: string;
  dtstart: Date;
  dtend?: Date;
  allDay?: boolean;
  location?: string;
  description?: string;
  rrule?: string;
}

export interface VTodoProps {
  uid: string;
  summary: string;
  due?: Date;
  status?: "NEEDS-ACTION" | "IN-PROCESS" | "COMPLETED" | "CANCELLED";
  priority?: number;
  description?: string;
  rrule?: string;
}

export interface VJournalProps {
  uid: string;
  summary: string;
  dtstart: Date;
  allDay?: boolean;
  status?: "DRAFT" | "FINAL" | "CANCELLED";
  description?: string;
}

function dateProp(name: string, d: Date, allDay: boolean): string {
  return allDay
    ? `${name};VALUE=DATE:${formatIcalDateValue(d)}`
    : `${name}:${formatIcalDateTimeUTC(d)}`;
}

export function serializeVEvent(p: VEventProps): string {
  const now = new Date();
  const lines: string[] = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//moreradicale//web-ui//EN",
    "BEGIN:VEVENT",
    `UID:${p.uid}`,
    `DTSTAMP:${formatIcalDateTimeUTC(now)}`,
    `SUMMARY:${encodeText(p.summary)}`,
    dateProp("DTSTART", p.dtstart, !!p.allDay),
  ];
  if (p.dtend) lines.push(dateProp("DTEND", p.dtend, !!p.allDay));
  if (p.location) lines.push(`LOCATION:${encodeText(p.location)}`);
  if (p.description) lines.push(`DESCRIPTION:${encodeText(p.description)}`);
  if (p.rrule) lines.push(`RRULE:${p.rrule}`);
  lines.push("END:VEVENT", "END:VCALENDAR", "");
  return lines.map(foldLine).join("\r\n");
}

export function serializeVJournal(p: VJournalProps): string {
  const now = new Date();
  const lines: string[] = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//moreradicale//web-ui//EN",
    "BEGIN:VJOURNAL",
    `UID:${p.uid}`,
    `DTSTAMP:${formatIcalDateTimeUTC(now)}`,
    `SUMMARY:${encodeText(p.summary)}`,
    dateProp("DTSTART", p.dtstart, !!p.allDay),
  ];
  if (p.status) lines.push(`STATUS:${p.status}`);
  if (p.description) lines.push(`DESCRIPTION:${encodeText(p.description)}`);
  lines.push("END:VJOURNAL", "END:VCALENDAR", "");
  return lines.map(foldLine).join("\r\n");
}

export function serializeVTodo(p: VTodoProps): string {
  const now = new Date();
  const lines: string[] = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//moreradicale//web-ui//EN",
    "BEGIN:VTODO",
    `UID:${p.uid}`,
    `DTSTAMP:${formatIcalDateTimeUTC(now)}`,
    `SUMMARY:${encodeText(p.summary)}`,
  ];
  if (p.due) lines.push(`DUE:${formatIcalDateTimeUTC(p.due)}`);
  if (p.status) lines.push(`STATUS:${p.status}`);
  if (p.priority !== undefined) lines.push(`PRIORITY:${p.priority}`);
  if (p.description) lines.push(`DESCRIPTION:${encodeText(p.description)}`);
  if (p.rrule) lines.push(`RRULE:${p.rrule}`);
  lines.push("END:VTODO", "END:VCALENDAR", "");
  return lines.map(foldLine).join("\r\n");
}

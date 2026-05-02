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

/** Parse iCal date/datetime: 19970714 or 19970714T173000Z or 19970714T173000 */
export function parseIcalDate(value: string): Date | null {
  if (!value) return null;
  const m = value.match(/^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z?))?$/);
  if (!m) return null;
  const [, y, mo, d, h, mi, s, z] = m;
  if (!h) return new Date(`${y}-${mo}-${d}T00:00:00Z`);
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

export interface VEventProps {
  uid: string;
  summary: string;
  dtstart: Date;
  dtend?: Date;
  allDay?: boolean;
  location?: string;
  description?: string;
}

export interface VTodoProps {
  uid: string;
  summary: string;
  due?: Date;
  status?: "NEEDS-ACTION" | "IN-PROCESS" | "COMPLETED" | "CANCELLED";
  priority?: number;
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
  lines.push("END:VEVENT", "END:VCALENDAR", "");
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
  lines.push("END:VTODO", "END:VCALENDAR", "");
  return lines.map(foldLine).join("\r\n");
}

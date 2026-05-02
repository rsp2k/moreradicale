// WebDAV/CalDAV/CardDAV client for moreradicale admin UI.
// Ported from the legacy fn.js, narrowed to what the new UI needs.

export type CollectionType =
  | "ADDRESSBOOK"
  | "CALENDAR_JOURNAL_TASKS"
  | "CALENDAR_JOURNAL"
  | "CALENDAR_TASKS"
  | "JOURNAL_TASKS"
  | "CALENDAR"
  | "JOURNAL"
  | "TASKS"
  | "WEBCAL"
  | "PRINCIPAL";

export interface Collection {
  href: string;
  displayname: string;
  description: string;
  color: string;
  type: CollectionType;
  source: string;
  contentcount: string;
}

export interface Credentials {
  user: string;
  password: string;
}

const NS = {
  D: "DAV:",
  C: "urn:ietf:params:xml:ns:caldav",
  CR: "urn:ietf:params:xml:ns:carddav",
  CS: "http://calendarserver.org/ns/",
  ICAL: "http://apple.com/ns/ical/",
  RADICALE: "http://radicale.org/ns/",
};

const CRED_KEY = "moreradicale.creds";

export function loadCreds(): Credentials | null {
  const raw = sessionStorage.getItem(CRED_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Credentials;
  } catch {
    return null;
  }
}

export function saveCreds(creds: Credentials): void {
  sessionStorage.setItem(CRED_KEY, JSON.stringify(creds));
}

export function clearCreds(): void {
  sessionStorage.removeItem(CRED_KEY);
}

function authHeader(creds: Credentials): string {
  return "Basic " + btoa(`${creds.user}:${creds.password}`);
}

function parseXml(text: string): Document {
  return new DOMParser().parseFromString(text, "application/xml");
}

function ns(elem: Element, namespace: string, localName: string): Element | null {
  return elem.getElementsByTagNameNS(namespace, localName)[0] ?? null;
}

function nsAll(elem: Element, namespace: string, localName: string): Element[] {
  return Array.from(elem.getElementsByTagNameNS(namespace, localName));
}

function textOf(el: Element | null): string {
  return el?.textContent?.trim() ?? "";
}

/** Test credentials by issuing PROPFIND on the root principal URL. */
export async function login(creds: Credentials): Promise<{ ok: true; principal: string } | { ok: false; error: string }> {
  const url = `/${encodeURIComponent(creds.user)}/`;
  try {
    const res = await fetch(url, {
      method: "PROPFIND",
      headers: {
        Authorization: authHeader(creds),
        Depth: "0",
        "Content-Type": "application/xml; charset=utf-8",
      },
      body: `<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:"><prop><current-user-principal/></prop></propfind>`,
    });
    if (res.status === 401) return { ok: false, error: "Wrong username or password" };
    if (!res.ok) return { ok: false, error: `Server error: ${res.status} ${res.statusText}` };
    const xml = parseXml(await res.text());
    const href = textOf(ns(xml.documentElement, NS.D, "href"));
    return { ok: true, principal: href || url };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/** List the user's collections (calendars, address books, journals, tasks). */
export async function listCollections(creds: Credentials): Promise<Collection[]> {
  const url = `/${encodeURIComponent(creds.user)}/`;
  const body = `<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:" xmlns:C="${NS.C}" xmlns:CR="${NS.CR}" xmlns:CS="${NS.CS}" xmlns:I="${NS.ICAL}" xmlns:R="${NS.RADICALE}">
  <prop>
    <resourcetype/>
    <displayname/>
    <CS:getctag/>
    <R:getcontentcount/>
    <I:calendar-color/>
    <C:supported-calendar-component-set/>
    <C:calendar-description/>
    <CR:addressbook-description/>
    <CS:source/>
  </prop>
</propfind>`;
  const res = await fetch(url, {
    method: "PROPFIND",
    headers: {
      Authorization: authHeader(creds),
      Depth: "1",
      "Content-Type": "application/xml; charset=utf-8",
    },
    body,
  });
  if (!res.ok) throw new Error(`PROPFIND failed: ${res.status} ${res.statusText}`);
  const xml = parseXml(await res.text());
  const collections: Collection[] = [];
  for (const response of nsAll(xml.documentElement, NS.D, "response")) {
    const href = textOf(ns(response, NS.D, "href"));
    if (href === url || href === url.replace(/\/$/, "")) continue;
    const propstat = ns(response, NS.D, "propstat");
    if (!propstat) continue;
    const prop = ns(propstat, NS.D, "prop");
    if (!prop) continue;
    const resourceType = ns(prop, NS.D, "resourcetype");
    const isAddressbook = resourceType && ns(resourceType, NS.CR, "addressbook");
    const isCalendar = resourceType && ns(resourceType, NS.C, "calendar");
    if (!isAddressbook && !isCalendar) continue;

    let type: CollectionType = "ADDRESSBOOK";
    if (isCalendar) {
      const supported = ns(prop, NS.C, "supported-calendar-component-set");
      const comps = supported ? nsAll(supported, NS.C, "comp").map((c) => c.getAttribute("name")) : [];
      const has = (n: string) => comps.includes(n);
      if (has("VEVENT") && has("VJOURNAL") && has("VTODO")) type = "CALENDAR_JOURNAL_TASKS";
      else if (has("VEVENT") && has("VJOURNAL")) type = "CALENDAR_JOURNAL";
      else if (has("VEVENT") && has("VTODO")) type = "CALENDAR_TASKS";
      else if (has("VJOURNAL") && has("VTODO")) type = "JOURNAL_TASKS";
      else if (has("VEVENT")) type = "CALENDAR";
      else if (has("VJOURNAL")) type = "JOURNAL";
      else if (has("VTODO")) type = "TASKS";
      const source = textOf(ns(prop, NS.CS, "source"));
      if (source) type = "WEBCAL";
    }

    collections.push({
      href,
      displayname: textOf(ns(prop, NS.D, "displayname")),
      description: isCalendar
        ? textOf(ns(prop, NS.C, "calendar-description"))
        : textOf(ns(prop, NS.CR, "addressbook-description")),
      color: textOf(ns(prop, NS.ICAL, "calendar-color")) || "",
      type,
      source: textOf(ns(prop, NS.CS, "source")),
      contentcount: textOf(ns(prop, NS.RADICALE, "getcontentcount")) || "0",
    });
  }
  collections.sort((a, b) => a.displayname.localeCompare(b.displayname));
  return collections;
}

export async function deleteCollection(creds: Credentials, href: string): Promise<void> {
  const res = await fetch(href, {
    method: "DELETE",
    headers: { Authorization: authHeader(creds) },
  });
  if (!res.ok) throw new Error(`DELETE failed: ${res.status} ${res.statusText}`);
}

/** Build the public URL for sharing/copying. */
export function publicUrlFor(href: string): string {
  const base = `${window.location.origin}`;
  return base + href;
}

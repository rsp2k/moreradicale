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
  /** null when auth is delegated to the reverse proxy (Authentik, etc.). */
  password: string | null;
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

/**
 * Authorization header for a credentials object.
 * Returns null when password is null (proxy/header auth) - the caller
 * should omit the Authorization header entirely in that case so the
 * upstream proxy can inject its own.
 */
function authHeader(creds: Credentials): string | null {
  if (creds.password === null) return null;
  return "Basic " + btoa(`${creds.user}:${creds.password}`);
}

/** Build fetch headers with optional auth. */
function withAuth(creds: Credentials, headers: Record<string, string> = {}): Record<string, string> {
  const a = authHeader(creds);
  if (a) headers["Authorization"] = a;
  return headers;
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
      headers: withAuth(creds, {
        Depth: "0",
        "Content-Type": "application/xml; charset=utf-8",
      }),
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

/**
 * Detect proxy/header authentication.
 *
 * Sends an unauthenticated PROPFIND for the site root. If the upstream
 * proxy (Authentik via Caddy, etc.) has already authenticated the
 * request and moreradicale is configured with one of the header-auth
 * backends (http_x_remote_user, http_remote_user, remote_user), the
 * response will contain a current-user-principal we can use. Otherwise
 * we get a 401 and the caller falls back to the Basic Auth login form.
 */
export async function detectProxiedSession(): Promise<Credentials | null> {
  try {
    const res = await fetch("/", {
      method: "PROPFIND",
      headers: {
        Depth: "0",
        "Content-Type": "application/xml; charset=utf-8",
      },
      body: `<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:"><prop><current-user-principal/></prop></propfind>`,
    });
    if (!res.ok) return null;
    const xml = parseXml(await res.text());
    const principalHref = textOf(ns(xml.documentElement, NS.D, "href"));
    // Walk responses for current-user-principal
    let principal = "";
    for (const response of nsAll(xml.documentElement, NS.D, "response")) {
      const cup = ns(response, NS.D, "current-user-principal");
      if (cup) {
        principal = textOf(ns(cup, NS.D, "href"));
        if (principal) break;
      }
    }
    if (!principal) principal = principalHref;
    if (!principal) return null;
    // principal looks like "/alice/" - extract the user segment
    const user = principal.replace(/^\/+|\/+$/g, "").split("/")[0];
    if (!user || user.startsWith(".")) return null;
    return { user: decodeURIComponent(user), password: null };
  } catch {
    return null;
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
      ...withAuth(creds),
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
    headers: withAuth(creds),
  });
  if (!res.ok) throw new Error(`DELETE failed: ${res.status} ${res.statusText}`);
}

/** Build the public URL for sharing/copying. */
export function publicUrlFor(href: string): string {
  const base = `${window.location.origin}`;
  return base + href;
}

export interface CollectionItem {
  href: string;
  etag: string;
  contentType: string;
  size: number;
}

/** List items inside a collection via PROPFIND depth=1. */
export async function listItems(creds: Credentials, collectionHref: string): Promise<CollectionItem[]> {
  const body = `<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:">
  <prop>
    <getetag/>
    <getcontenttype/>
    <getcontentlength/>
    <resourcetype/>
  </prop>
</propfind>`;
  const res = await fetch(collectionHref, {
    method: "PROPFIND",
    headers: {
      ...withAuth(creds),
      Depth: "1",
      "Content-Type": "application/xml; charset=utf-8",
    },
    body,
  });
  if (!res.ok) throw new Error(`PROPFIND failed: ${res.status} ${res.statusText}`);
  const xml = parseXml(await res.text());
  const items: CollectionItem[] = [];
  const normalizedColl = collectionHref.replace(/\/+$/, "");
  for (const response of nsAll(xml.documentElement, NS.D, "response")) {
    const href = textOf(ns(response, NS.D, "href"));
    if (!href) continue;
    if (href.replace(/\/+$/, "") === normalizedColl) continue;
    const propstat = ns(response, NS.D, "propstat");
    if (!propstat) continue;
    const prop = ns(propstat, NS.D, "prop");
    if (!prop) continue;
    // Skip if this is itself a collection (e.g. nested)
    const rt = ns(prop, NS.D, "resourcetype");
    if (rt && ns(rt, NS.D, "collection")) continue;
    items.push({
      href,
      etag: textOf(ns(prop, NS.D, "getetag")).replace(/^"|"$/g, ""),
      contentType: textOf(ns(prop, NS.D, "getcontenttype")),
      size: parseInt(textOf(ns(prop, NS.D, "getcontentlength")), 10) || 0,
    });
  }
  items.sort((a, b) => a.href.localeCompare(b.href));
  return items;
}

/** Fetch the raw .ics or .vcf body of an item. */
export async function getItem(creds: Credentials, href: string): Promise<string> {
  const res = await fetch(href, {
    method: "GET",
    headers: withAuth(creds),
  });
  if (!res.ok) throw new Error(`GET failed: ${res.status} ${res.statusText}`);
  return await res.text();
}

/** Delete a single item. */
export async function deleteItem(creds: Credentials, href: string, etag?: string): Promise<void> {
  const extra: Record<string, string> = {};
  if (etag) extra["If-Match"] = `"${etag}"`;
  const res = await fetch(href, { method: "DELETE", headers: withAuth(creds, extra) });
  if (!res.ok) throw new Error(`DELETE failed: ${res.status} ${res.statusText}`);
}

export interface CollectionProps {
  displayname: string;
  description: string;
  color: string;
  /** Webcal source URL, only used for type=WEBCAL */
  source?: string;
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function calendarComponents(type: CollectionType): string[] {
  switch (type) {
    case "CALENDAR_JOURNAL_TASKS": return ["VEVENT", "VJOURNAL", "VTODO"];
    case "CALENDAR_JOURNAL": return ["VEVENT", "VJOURNAL"];
    case "CALENDAR_TASKS": return ["VEVENT", "VTODO"];
    case "JOURNAL_TASKS": return ["VJOURNAL", "VTODO"];
    case "CALENDAR": return ["VEVENT"];
    case "JOURNAL": return ["VJOURNAL"];
    case "TASKS": return ["VTODO"];
    case "WEBCAL": return ["VEVENT"];
    default: return [];
  }
}

/** Generate a UUID v4 for new collection hrefs. */
export function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Fallback
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/** Create a new collection (calendar or address book) under the user's principal. */
export async function createCollection(
  creds: Credentials,
  type: CollectionType,
  href: string,
  props: CollectionProps
): Promise<void> {
  if (type === "PRINCIPAL") throw new Error("Cannot create principal collections");

  const collectionUrl = `/${encodeURIComponent(creds.user)}/${encodeURI(href.replace(/^\/|\/$/g, ""))}/`;
  const isAddressbook = type === "ADDRESSBOOK";
  const method = isAddressbook ? "MKCOL" : "MKCALENDAR";

  const propParts: string[] = [];
  if (props.displayname) {
    propParts.push(`<displayname>${escapeXml(props.displayname)}</displayname>`);
  }
  if (props.color) {
    propParts.push(`<I:calendar-color>${escapeXml(props.color)}</I:calendar-color>`);
  }
  if (props.description) {
    if (isAddressbook) {
      propParts.push(`<CR:addressbook-description>${escapeXml(props.description)}</CR:addressbook-description>`);
    } else {
      propParts.push(`<C:calendar-description>${escapeXml(props.description)}</C:calendar-description>`);
    }
  }
  if (type === "WEBCAL" && props.source) {
    propParts.push(`<CS:source><href>${escapeXml(props.source)}</href></CS:source>`);
  }

  let resourceTypeAndProps: string;
  if (isAddressbook) {
    resourceTypeAndProps = `
        <resourcetype><collection/><CR:addressbook/></resourcetype>
        ${propParts.join("\n        ")}`;
  } else {
    const comps = calendarComponents(type)
      .map((c) => `<C:comp name="${c}"/>`)
      .join("");
    resourceTypeAndProps = `
        ${propParts.join("\n        ")}
        <C:supported-calendar-component-set>${comps}</C:supported-calendar-component-set>`;
  }

  const body = `<?xml version="1.0" encoding="utf-8"?>
<${isAddressbook ? "create" : "C:mkcalendar"} xmlns="DAV:" xmlns:C="${NS.C}" xmlns:CR="${NS.CR}" xmlns:CS="${NS.CS}" xmlns:I="${NS.ICAL}">
  <set>
    <prop>${resourceTypeAndProps}
    </prop>
  </set>
</${isAddressbook ? "create" : "C:mkcalendar"}>`;

  const res = await fetch(collectionUrl, {
    method,
    headers: {
      ...withAuth(creds),
      "Content-Type": "application/xml; charset=utf-8",
    },
    body,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${method} failed: ${res.status} ${res.statusText} ${text.slice(0, 200)}`);
  }
}

/** Update displayname / description / color on an existing collection. */
export async function updateCollectionProps(
  creds: Credentials,
  href: string,
  type: CollectionType,
  props: Partial<CollectionProps>
): Promise<void> {
  const isAddressbook = type === "ADDRESSBOOK";
  const setParts: string[] = [];
  const removeParts: string[] = [];

  function setOrRemove(value: string | undefined, xml: string, removeXml: string) {
    if (value === undefined) return;
    if (value === "") removeParts.push(removeXml);
    else setParts.push(xml);
  }

  setOrRemove(
    props.displayname,
    `<displayname>${escapeXml(props.displayname ?? "")}</displayname>`,
    "<displayname/>"
  );
  setOrRemove(
    props.color,
    `<I:calendar-color>${escapeXml(props.color ?? "")}</I:calendar-color>`,
    "<I:calendar-color/>"
  );
  if (isAddressbook) {
    setOrRemove(
      props.description,
      `<CR:addressbook-description>${escapeXml(props.description ?? "")}</CR:addressbook-description>`,
      "<CR:addressbook-description/>"
    );
  } else {
    setOrRemove(
      props.description,
      `<C:calendar-description>${escapeXml(props.description ?? "")}</C:calendar-description>`,
      "<C:calendar-description/>"
    );
  }
  if (props.source !== undefined) {
    if (props.source === "") removeParts.push("<CS:source/>");
    else setParts.push(`<CS:source><href>${escapeXml(props.source)}</href></CS:source>`);
  }

  const sections: string[] = [];
  if (setParts.length) sections.push(`<set><prop>${setParts.join("")}</prop></set>`);
  if (removeParts.length) sections.push(`<remove><prop>${removeParts.join("")}</prop></remove>`);
  if (!sections.length) return;

  const body = `<?xml version="1.0" encoding="utf-8"?>
<propertyupdate xmlns="DAV:" xmlns:C="${NS.C}" xmlns:CR="${NS.CR}" xmlns:CS="${NS.CS}" xmlns:I="${NS.ICAL}">
  ${sections.join("")}
</propertyupdate>`;

  const res = await fetch(href, {
    method: "PROPPATCH",
    headers: {
      ...withAuth(creds),
      "Content-Type": "application/xml; charset=utf-8",
    },
    body,
  });
  if (!res.ok) {
    throw new Error(`PROPPATCH failed: ${res.status} ${res.statusText}`);
  }
}

/** Upload a single .ics or .vcf body into a collection. */
export async function uploadItem(
  creds: Credentials,
  collectionHref: string,
  filename: string,
  content: string,
  contentType: string
): Promise<{ href: string; status: number }> {
  const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, "_") || `${uuid()}.ics`;
  const itemHref = `${collectionHref.replace(/\/$/, "")}/${encodeURIComponent(safeName)}`;
  const res = await fetch(itemHref, {
    method: "PUT",
    headers: {
      ...withAuth(creds),
      "Content-Type": contentType,
    },
    body: content,
  });
  if (!res.ok && res.status !== 201 && res.status !== 204) {
    const text = await res.text();
    throw new Error(`PUT ${safeName} failed: ${res.status} ${res.statusText} ${text.slice(0, 200)}`);
  }
  return { href: itemHref, status: res.status };
}

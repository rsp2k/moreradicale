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
  /** Set when the collection is shared with the current user, not owned. */
  sharedBy?: string;
  /** "read" or "read-write" - only set when sharedBy is set. */
  sharedAccess?: ShareAccess;
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
  // 3-second cap so a slow / hanging probe (rate-limited htpasswd, dead
  // upstream, etc.) doesn't keep the UI stuck in the "detecting" phase.
  // On timeout we abort, the catch returns null, and LoginView renders.
  const ctrl = new AbortController();
  const timeoutId = setTimeout(() => ctrl.abort(), 3000);
  try {
    const res = await fetch("/", {
      method: "PROPFIND",
      headers: {
        Depth: "0",
        "Content-Type": "application/xml; charset=utf-8",
        // Empty-Basic stub ("Og==" decodes to ":"). Two effects:
        //   1. Suppresses the browser's native Basic-auth popup. Browsers
        //      only auto-prompt on 401+WWW-Authenticate when the request
        //      had no Authorization header; setting one (even bogus)
        //      tells the browser "creds were already attempted, don't ask".
        //   2. moreradicale's app/__init__.py:559 short-circuits when the
        //      decoded login is empty - the htpasswd backend never runs,
        //      so this probe doesn't accumulate failed-login rate-limit
        //      delay for legitimate htpasswd users on the same server.
        // With http_x_remote_user (proxy auth), the Authorization header
        // is ignored entirely in favor of X-Remote-User, so the probe
        // succeeds and we get a real principal back.
        Authorization: "Basic Og==",
      },
      body: `<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:"><prop><current-user-principal/></prop></propfind>`,
      signal: ctrl.signal,
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
  } finally {
    clearTimeout(timeoutId);
  }
}

const COLLECTION_PROPS_BODY = `<?xml version="1.0" encoding="utf-8"?>
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

/** Parse one <D:response> from a PROPFIND multistatus into a Collection. */
function parseCollectionResponse(response: Element): Collection | null {
  const href = textOf(ns(response, NS.D, "href"));
  if (!href) return null;
  const propstat = ns(response, NS.D, "propstat");
  if (!propstat) return null;
  const prop = ns(propstat, NS.D, "prop");
  if (!prop) return null;
  const resourceType = ns(prop, NS.D, "resourcetype");
  const isAddressbook = resourceType && ns(resourceType, NS.CR, "addressbook");
  const isCalendar = resourceType && ns(resourceType, NS.C, "calendar");
  if (!isAddressbook && !isCalendar) return null;

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

  return {
    href,
    displayname: textOf(ns(prop, NS.D, "displayname")),
    description: isCalendar
      ? textOf(ns(prop, NS.C, "calendar-description"))
      : textOf(ns(prop, NS.CR, "addressbook-description")),
    color: textOf(ns(prop, NS.ICAL, "calendar-color")) || "",
    type,
    source: textOf(ns(prop, NS.CS, "source")),
    contentcount: textOf(ns(prop, NS.RADICALE, "getcontentcount")) || "0",
  };
}

/**
 * List the user's collections (calendars, address books, journals, tasks).
 *
 * moreradicale's storage layer auto-includes shared calendars (status =
 * accepted) in a principal's depth=1 listing - see
 * `storage/multifilesystem/discover.py:128`. So this PROPFIND returns both
 * owned and shared collections in one round trip. We classify each entry by
 * comparing the leading path segment of its href against the current user;
 * mismatches are tagged with `sharedBy` so the UI can render the borrowed
 * calendar with a "Shared by" badge and hide owner-only actions.
 */
export async function listCollections(creds: Credentials): Promise<Collection[]> {
  const url = `/${encodeURIComponent(creds.user)}/`;
  const res = await fetch(url, {
    method: "PROPFIND",
    headers: {
      ...withAuth(creds),
      Depth: "1",
      "Content-Type": "application/xml; charset=utf-8",
    },
    body: COLLECTION_PROPS_BODY,
  });
  if (!res.ok) throw new Error(`PROPFIND failed: ${res.status} ${res.statusText}`);
  const xml = parseXml(await res.text());
  const collections: Collection[] = [];
  const userSegment = creds.user;
  for (const response of nsAll(xml.documentElement, NS.D, "response")) {
    const href = textOf(ns(response, NS.D, "href"));
    if (href === url || href === url.replace(/\/$/, "")) continue;
    const c = parseCollectionResponse(response);
    if (!c) continue;
    // First non-empty segment of the href is the owning principal.
    // If it doesn't match the current user, this is a borrowed share.
    const firstSeg = c.href.split("/").filter(Boolean)[0] ?? "";
    if (decodeURIComponent(firstSeg) !== userSegment) {
      c.sharedBy = decodeURIComponent(firstSeg);
    }
    collections.push(c);
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

// -------- Sharing (CalendarServer extension) --------

export type ShareAccess = "read" | "read-write";
export type InviteStatus = "accepted" | "declined" | "noresponse";

export interface Share {
  sharee: string; // username (extracted from /sharee/)
  href: string; // full principal href, e.g. "/bob/"
  commonName: string;
  access: ShareAccess;
  status: InviteStatus;
}

/** List current shares on a calendar collection via PROPFIND CS:invite. */
export async function listShares(creds: Credentials, collectionHref: string): Promise<Share[]> {
  const body = `<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:" xmlns:CS="${NS.CS}">
  <prop><CS:invite/></prop>
</propfind>`;
  const res = await fetch(collectionHref, {
    method: "PROPFIND",
    headers: withAuth(creds, {
      Depth: "0",
      "Content-Type": "application/xml; charset=utf-8",
    }),
    body,
  });
  if (!res.ok) throw new Error(`PROPFIND failed: ${res.status} ${res.statusText}`);
  const xml = parseXml(await res.text());
  const invite = ns(xml.documentElement, NS.CS, "invite");
  if (!invite) return [];
  const shares: Share[] = [];
  for (const userEl of nsAll(invite, NS.CS, "user")) {
    const href = textOf(ns(userEl, NS.D, "href"));
    if (!href) continue;
    const sharee = href.replace(/^\/+|\/+$/g, "").split("/").pop() ?? "";
    const cn = textOf(ns(userEl, NS.CS, "common-name"));
    let status: InviteStatus = "noresponse";
    if (ns(userEl, NS.CS, "invite-accepted")) status = "accepted";
    else if (ns(userEl, NS.CS, "invite-declined")) status = "declined";
    let access: ShareAccess = "read";
    const accessEl = ns(userEl, NS.CS, "access");
    if (accessEl && ns(accessEl, NS.CS, "read-write")) access = "read-write";
    shares.push({ sharee, href, commonName: cn, access, status });
  }
  return shares;
}

/** Add or update a share on a calendar collection (CS:share-resource POST). */
export async function addShare(
  creds: Credentials,
  collectionHref: string,
  sharee: string,
  access: ShareAccess,
  summary: string = ""
): Promise<void> {
  const accessTag = access === "read-write" ? "CS:read-write" : "CS:read";
  const body = `<?xml version="1.0" encoding="utf-8"?>
<CS:share-resource xmlns="DAV:" xmlns:CS="${NS.CS}">
  <CS:set>
    <href>/${encodeURIComponent(sharee)}/</href>
    ${summary ? `<CS:summary>${escapeXml(summary)}</CS:summary>` : ""}
    <${accessTag}/>
  </CS:set>
</CS:share-resource>`;
  const res = await fetch(collectionHref, {
    method: "POST",
    headers: withAuth(creds, {
      "Content-Type": "application/xml; charset=utf-8",
    }),
    body,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Share failed: ${res.status} ${res.statusText} ${text.slice(0, 200)}`);
  }
}

/** Remove a share (CS:share-resource POST with CS:remove). */
export async function removeShare(
  creds: Credentials,
  collectionHref: string,
  sharee: string
): Promise<void> {
  const body = `<?xml version="1.0" encoding="utf-8"?>
<CS:share-resource xmlns="DAV:" xmlns:CS="${NS.CS}">
  <CS:remove>
    <href>/${encodeURIComponent(sharee)}/</href>
  </CS:remove>
</CS:share-resource>`;
  const res = await fetch(collectionHref, {
    method: "POST",
    headers: withAuth(creds, {
      "Content-Type": "application/xml; charset=utf-8",
    }),
    body,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Remove share failed: ${res.status} ${res.statusText} ${text.slice(0, 200)}`);
  }
}

// -------- Sharing notifications (CalendarServer extension) --------

export type NotificationKind = "invite-notification" | "invite-reply" | "invite-deleted";

export interface ShareNotification {
  /** Path to the notification subcollection, e.g. "/bob/notifications/invite-{uid}.xml/". */
  href: string;
  /** Notification UID from the metadata blob (NOT the same as the href filename). */
  uid: string;
  kind: NotificationKind;
  createdAt: string;
  /** Path to the calendar being shared, e.g. "alice/work-calendar". */
  sharedCollectionPath: string | null;
  sharedCollectionName: string | null;
  sharerUsername: string | null;
  sharerCommonName: string | null;
  /** "read" or "read-write" - only set on invite-notification. */
  accessLevel: ShareAccess | null;
  /** Who replied - only set on invite-reply. */
  replyFrom: string | null;
  /** "accepted" or "declined" - only set on invite-reply. */
  replyStatus: "accepted" | "declined" | null;
  comment: string | null;
}

interface NotificationDict {
  uid?: string;
  type?: string;
  created_at?: string;
  shared_collection_path?: string | null;
  shared_collection_name?: string | null;
  sharer_username?: string | null;
  sharer_cn?: string | null;
  access_level?: string | null;
  reply_from?: string | null;
  reply_status?: string | null;
  comment?: string | null;
}

/**
 * List sharing notifications for the user.
 *
 * The server stores each notification as a child collection under
 * /{user}/notifications/, with a JSON metadata blob in the
 * RADICALE:notifications property. We PROPFIND depth=1 and pull out
 * the JSON blobs, sorted newest-first.
 */
export async function listNotifications(creds: Credentials): Promise<ShareNotification[]> {
  const url = `/${encodeURIComponent(creds.user)}/notifications/`;
  const body = `<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:" xmlns:R="${NS.RADICALE}">
  <prop>
    <R:notifications/>
    <resourcetype/>
  </prop>
</propfind>`;
  const res = await fetch(url, {
    method: "PROPFIND",
    headers: withAuth(creds, {
      Depth: "1",
      "Content-Type": "application/xml; charset=utf-8",
    }),
    body,
  });
  // Notification collection might not exist yet (no shares ever received).
  // 404 is normal in that case - return empty list rather than throwing.
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`PROPFIND failed: ${res.status} ${res.statusText}`);
  const xml = parseXml(await res.text());
  const notifications: ShareNotification[] = [];
  const collectionUrl = url.replace(/\/+$/, "");
  for (const response of nsAll(xml.documentElement, NS.D, "response")) {
    const href = textOf(ns(response, NS.D, "href"));
    if (!href) continue;
    if (href.replace(/\/+$/, "") === collectionUrl) continue;
    const propstat = ns(response, NS.D, "propstat");
    if (!propstat) continue;
    const prop = ns(propstat, NS.D, "prop");
    if (!prop) continue;
    const blob = textOf(ns(prop, NS.RADICALE, "notifications"));
    if (!blob) continue;
    let data: NotificationDict;
    try {
      data = JSON.parse(blob);
    } catch {
      continue;
    }
    const kind = data.type as NotificationKind | undefined;
    if (kind !== "invite-notification" && kind !== "invite-reply" && kind !== "invite-deleted") {
      continue;
    }
    const access = data.access_level === "read-write" ? "read-write" : data.access_level === "read" ? "read" : null;
    const replyStatus = data.reply_status === "accepted" || data.reply_status === "declined" ? data.reply_status : null;
    notifications.push({
      href,
      uid: data.uid ?? "",
      kind,
      createdAt: data.created_at ?? "",
      sharedCollectionPath: data.shared_collection_path ?? null,
      sharedCollectionName: data.shared_collection_name ?? null,
      sharerUsername: data.sharer_username ?? null,
      sharerCommonName: data.sharer_cn ?? null,
      accessLevel: access,
      replyFrom: data.reply_from ?? null,
      replyStatus,
      comment: data.comment ?? null,
    });
  }
  // Newest first
  notifications.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return notifications;
}

/** Build href for the shared calendar from a stored path like "alice/work". */
function sharedCalendarHref(sharedCollectionPath: string): string {
  const trimmed = sharedCollectionPath.replace(/^\/+|\/+$/g, "");
  // Path segments are already URL-safe identifiers, but encode each component
  // to be safe with display-name slugs that contain non-ASCII.
  const encoded = trimmed.split("/").map(encodeURIComponent).join("/");
  return `/${encoded}/`;
}

async function postShareReply(
  creds: Credentials,
  collectionHref: string,
  inReplyToUid: string,
  accept: boolean
): Promise<void> {
  const decision = accept ? "<CS:invite-accepted/>" : "<CS:invite-declined/>";
  const body = `<?xml version="1.0" encoding="utf-8"?>
<CS:share-reply xmlns="DAV:" xmlns:CS="${NS.CS}">
  <CS:href>${escapeXml(collectionHref)}</CS:href>
  <CS:in-reply-to>${escapeXml(inReplyToUid)}</CS:in-reply-to>
  ${decision}
  <CS:hosturl><href>${escapeXml(collectionHref)}</href></CS:hosturl>
</CS:share-reply>`;
  const res = await fetch(collectionHref, {
    method: "POST",
    headers: withAuth(creds, {
      "Content-Type": "application/xml; charset=utf-8",
    }),
    body,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Share reply failed: ${res.status} ${res.statusText} ${text.slice(0, 200)}`);
  }
}

async function deleteNotification(creds: Credentials, notificationHref: string): Promise<void> {
  const res = await fetch(notificationHref, {
    method: "DELETE",
    headers: withAuth(creds),
  });
  // 404 is fine - notification was already cleared by another tab/client.
  if (!res.ok && res.status !== 404) {
    throw new Error(`DELETE notification failed: ${res.status} ${res.statusText}`);
  }
}

/**
 * Accept a sharing invitation.
 *
 * Sends CS:share-reply to the shared calendar's owner, then deletes the
 * local notification subcollection so it stops appearing in the inbox.
 * The server-side handler updates the share's status to "accepted" and
 * notifies the calendar owner via their own notification collection.
 */
export async function acceptShare(creds: Credentials, n: ShareNotification): Promise<void> {
  if (n.kind !== "invite-notification" || !n.sharedCollectionPath) {
    throw new Error("Notification is not an actionable invitation");
  }
  await postShareReply(creds, sharedCalendarHref(n.sharedCollectionPath), n.uid, true);
  await deleteNotification(creds, n.href);
}

/** Decline a sharing invitation - mirror of acceptShare. */
export async function declineShare(creds: Credentials, n: ShareNotification): Promise<void> {
  if (n.kind !== "invite-notification" || !n.sharedCollectionPath) {
    throw new Error("Notification is not an actionable invitation");
  }
  await postShareReply(creds, sharedCalendarHref(n.sharedCollectionPath), n.uid, false);
  await deleteNotification(creds, n.href);
}

/**
 * Dismiss a non-actionable notification (replies, revocations).
 * Just deletes the subcollection - no server-side state to update.
 */
export async function dismissNotification(creds: Credentials, n: ShareNotification): Promise<void> {
  await deleteNotification(creds, n.href);
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

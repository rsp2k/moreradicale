# moreradicale

A CalDAV and CardDAV server — substantially extended fork of [Radicale](https://radicale.org) 3.5.10.

Upstream Radicale gives you a small, file-backed CalDAV/CardDAV server that "just works." `moreradicale` keeps that, then layers in the standards-track features and operational tooling that bigger deployments tend to need: scheduling, sharing, web push, attachments, real-time sync, multi-tenancy, and Prometheus.

## What you get over upstream

### Standards

| RFC / Spec | Feature |
|---|---|
| RFC 3253 | WebDAV versioning (DeltaV), git-backed |
| RFC 4331 | Quota reporting |
| RFC 6638 | CalDAV scheduling (REQUEST, REPLY, CANCEL, COUNTER) |
| RFC 6638 §9.2 | Schedule inbox/outbox, free/busy, auto-accept for resources |
| RFC 7808 / 7809 | Timezone Distribution Service (TZDIST) |
| RFC 7953 | VAVAILABILITY in free/busy responses |
| RFC 7986 | Extended VCALENDAR properties (NAME, COLOR, IMAGE, etc.) |
| RFC 8030 | Web Push notifications with VAPID |
| RFC 8607 | Managed attachments |
| draft-ietf-calext-vpoll | VPOLL consensus scheduling |
| CalendarServer Sharing extension | Calendar sharing, delegation, invite notifications |

### Operations

- **Multi-tenant isolation** — domain / header / path-prefix / subdomain extraction; logical or filesystem-scoped storage; per-tenant config overrides
- **WebSocket real-time sync** at `/.websync` — live push to connected clients on every storage change, with polling fallback
- **Prometheus metrics** at `/.metrics` — request counts, latency histograms, push subscription gauges
- **CardDAV LDAP gateway** — exposes an LDAP directory as a virtual address book
- **External calendar subscriptions** — background refresh of remote ICS feeds (Apple's `CS:source` style)
- **iTIP webhook + IMAP polling** — accept scheduling messages from external attendees over email
- **ASGI runtime** — runs under `uvicorn` for HTTP + native WebSocket; standard WSGI deployment still supported

### Web UI

A modern admin/management UI under `/.web/` (replaces the upstream's vanilla-JS interface):

- **Astro 5 + React + Tailwind v4 + shadcn-style components**
- Real-time updates via the same `/.websync` WebSocket the API uses
- Share-acceptance inbox with accept/decline/dismiss actions
- "Shared by X" badges on borrowed calendars, hidden owner-only actions
- Proxy-auth aware (Authentik / Caddy `X-Remote-User` flows) with Basic Auth fallback

## Quick start

```bash
# Run from source with uv
git clone https://git.supported.systems/mcfeely/moreradicale.git
cd moreradicale
uv run python -m moreradicale --config /etc/moreradicale/config

# Or build the Docker image (includes the web UI pre-built)
docker compose build && docker compose up -d
```

The default `[auth] type` is `denyall` since v3.5.0 — you must configure an auth backend (`htpasswd`, `ldap`, `oauth2`, `pam`, `http_remote_user`, etc.) before the server will accept anything.

See `CLAUDE.md` for the architecture rundown and `contrib/` for Caddy / nginx / Apache reverse-proxy examples.

## Project status

Built as a working fork — actively developed, deployed, and used in anger by the maintainer. Versions follow upstream Radicale numbering (`3.5.x`) despite the substantial divergence; see `CHANGELOG.md` for what each version added.

- **Source**: https://git.supported.systems/mcfeely/moreradicale
- **Tracking branch**: upstream Radicale `master` was last merged from 3.5.10. New upstream changes are cherry-picked as needed.

## Compatibility

- **Python**: 3.9+ (tested through 3.14; CI runs against CPython and PyPy)
- **Platforms**: Linux, *BSD, macOS, Windows
- **Storage**: file system (`multifilesystem` default; `multifilesystem_nolock` for single-process deploys)
- **Auth backends**: htpasswd, LDAP, IMAP, OAuth2, PAM, Dovecot, HTTP header (Authentik / Caddy forward-auth), remote-user, none, denyall

## License

GPLv3, inherited from upstream Radicale. See `COPYING.md`.

Upstream copyright belongs to Guillaume Ayoub, Unrud, Peter Bieringer, and the Kozea organization; fork additions copyright Ryan Malloy and contributors.

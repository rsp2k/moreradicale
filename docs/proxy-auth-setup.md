# Reverse-proxy authentication (Authentik via Caddy)

By default the bundled Docker deployment uses htpasswd: users sign in via
the moreradicale web UI with a username and password. This document
covers the alternative pattern: delegating authentication to a reverse
proxy (Authentik, oauth2-proxy, Tailscale Funnel + identity-aware proxy,
etc.) which forwards an authenticated identity header to moreradicale.

The web UI **auto-detects** which mode is in use. No client-side
configuration is needed; once the server-side switch is flipped, the
login screen disappears and users land directly on their collections.

## What changes

- moreradicale's auth backend reads `X-Remote-User` (or `Remote-User`)
  from the incoming request instead of validating an htpasswd entry.
- Caddy enforces authentication via `forward_auth` against Authentik
  before any request reaches moreradicale.
- The browser receives a session cookie from Authentik; moreradicale
  trusts the proxy without ever seeing credentials.

## Server config (`config.docker`)

Replace the `[auth]` block:

```ini
[auth]
type = http_x_remote_user
# delay still applies if the proxy ever forwards an empty header
delay = 1
realm = moreradicale
```

If your proxy uses `Remote-User` instead of `X-Remote-User` (e.g. some
oauth2-proxy setups), use `http_remote_user` instead.

The `[rights]`, `[storage]`, `[web]`, and other sections remain
unchanged.

## Caddy labels (`docker-compose.yml`)

Add `forward_auth` directives to the `moreradicale` service's labels.
This example uses Authentik's outpost endpoint:

```yaml
services:
  moreradicale:
    # ... existing config ...
    labels:
      caddy: ${DOMAIN}
      caddy.reverse_proxy: "{{upstreams 5232}}"
      caddy.encode: gzip zstd

      # Forward auth to Authentik. Adjust the host:port to point at
      # your Authentik outpost (typically the authentik-server container
      # on the same docker network, or the public host if using DNS).
      caddy.forward_auth: authentik-server:9000
      caddy.forward_auth.uri: /outpost.goauthentik.io/auth/caddy
      caddy.forward_auth.copy_headers: >-
        X-Authentik-Username
        X-Authentik-Groups
        X-Authentik-Email
        X-Authentik-Name
        X-Authentik-Uid

      # Authentik sends the username as X-Authentik-Username; rename it
      # to X-Remote-User for moreradicale's http_x_remote_user backend.
      caddy.forward_auth.header_up.X-Remote-User: "{http.request.header.X-Authentik-Username}"

      # WebSocket-friendly proxy timeouts (existing)
      caddy.reverse_proxy.flush_interval: "-1"
      caddy.reverse_proxy.transport: "http"
      caddy.reverse_proxy.transport.read_timeout: "0"
      caddy.reverse_proxy.transport.write_timeout: "0"
      caddy.reverse_proxy.transport.keepalive: "5m"
      caddy.reverse_proxy.transport.keepalive_idle_conns: "10"
      caddy.reverse_proxy.stream_timeout: "24h"
      caddy.reverse_proxy.stream_close_delay: "5s"

    networks:
      - caddy
      - authentik  # whatever network exposes authentik-server
```

If Authentik is on a separate Docker network, attach the moreradicale
container to both. Adjust the `forward_auth` target accordingly.

## Authentik provider setup

In the Authentik admin UI:

1. **Create a Proxy Provider** (Applications → Providers → Create →
   Proxy Provider)
   - Mode: **Forward auth (single application)**
   - External host: `https://moreradicale.l.supported.systems/`
   - Token validity: whatever fits your session policy

2. **Create an Application** bound to the provider
   - Slug: `moreradicale`
   - Provider: the provider just created
   - Launch URL: same external host

3. **Create or reuse an Outpost** that includes the application
   (Applications → Outposts). Note the outpost's container name —
   that's what `caddy.forward_auth` points at.

4. **Bindings**: control who can sign in via group/user policies on
   the application.

## Testing

1. Apply the new `config.docker` and `docker-compose.yml`.
2. `make build && make up` (or `docker compose up -d --force-recreate`).
3. Visit `https://moreradicale.l.supported.systems/` in a browser
   that's not already signed into Authentik — you should be
   redirected to the Authentik login flow.
4. After signing in, you should land on the moreradicale UI **without
   seeing the login form**. The header shows "Signed in as <username>".

## How the UI auto-detect works

On page load, `App.tsx` calls `detectProxiedSession()` which sends an
unauthenticated `PROPFIND /` request:

- **200 with `<current-user-principal>`**: proxy auth is active.
  The username is extracted from the principal href; subsequent
  fetches send no `Authorization` header (the proxy handles it).
- **401**: proxy auth is not in effect. The UI falls back to the
  htpasswd Basic-Auth login form.

This means a single moreradicale build serves both deployment modes,
and switching between them only requires server-side changes.

## Logging out under proxy auth

The htpasswd flow's "Sign out" button clears the in-memory credentials.
Under proxy auth there are no credentials in moreradicale to clear —
the session lives in the proxy's cookie. The UI handles this by reloading
the page after logout, which re-runs the detect step. To actually end
the Authentik session, link users to Authentik's `/-/logout/` URL or
add a custom button that points there.

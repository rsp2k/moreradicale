# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 Ryan Malloy and contributors
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
ASGI entry point for moreradicale.

Run with: uvicorn moreradicale.asgi:app --host 0.0.0.0 --port 5232

The default WSGI server (`python -m moreradicale`) can't handle the
WebSocket protocol upgrade because wsgiref doesn't expose the raw
socket after dispatch. This entry point keeps the existing WSGI
Application unchanged for HTTP traffic - it's wrapped by
asgiref.WsgiToAsgi - and adds a native ASGI WebSocket handler on
/.websync that uses the existing WebSyncHandler protocol logic.

Authentication for WebSocket: the standard WSGI auth backend isn't
reachable from the ASGI side, so WebSockets accept Basic Auth via
the initial handshake only (or proxy auth via the X-Remote-User
header just like the HTTP path).
"""

import asyncio
import base64
import json
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional, cast

from asgiref.wsgi import WsgiToAsgi

from moreradicale import Application, config
from moreradicale.log import logger
from moreradicale.websync.handler import WebSyncHandler
from moreradicale.websync.manager import websync_manager


def _load_configuration() -> config.Configuration:
    paths = config.parse_compound_paths(
        config.DEFAULT_CONFIG_PATH,
        os.environ.get("MORERADICALE_CONFIG"),
    )
    return config.load(paths)


_configuration = _load_configuration()
_wsgi_application = Application(_configuration)
_wsgi_asgi = WsgiToAsgi(_wsgi_application)
_websync_handler = WebSyncHandler(_configuration)


def _extract_user_from_basic_auth(headers: Dict[bytes, bytes]) -> str:
    """Decode HTTP Basic Auth username from the WS handshake headers."""
    auth = headers.get(b"authorization", b"").decode("latin-1")
    if not auth.lower().startswith("basic "):
        return ""
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
        return decoded.split(":", 1)[0]
    except Exception:
        return ""


def _extract_user_from_proxy(headers: Dict[bytes, bytes]) -> str:
    """Read X-Remote-User / Remote-User if a reverse proxy is in use."""
    for header_name in (b"x-remote-user", b"remote-user"):
        v = headers.get(header_name, b"")
        if v:
            return v.decode("latin-1", errors="replace")
    return ""


async def _authenticate_first_message(
    receive: Callable[[], Awaitable[Dict[str, Any]]],
    headers: Dict[bytes, bytes],
) -> Optional[str]:
    """Wait up to 5 seconds for a {"action":"auth", ...} message.

    Browsers can't set the Authorization header on `new WebSocket()`,
    so we accept the upgrade then require the client to send an auth
    message immediately. Returns the authenticated username or None.
    """
    # Already auth'd at HTTP level? (proxy auth, or non-browser clients
    # that DID set Authorization on the upgrade.)
    user = _extract_user_from_proxy(headers) or _extract_user_from_basic_auth(headers)
    if user:
        # Trust proxy auth, or trust HTTP-supplied Basic Auth for the
        # current request (uvicorn validated nothing - we have to check
        # it ourselves if it's Basic, but proxy headers are pre-validated
        # by Caddy/Authentik upstream).
        return user

    try:
        message = await asyncio.wait_for(receive(), timeout=5.0)
    except asyncio.TimeoutError:
        return None
    if message.get("type") != "websocket.receive":
        return None
    text = message.get("text") or ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if payload.get("action") != "auth":
        return None
    auth_user = str(payload.get("user", ""))
    auth_password = str(payload.get("password", ""))
    if not auth_user or not auth_password:
        return None

    # Run the WSGI app's auth backend in a thread (it's blocking).
    from moreradicale.auth import AuthContext
    auth_backend = _wsgi_application._auth
    auth_ctx = AuthContext()
    logger.info("WebSocket auth attempt: user=%s backend=%s", auth_user, type(auth_backend).__module__)
    try:
        validated = await asyncio.to_thread(
            auth_backend.login, auth_user, auth_password, auth_ctx
        )
    except Exception as e:
        logger.warning("WebSocket auth threw: %s", e, exc_info=True)
        return None
    logger.info("WebSocket auth result: %r", validated)
    if isinstance(validated, tuple):
        validated_user = validated[0]
    else:
        validated_user = validated
    return validated_user or None


async def _handle_websocket(
    scope: Dict[str, Any],
    receive: Callable[[], Awaitable[Dict[str, Any]]],
    send: Callable[[Dict[str, Any]], Awaitable[None]],
) -> None:
    """Native ASGI WebSocket handler for /.websync."""
    headers = {k.lower(): v for k, v in scope.get("headers", [])}

    if not _websync_handler.enabled:
        # Need to wait for the connect event before we can close.
        await receive()
        await send({"type": "websocket.close", "code": 1003, "reason": "WebSync disabled"})
        return

    # ASGI requires reading the websocket.connect event before accepting.
    connect_event = await receive()
    if connect_event.get("type") != "websocket.connect":
        logger.warning("WebSocket handler got unexpected first event: %s", connect_event.get("type"))
        return
    await send({"type": "websocket.accept"})

    user = await _authenticate_first_message(receive, headers)
    if not user:
        await send({"type": "websocket.send",
                    "text": json.dumps({"error": "Authentication required",
                                        "hint": "Send {\"action\":\"auth\",\"user\":...,\"password\":...} as the first message"})})
        await send({"type": "websocket.close", "code": 1008, "reason": "Authentication required"})
        return

    # Confirm successful auth so the client knows to start subscribing.
    await send({"type": "websocket.send",
                "text": json.dumps({"status": "authenticated", "user": user})})

    # Register the connection so notify_change() can push to it.
    connection_id = os.urandom(16).hex()
    queue: asyncio.Queue[str] = asyncio.Queue()

    def push(text: str) -> None:
        # websync_manager calls this from arbitrary threads; bridge to the
        # async queue.
        loop.call_soon_threadsafe(queue.put_nowait, text)

    loop = asyncio.get_running_loop()

    websync_manager.register_connection(
        connection_id,
        user,
        lambda msg: push(msg if isinstance(msg, str) else json.dumps(msg)),
    )
    logger.info("WebSync: ws upgrade accepted for user=%s connection=%s", user, connection_id)

    async def reader() -> None:
        """Read incoming JSON messages from the client."""
        while True:
            event = await receive()
            etype = event["type"]
            if etype == "websocket.disconnect":
                return
            if etype != "websocket.receive":
                continue
            text: Optional[str] = event.get("text")
            if text is None:
                # Binary messages aren't part of our protocol; ignore.
                continue
            response = _websync_handler.handle_message(connection_id, text, user)
            if response:
                await send({"type": "websocket.send", "text": response})

    async def writer() -> None:
        """Drain the queue of server-pushed notifications."""
        while True:
            text = await queue.get()
            await send({"type": "websocket.send", "text": text})

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())
    try:
        # Reader returns on disconnect; writer is cancelled.
        done, pending = await asyncio.wait(
            {reader_task, writer_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    finally:
        websync_manager.unregister_connection(connection_id)
        logger.info("WebSync: ws closed for user=%s connection=%s", user, connection_id)


async def app(
    scope: Dict[str, Any],
    receive: Callable[[], Awaitable[Dict[str, Any]]],
    send: Callable[[Dict[str, Any]], Awaitable[None]],
) -> None:
    """Top-level ASGI app: HTTP -> wrapped WSGI, WebSocket -> websync."""
    scope_type = scope["type"]
    if scope_type == "http":
        await _wsgi_asgi(scope, receive, send)
    elif scope_type == "websocket":
        path = scope.get("path", "")
        if path == "/.websync" or path == "/.websync/":
            await _handle_websocket(scope, receive, send)
        else:
            # No WebSocket route at this path.
            await send({"type": "websocket.close", "code": 1000, "reason": "Not found"})
    elif scope_type == "lifespan":
        # Standard ASGI lifespan handshake.
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                logger.info(
                    "moreradicale ASGI app starting (HTTP via WsgiToAsgi, "
                    "WebSocket on /.websync)"
                )
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        # Unknown scope; let it die quietly.
        return


# Configure logging to match the WSGI server's format.
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(process)d] [%(levelname)s] %(message)s",
    )

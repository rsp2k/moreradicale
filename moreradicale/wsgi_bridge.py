# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2026 Ryan Malloy and contributors
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Radicale.  If not, see <http://www.gnu.org/licenses/>.

"""
Minimal WSGI-to-ASGI bridge.

Replaces ``asgiref.wsgi.WsgiToAsgi`` for moreradicale's HTTP path.

Why not asgiref
---------------
asgiref's bridge supports WSGI apps that call *back* into the running
event loop: it wraps ``send`` in an ``AsyncToSync`` and drives the WSGI
app through ``sync_to_async(thread_sensitive=True)``. That machinery
parks a ``CurrentThreadExecutor`` in a contextvar-backed ``Local`` so
nested ``sync_to_async`` calls can find their parent frame.

That executor is single-use: ``run_until_future()`` registers a done
callback that sets ``_broken = True``, so once the frame it belongs to
finishes, the executor is permanently dead. On Python 3.14, requests
arriving on a reused HTTP/1.1 keep-alive connection could observe a
*previous* request's dead executor and fail with::

    RuntimeError: CurrentThreadExecutor already quit or is broken

which 500s the request. Reproduced locally on both asgiref 3.11.1 and
3.12.1 by issuing several PROPFINDs over one keep-alive connection; see
docs/agent-threads/asgi-executor-lifecycle/.

moreradicale's WSGI application never calls back into the event loop.
It is a plain blocking callable that does its own file locking and
returns a fully materialised body. So none of that machinery is needed,
and this bridge does the obvious thing instead: read the request, run
the app in a worker thread, send the response. No ``AsyncToSync``, no
``CurrentThreadExecutor``, no contextvar-parked state between requests.

Buffering
---------
The response body is collected in the worker thread and sent in one
piece. This costs nothing here because the application already
materialises the whole body before returning (``app/__init__.py`` builds
a single ``bytes`` and returns ``[answer]``); there are no generator or
``wsgi.file_wrapper`` responses in the codebase. Request bodies are
spooled to disk past 64 KiB so large uploads do not sit in RAM.
"""

import asyncio
import concurrent.futures
import os
import sys
from collections import defaultdict
from tempfile import SpooledTemporaryFile
from typing import (Any, Awaitable, Callable, Dict, Iterable, List, Optional,
                    Tuple)

from moreradicale.log import logger

# Requests run on a dedicated pool rather than the loop's default executor
# so that WSGI traffic cannot starve other ``asyncio.to_thread`` users
# (the WebSocket auth path in asgi.py is one).
#
# On concurrency: an earlier version of this comment asserted the application
# "is written for concurrent multi-threaded execution". That was wrong, and it
# was the load-bearing assumption of replacing asgiref. asgiref's
# ``thread_sensitive=True`` serialised every WSGI call through one thread, and
# that serialisation was accidentally holding together three pieces of
# per-request state that were being written onto process-wide singletons:
# LDAP group memberships, the tenant context, and the calendar filter's
# floating timezone. Under a real pool those leak across requests - the first
# two are authorization decisions, the third silently corrupts query results.
#
# They now live in ContextVars (see moreradicale/tenant/__init__.py,
# auth/__init__.py, rights/__init__.py, item/filter.py) and are reset per
# request, with regression coverage in tests/test_request_isolation.py.
# The same exposure existed under the built-in ParallelHTTPServer and the
# documented gunicorn/uwsgi thread deployments, so this was a latent defect
# that the bridge surfaced rather than introduced.
#
# If a future change reintroduces shared per-request state, set
# MORERADICALE_WSGI_THREADS=1 to restore serialised execution while it is
# fixed. Do not set it to 0.
_DEFAULT_THREADS = min(32, (os.cpu_count() or 1) + 4)


def _spool_max_size() -> int:
    return 65536


class WSGIBridge:
    """ASGI application wrapping a blocking WSGI application."""

    def __init__(self, wsgi_application: Callable,
                 max_threads: Optional[int] = None) -> None:
        self.wsgi_application = wsgi_application
        if max_threads is None:
            env = os.environ.get("MORERADICALE_WSGI_THREADS")
            max_threads = int(env) if env and env.isdigit() else _DEFAULT_THREADS
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_threads, thread_name_prefix="moreradicale-wsgi")
        logger.info("WSGI bridge: %d worker threads", max_threads)

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Awaitable[Dict[str, Any]]],
        send: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            raise ValueError("WSGI bridge received a non-HTTP scope")

        with SpooledTemporaryFile(max_size=_spool_max_size()) as body:
            # Drain the request body. A client that disappears mid-upload
            # sends http.disconnect instead of another http.request; there
            # is nothing to respond to in that case, so just return.
            while True:
                message = await receive()
                mtype = message["type"]
                if mtype == "http.disconnect":
                    return
                if mtype != "http.request":
                    raise ValueError(
                        "WSGI bridge received unexpected message %r" % mtype)
                body.write(message.get("body", b""))
                if not message.get("more_body"):
                    break
            body.seek(0)

            environ = self._build_environ(scope, body)
            loop = asyncio.get_running_loop()
            try:
                status, headers, chunks = await loop.run_in_executor(
                    self._executor, self._run_app, environ)
            except Exception:
                logger.error("Unhandled error in WSGI application",
                             exc_info=True)
                await self._send_error(send)
                return

        await send({"type": "http.response.start",
                    "status": status,
                    "headers": headers})
        await send({"type": "http.response.body",
                    "body": b"".join(chunks)})

    # -- worker-thread side -------------------------------------------------

    def _run_app(self, environ: Dict[str, Any]
                 ) -> Tuple[int, List[Tuple[bytes, bytes]], List[bytes]]:
        """Run the WSGI app to completion. Executed off the event loop.

        Everything here is plain blocking code - no interaction with the
        event loop at all, which is the whole point of this bridge.
        """
        state: Dict[str, Any] = {"status": None, "headers": None}

        def start_response(status: str, response_headers: List[Tuple[str, str]],
                           exc_info: Any = None) -> Callable[[bytes], None]:
            # PEP 3333: with exc_info the app may replace an unsent response;
            # since nothing is sent until the app returns, we can always
            # accept it. Without exc_info a second call is a programming
            # error.
            if state["status"] is not None and exc_info is None:
                raise AssertionError(
                    "start_response() called twice without exc_info")
            code = int(status.split(" ", 1)[0])
            # latin-1 rather than ascii: it cannot raise, and it matches the
            # encoding used when decoding request headers.
            state["status"] = code
            state["headers"] = [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in response_headers]

            def write(_data: bytes) -> None:
                # The legacy write() callable is not used anywhere in
                # moreradicale; failing loudly beats silently dropping body
                # bytes if that ever changes.
                raise NotImplementedError(
                    "WSGI write() callable is not supported by this bridge")
            return write

        chunks: List[bytes] = []
        result: Iterable[bytes] = self.wsgi_application(environ, start_response)
        try:
            for chunk in result:
                if chunk:
                    chunks.append(chunk)
        finally:
            # PEP 3333: close() must be called if present, even on error.
            close = getattr(result, "close", None)
            if close is not None:
                close()

        if state["status"] is None:
            raise AssertionError("WSGI application never called start_response()")
        return state["status"], state["headers"], chunks

    # -- helpers ------------------------------------------------------------

    @staticmethod
    async def _send_error(send: Callable[[Dict[str, Any]], Awaitable[None]]
                          ) -> None:
        body = b"Internal Server Error"
        await send({"type": "http.response.start", "status": 500,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length",
                                 str(len(body)).encode("latin-1"))]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _build_environ(scope: Dict[str, Any], body: Any) -> Dict[str, Any]:
        """Build a PEP 3333 environ from an ASGI HTTP scope."""
        script_name = scope.get("root_path", "").encode("utf8").decode("latin1")
        path_info = scope["path"].encode("utf8").decode("latin1")
        if script_name and path_info.startswith(script_name):
            path_info = path_info[len(script_name):]
        environ: Dict[str, Any] = {
            "REQUEST_METHOD": scope["method"],
            "SCRIPT_NAME": script_name,
            "PATH_INFO": path_info,
            "QUERY_STRING": scope["query_string"].decode("ascii"),
            "SERVER_PROTOCOL": "HTTP/%s" % scope["http_version"],
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": scope.get("scheme", "http"),
            "wsgi.input": body,
            # moreradicale routes its logging through wsgi.errors
            # (Application.__call__ does log.register_stream on it), so this
            # must stay a real stream. Matches asgiref's choice.
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": True,
            "wsgi.multiprocess": True,
            "wsgi.run_once": False,
        }
        if scope.get("server"):
            environ["SERVER_NAME"] = scope["server"][0]
            environ["SERVER_PORT"] = str(scope["server"][1])
        else:
            environ["SERVER_NAME"] = "localhost"
            environ["SERVER_PORT"] = "80"
        if scope.get("client"):
            environ["REMOTE_ADDR"] = scope["client"][0]

        collected: Dict[str, List[str]] = defaultdict(list)
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin1")
            if name == "content-length":
                key = "CONTENT_LENGTH"
            elif name == "content-type":
                key = "CONTENT_TYPE"
            else:
                key = "HTTP_%s" % name.upper().replace("-", "_")
            collected[key].append(raw_value.decode("latin1"))
        for key, values in collected.items():
            environ[key] = ",".join(values)
        return environ

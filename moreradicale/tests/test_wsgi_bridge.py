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
Tests for the WSGI-to-ASGI bridge.

The bridge exists because asgiref's WsgiToAsgi 500s requests that arrive
on a reused keep-alive connection under Python 3.14 with
"CurrentThreadExecutor already quit or is broken". The test that matters
most here is test_repeated_calls_on_one_instance - a single bridge
instance serving many sequential requests, which is what a keep-alive
connection produces.

See docs/agent-threads/asgi-executor-lifecycle/ for the field report.
"""

import asyncio
from typing import Any, Dict, List

from moreradicale.wsgi_bridge import WSGIBridge


def _scope(method: str = "GET", path: str = "/", headers=None,
           query: bytes = b"") -> Dict[str, Any]:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "root_path": "",
        "query_string": query,
        "scheme": "http",
        "headers": headers or [],
        "server": ("testserver", 5232),
        "client": ("198.51.100.7", 54321),
    }


def _drive(bridge: WSGIBridge, scope: Dict[str, Any], body: bytes = b"",
           disconnect_instead: bool = False):
    """Run one request through the bridge, returning the sent messages."""
    sent: List[Dict[str, Any]] = []
    incoming = [{"type": "http.disconnect"}] if disconnect_instead else [
        {"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> Dict[str, Any]:
        return incoming.pop(0)

    async def send(message: Dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(bridge(scope, receive, send))
    return sent


def _simple_app(body: bytes = b"hello", status: str = "200 OK",
                headers=None):
    """A minimal conforming WSGI app that records the environ it saw."""
    seen: Dict[str, Any] = {}

    def app(environ, start_response):
        seen.clear()
        seen.update(environ)
        # wsgi.input is only valid for the duration of the call (the bridge
        # scopes the spooled file to the request), so read it here rather
        # than letting the test touch it after the fact.
        seen["__body__"] = environ["wsgi.input"].read()
        start_response(status, headers or [("Content-Type", "text/plain")])
        return [body]
    app.seen = seen  # type: ignore[attr-defined]
    return app


class TestWSGIBridge:
    """Bridge behaviour, independent of the moreradicale application."""

    def test_basic_round_trip(self):
        bridge = WSGIBridge(_simple_app(b"hello"), max_threads=2)
        sent = _drive(bridge, _scope())
        assert sent[0]["type"] == "http.response.start"
        assert sent[0]["status"] == 200
        assert (b"content-type", b"text/plain") in sent[0]["headers"]
        assert sent[1]["body"] == b"hello"

    def test_repeated_calls_on_one_instance(self):
        """Regression: the keep-alive shape that broke asgiref.

        asgiref parked a single-use CurrentThreadExecutor in a
        contextvar; a later request could find it already broken and
        500. The bridge holds no per-request state between calls, so
        many sequential requests through one instance must all succeed.
        """
        bridge = WSGIBridge(_simple_app(b"ok"), max_threads=2)
        for i in range(50):
            sent = _drive(bridge, _scope(path="/%d/" % i))
            assert sent[0]["status"] == 200, "request %d failed" % i
            assert sent[1]["body"] == b"ok"

    def test_alternating_paths_on_one_instance(self):
        """The exact interleaving that reproduced the asgiref failure."""
        bridge = WSGIBridge(_simple_app(b"ok"), max_threads=2)
        for path in ["/", "/user/", "/user/cal/", "/", "/user/cal/"] * 10:
            sent = _drive(bridge, _scope(path=path))
            assert sent[0]["status"] == 200, "path %s failed" % path

    def test_request_body_reaches_the_app(self):
        app = _simple_app()
        bridge = WSGIBridge(app, max_threads=2)
        payload = b"<propfind/>"
        _drive(bridge, _scope("PROPFIND", "/x/", headers=[
            (b"content-length", str(len(payload)).encode()),
            (b"content-type", b"application/xml")]), body=payload)
        assert app.seen["REQUEST_METHOD"] == "PROPFIND"
        assert app.seen["CONTENT_TYPE"] == "application/xml"
        assert app.seen["CONTENT_LENGTH"] == str(len(payload))
        assert app.seen["__body__"] == payload

    def test_headers_and_environ_mapping(self):
        app = _simple_app()
        bridge = WSGIBridge(app, max_threads=2)
        _drive(bridge, _scope("GET", "/p/", headers=[
            (b"depth", b"1"), (b"x-remote-user", b"alice")],
            query=b"a=1&b=2"))
        assert app.seen["HTTP_DEPTH"] == "1"
        assert app.seen["HTTP_X_REMOTE_USER"] == "alice"
        assert app.seen["QUERY_STRING"] == "a=1&b=2"
        assert app.seen["PATH_INFO"] == "/p/"
        assert app.seen["SERVER_NAME"] == "testserver"
        assert app.seen["SERVER_PORT"] == "5232"
        assert app.seen["REMOTE_ADDR"] == "198.51.100.7"
        assert app.seen["wsgi.url_scheme"] == "http"

    def test_duplicate_headers_are_joined(self):
        app = _simple_app()
        bridge = WSGIBridge(app, max_threads=2)
        _drive(bridge, _scope(headers=[(b"x-tag", b"a"), (b"x-tag", b"b")]))
        assert app.seen["HTTP_X_TAG"] == "a,b"

    def test_script_name_is_stripped_from_path_info(self):
        app = _simple_app()
        bridge = WSGIBridge(app, max_threads=2)
        scope = _scope(path="/prefix/cal/")
        scope["root_path"] = "/prefix"
        _drive(bridge, scope)
        assert app.seen["SCRIPT_NAME"] == "/prefix"
        assert app.seen["PATH_INFO"] == "/cal/"

    def test_client_disconnect_before_body_sends_nothing(self):
        bridge = WSGIBridge(_simple_app(), max_threads=2)
        sent = _drive(bridge, _scope("PUT", "/x/"), disconnect_instead=True)
        assert sent == []

    def test_application_exception_becomes_500(self):
        def boom(environ, start_response):
            raise RuntimeError("kaboom")
        bridge = WSGIBridge(boom, max_threads=2)
        sent = _drive(bridge, _scope())
        assert sent[0]["status"] == 500
        assert b"Internal Server Error" in sent[1]["body"]

    def test_missing_start_response_becomes_500(self):
        def silent(environ, start_response):
            return [b"body without start_response"]
        bridge = WSGIBridge(silent, max_threads=2)
        sent = _drive(bridge, _scope())
        assert sent[0]["status"] == 500

    def test_iterable_close_is_called(self):
        closed = []

        class Result:
            def __iter__(self):
                return iter([b"x"])

            def close(self):
                closed.append(True)

        def app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return Result()

        bridge = WSGIBridge(app, max_threads=2)
        _drive(bridge, _scope())
        assert closed == [True], "PEP 3333 requires close() on the iterable"

    def test_empty_body_response(self):
        def app(environ, start_response):
            start_response("204 No Content", [])
            return []
        bridge = WSGIBridge(app, max_threads=2)
        sent = _drive(bridge, _scope())
        assert sent[0]["status"] == 204
        assert sent[1]["body"] == b""

    def test_non_http_scope_rejected(self):
        bridge = WSGIBridge(_simple_app(), max_threads=2)
        scope = _scope()
        scope["type"] = "websocket"

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            pass

        try:
            asyncio.run(bridge(scope, receive, send))
        except ValueError:
            return
        raise AssertionError("expected ValueError for non-HTTP scope")


class TestWSGIBridgeConcurrency:
    """Many requests in flight at once through one bridge instance."""

    def test_concurrent_requests(self):
        bridge = WSGIBridge(_simple_app(b"ok"), max_threads=8)

        async def one(i: int) -> int:
            sent: List[Dict[str, Any]] = []
            incoming = [{"type": "http.request", "body": b"", "more_body": False}]

            async def receive():
                return incoming.pop(0)

            async def send(message):
                sent.append(message)

            await bridge(_scope(path="/c%d/" % i), receive, send)
            return sent[0]["status"]

        async def main():
            return await asyncio.gather(*(one(i) for i in range(64)))

        assert all(s == 200 for s in asyncio.run(main()))

"""
RFC 7808 Time Zone Data Distribution Service request handler.

Routes requests to /.well-known/timezone and returns appropriate responses.
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from http import client
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

from moreradicale.log import logger

from . import SUPPORTED_FORMATS, TZDIST_VERSION, WELL_KNOWN_PATH
from .formatter import transitions_to_vtimezone
from .provider import BaseTimezoneProvider, get_provider


class TZDistHandler:
    """
    RFC 7808 Timezone Distribution Service handler.

    Handles requests to /.well-known/timezone with various actions:
    - capabilities: Server capabilities and supported features
    - list: List all available timezones
    - get: Get specific timezone data in iCalendar format
    - find: Search for timezones by pattern
    """

    def __init__(self, configuration):
        self._configuration = configuration
        self._provider: Optional[BaseTimezoneProvider] = None
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_ttl = configuration.get("tzdist", "cache_ttl")
        self._truncate_years = configuration.get("tzdist", "truncate_years_before")
        self._expand_years = configuration.get("tzdist", "expand_years")

    def _get_provider(self) -> BaseTimezoneProvider:
        """Lazy-load the timezone provider."""
        if self._provider is None:
            provider_type = self._configuration.get("tzdist", "provider")
            self._provider = get_provider(provider_type)
        return self._provider

    def _get_cached(self, key: str) -> Optional[Any]:
        """Get item from cache if not expired."""
        if key in self._cache:
            timestamp, value = self._cache[key]
            if time.time() - timestamp < self._cache_ttl:
                return value
            del self._cache[key]
        return None

    def _set_cached(self, key: str, value: Any) -> None:
        """Store item in cache."""
        self._cache[key] = (time.time(), value)

    def handle_request(
        self, environ: dict, base_prefix: str, path: str
    ) -> Tuple[int, Dict[str, str], Optional[str], None]:
        """
        Handle a TZDIST request.

        Args:
            environ: WSGI environment
            base_prefix: Base URL prefix
            path: Request path

        Returns:
            Tuple of (status_code, headers, body, xml_request)
        """
        method = environ.get("REQUEST_METHOD", "GET")
        if method != "GET":
            return (
                client.METHOD_NOT_ALLOWED,
                {"Allow": "GET", "Content-Type": "text/plain"},
                "Only GET method is supported",
                None
            )

        # Parse query string - check both QUERY_STRING and path
        query_string = environ.get("QUERY_STRING", "")
        if not query_string and "?" in path:
            # Extract query string from path if not in environ
            query_string = path.split("?", 1)[1]
        params = parse_qs(query_string)

        # Get action (default to capabilities)
        action = params.get("action", ["capabilities"])[0]

        logger.debug("TZDIST request: action=%s, params=%s", action, params)

        try:
            if action == "capabilities":
                return self._handle_capabilities(base_prefix)
            elif action == "list":
                return self._handle_list(params)
            elif action == "get":
                tzid = params.get("tzid", [None])[0]
                if not tzid:
                    return self._error_response(
                        client.BAD_REQUEST,
                        "missing-tzid",
                        "The 'tzid' parameter is required for action=get"
                    )
                return self._handle_get(tzid, params)
            elif action == "find":
                pattern = params.get("pattern", ["*"])[0]
                return self._handle_find(pattern, params)
            else:
                return self._error_response(
                    client.BAD_REQUEST,
                    "invalid-action",
                    f"Unknown action: {action}"
                )
        except Exception as e:
            logger.error("TZDIST error: %s", e, exc_info=True)
            return self._error_response(
                client.INTERNAL_SERVER_ERROR,
                "server-error",
                str(e)
            )

    def _handle_capabilities(
        self, base_prefix: str
    ) -> Tuple[int, Dict[str, str], str, None]:
        """
        Return server capabilities (RFC 7808 Section 5.1).

        Returns JSON describing supported features, actions, and formats.
        """
        provider = self._get_provider()
        tz_count = len(provider.list_timezones())

        capabilities = {
            "version": TZDIST_VERSION,
            "info": {
                "primary-source": True,
                "contacts": [],
                "formats": SUPPORTED_FORMATS,
            },
            "actions": [
                {
                    "name": "capabilities",
                    "description": "Get server capabilities",
                    "uri-template": f"{base_prefix}{WELL_KNOWN_PATH}?action=capabilities"
                },
                {
                    "name": "list",
                    "description": "List available timezones",
                    "uri-template": f"{base_prefix}{WELL_KNOWN_PATH}?action=list{{&changedsince}}",
                    "parameters": [
                        {
                            "name": "changedsince",
                            "required": False,
                            "description": "Return only timezones changed since this sync token"
                        }
                    ]
                },
                {
                    "name": "get",
                    "description": "Get timezone data",
                    "uri-template": f"{base_prefix}{WELL_KNOWN_PATH}?action=get&tzid={{tzid}}{{&start,end}}",
                    "parameters": [
                        {
                            "name": "tzid",
                            "required": True,
                            "description": "Timezone identifier (e.g., America/New_York)"
                        },
                        {
                            "name": "start",
                            "required": False,
                            "description": "Truncate data before this date (YYYY-MM-DD)"
                        },
                        {
                            "name": "end",
                            "required": False,
                            "description": "Truncate data after this date (YYYY-MM-DD)"
                        }
                    ]
                },
                {
                    "name": "find",
                    "description": "Search for timezones",
                    "uri-template": f"{base_prefix}{WELL_KNOWN_PATH}?action=find&pattern={{pattern}}",
                    "parameters": [
                        {
                            "name": "pattern",
                            "required": True,
                            "description": "Glob pattern to match timezone IDs"
                        }
                    ]
                }
            ],
            "stats": {
                "timezone-count": tz_count
            }
        }

        body = json.dumps(capabilities, indent=2)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": f"max-age={self._cache_ttl}"
        }

        return (client.OK, headers, body, None)

    def _handle_list(
        self, params: Dict[str, List[str]]
    ) -> Tuple[int, Dict[str, str], str, None]:
        """
        List available timezones (RFC 7808 Section 5.2).

        Returns JSON with timezone identifiers and metadata.
        """
        cache_key = "list"
        cached = self._get_cached(cache_key)
        if cached:
            body, etag = cached
        else:
            provider = self._get_provider()
            timezones = provider.list_timezones()

            # Build response with timezone metadata
            tz_list = []
            for tzid in timezones:
                tz_list.append({
                    "tzid": tzid,
                    "last-modified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

            # Generate sync token from timezone list hash
            list_hash = hashlib.sha256(
                json.dumps(timezones).encode()
            ).hexdigest()[:16]

            response = {
                "synctoken": f"tzdist-{list_hash}",
                "timezones": tz_list
            }

            body = json.dumps(response, indent=2)
            etag = f'"{list_hash}"'
            self._set_cached(cache_key, (body, etag))

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": f"max-age={self._cache_ttl}",
            "ETag": etag
        }

        return (client.OK, headers, body, None)

    def _handle_get(
        self, tzid: str, params: Dict[str, List[str]]
    ) -> Tuple[int, Dict[str, str], str, None]:
        """
        Get specific timezone data (RFC 7808 Section 5.3).

        Returns iCalendar VTIMEZONE component.
        """
        provider = self._get_provider()
        tz = provider.get_timezone(tzid)

        if not tz:
            return self._error_response(
                client.NOT_FOUND,
                "invalid-tzid",
                f"Timezone not found: {tzid}"
            )

        # Parse date range parameters
        now = datetime.now()
        start_year = now.year - self._truncate_years if self._truncate_years > 0 else now.year - 5

        if "start" in params:
            try:
                start_date = datetime.strptime(params["start"][0], "%Y-%m-%d")
                start_year = start_date.year
            except ValueError:
                pass

        end_year = now.year + self._expand_years

        if "end" in params:
            try:
                end_date = datetime.strptime(params["end"][0], "%Y-%m-%d")
                end_year = end_date.year
            except ValueError:
                pass

        # Check cache
        cache_key = f"get:{tzid}:{start_year}:{end_year}"
        cached = self._get_cached(cache_key)
        if cached:
            body, etag = cached
        else:
            # Get transitions and format as VTIMEZONE
            transitions = provider.get_transitions(tzid, start_year, end_year)
            body = transitions_to_vtimezone(tzid, transitions, start_year, end_year)

            # Generate ETag from content
            etag = f'"{hashlib.sha256(body.encode()).hexdigest()[:16]}"'
            self._set_cached(cache_key, (body, etag))

        headers = {
            "Content-Type": "text/calendar; charset=utf-8",
            "Cache-Control": f"max-age={self._cache_ttl}",
            "ETag": etag
        }

        return (client.OK, headers, body, None)

    def _handle_find(
        self, pattern: str, params: Dict[str, List[str]]
    ) -> Tuple[int, Dict[str, str], str, None]:
        """
        Find timezones matching pattern (RFC 7808 Section 5.5).

        Supports glob-style patterns with * and ? wildcards.
        """
        provider = self._get_provider()
        matches = provider.find_timezones(pattern)

        response = {
            "pattern": pattern,
            "count": len(matches),
            "timezones": [{"tzid": tzid} for tzid in matches]
        }

        body = json.dumps(response, indent=2)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": f"max-age={self._cache_ttl}"
        }

        return (client.OK, headers, body, None)

    def _error_response(
        self, status: int, error_type: str, detail: str
    ) -> Tuple[int, Dict[str, str], str, None]:
        """
        Generate RFC 7807 problem details error response.
        """
        error = {
            "type": f"urn:ietf:params:tzdist:error:{error_type}",
            "title": client.responses.get(status, "Error"),
            "status": status,
            "detail": detail
        }

        body = json.dumps(error, indent=2)
        headers = {
            "Content-Type": "application/problem+json; charset=utf-8"
        }

        return (status, headers, body, None)

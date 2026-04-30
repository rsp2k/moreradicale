"""
ICS Subscription Sync Engine.

Handles fetching, parsing, and importing external iCalendar feeds.
Implements smart sync with HTTP caching (ETag, If-Modified-Since).
"""

import hashlib
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from moreradicale.log import logger


class SyncStatus(Enum):
    """Result status of a sync operation."""
    SUCCESS = "success"
    NOT_MODIFIED = "not_modified"
    ERROR = "error"
    INVALID_URL = "invalid_url"
    INVALID_DATA = "invalid_data"
    TIMEOUT = "timeout"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"


@dataclass
class SyncResult:
    """
    Result of a sync operation.

    Attributes:
        status: Sync status code
        message: Human-readable status message
        items_added: Number of new items added
        items_updated: Number of items updated
        items_deleted: Number of items removed
        etag: Server ETag for caching
        last_modified: Server Last-Modified header
        content_hash: Hash of fetched content for change detection
        sync_time: When sync completed
    """
    status: SyncStatus
    message: str = ""
    items_added: int = 0
    items_updated: int = 0
    items_deleted: int = 0
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    sync_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        if self.status == SyncStatus.SUCCESS:
            return (f"Synced: +{self.items_added} ~{self.items_updated} "
                    f"-{self.items_deleted}")
        return f"{self.status.value}: {self.message}"


class SyncEngine:
    """
    Fetches and processes external ICS calendar feeds.

    Features:
    - HTTP caching with ETag and If-Modified-Since
    - Content hash for detecting actual changes
    - Configurable timeout and retry
    - SSL/TLS support with configurable verification
    """

    # User-Agent for requests (some servers block default Python UA)
    USER_AGENT = "Radicale-ICS-Sync/1.0"

    # Maximum content size (10 MB)
    MAX_CONTENT_SIZE = 10 * 1024 * 1024

    def __init__(self, configuration):
        """
        Initialize the sync engine.

        Args:
            configuration: Radicale configuration
        """
        self._configuration = configuration
        self._timeout = configuration.get("subscriptions", "timeout")
        self._verify_ssl = configuration.get("subscriptions", "verify_ssl")
        self._max_size = configuration.get("subscriptions", "max_content_size")

    def fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None
    ) -> Tuple[SyncResult, Optional[str]]:
        """
        Fetch ICS data from external URL.

        Uses conditional GET with ETag/If-Modified-Since when available
        to avoid re-downloading unchanged content.

        Args:
            url: External ICS URL
            etag: Previous ETag for conditional GET
            last_modified: Previous Last-Modified for conditional GET

        Returns:
            Tuple of (SyncResult, ics_data or None)
        """
        if not self._validate_url(url):
            return SyncResult(
                status=SyncStatus.INVALID_URL,
                message=f"Invalid or disallowed URL: {url}"
            ), None

        try:
            # Build request with caching headers
            request = Request(url)
            request.add_header("User-Agent", self.USER_AGENT)
            request.add_header("Accept", "text/calendar, application/calendar+xml")

            if etag:
                request.add_header("If-None-Match", etag)
            if last_modified:
                request.add_header("If-Modified-Since", last_modified)

            # SSL context
            ssl_context = None
            if url.startswith("https://"):
                ssl_context = ssl.create_default_context()
                if not self._verify_ssl:
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE

            # Fetch with timeout
            with urlopen(request, timeout=self._timeout,
                        context=ssl_context) as response:
                # Check content length
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > self._max_size:
                    return SyncResult(
                        status=SyncStatus.ERROR,
                        message=f"Content too large: {content_length} bytes"
                    ), None

                # Read content with size limit
                data = response.read(self._max_size + 1)
                if len(data) > self._max_size:
                    return SyncResult(
                        status=SyncStatus.ERROR,
                        message="Content exceeds maximum size"
                    ), None

                # Decode content
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    ics_data = data.decode(charset)
                except UnicodeDecodeError:
                    ics_data = data.decode("utf-8", errors="replace")

                # Validate it's actually iCalendar data
                if not self._validate_ics(ics_data):
                    return SyncResult(
                        status=SyncStatus.INVALID_DATA,
                        message="Response is not valid iCalendar data"
                    ), None

                # Extract caching headers
                new_etag = response.headers.get("ETag")
                new_last_modified = response.headers.get("Last-Modified")

                # Calculate content hash
                content_hash = hashlib.sha256(data).hexdigest()[:16]

                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    etag=new_etag,
                    last_modified=new_last_modified,
                    content_hash=content_hash,
                ), ics_data

        except HTTPError as e:
            if e.code == 304:
                # Not Modified - content hasn't changed
                return SyncResult(
                    status=SyncStatus.NOT_MODIFIED,
                    message="Content not modified since last sync"
                ), None
            elif e.code == 401 or e.code == 403:
                return SyncResult(
                    status=SyncStatus.FORBIDDEN,
                    message=f"Access denied: HTTP {e.code}"
                ), None
            elif e.code == 404:
                return SyncResult(
                    status=SyncStatus.NOT_FOUND,
                    message="Calendar feed not found"
                ), None
            else:
                return SyncResult(
                    status=SyncStatus.ERROR,
                    message=f"HTTP error: {e.code} {e.reason}"
                ), None

        except URLError as e:
            if "timed out" in str(e.reason).lower():
                return SyncResult(
                    status=SyncStatus.TIMEOUT,
                    message=f"Connection timed out: {url}"
                ), None
            return SyncResult(
                status=SyncStatus.ERROR,
                message=f"Network error: {e.reason}"
            ), None

        except Exception as e:
            logger.warning("Unexpected error fetching %s: %s", url, e)
            return SyncResult(
                status=SyncStatus.ERROR,
                message=f"Unexpected error: {e}"
            ), None

    def parse_events(self, ics_data: str) -> List[Dict]:
        """
        Parse events from ICS data.

        Extracts VEVENT, VTODO, VJOURNAL components with their UIDs.

        Args:
            ics_data: iCalendar data string

        Returns:
            List of dicts with 'uid', 'type', 'data' keys
        """
        events = []

        # Find all components
        for comp_type in ("VEVENT", "VTODO", "VJOURNAL"):
            pattern = rf"BEGIN:{comp_type}\r?\n(.*?)END:{comp_type}"
            for match in re.finditer(pattern, ics_data, re.DOTALL):
                component_data = match.group(0)

                # Extract UID
                uid_match = re.search(r"^UID:(.+?)(?:\r?\n)", component_data,
                                     re.MULTILINE)
                if uid_match:
                    uid = uid_match.group(1).strip()

                    # Wrap in VCALENDAR for storage
                    wrapped = self._wrap_component(component_data, ics_data)

                    events.append({
                        "uid": uid,
                        "type": comp_type,
                        "data": wrapped,
                    })

        return events

    def _wrap_component(self, component: str, original_ics: str) -> str:
        """Wrap a component in a VCALENDAR with proper headers."""
        # Extract VERSION and PRODID from original
        version = "2.0"
        prodid = "-//Radicale//Subscription//EN"

        version_match = re.search(r"^VERSION:(.+?)$", original_ics, re.MULTILINE)
        if version_match:
            version = version_match.group(1).strip()

        prodid_match = re.search(r"^PRODID:(.+?)$", original_ics, re.MULTILINE)
        if prodid_match:
            prodid = prodid_match.group(1).strip()

        # Extract VTIMEZONE components
        timezones = []
        for tz_match in re.finditer(
            r"BEGIN:VTIMEZONE\r?\n.*?END:VTIMEZONE",
            original_ics, re.DOTALL
        ):
            timezones.append(tz_match.group(0))

        # Build wrapped calendar
        lines = [
            "BEGIN:VCALENDAR",
            f"VERSION:{version}",
            f"PRODID:{prodid}",
        ]

        # Add referenced timezones
        for tz in timezones:
            # Check if this timezone is referenced in the component
            tz_id_match = re.search(r"^TZID:(.+?)$", tz, re.MULTILINE)
            if tz_id_match:
                tzid = tz_id_match.group(1).strip()
                if tzid in component:
                    lines.append(tz)

        lines.append(component)
        lines.append("END:VCALENDAR")

        return "\r\n".join(lines)

    def _validate_url(self, url: str) -> bool:
        """Validate URL is allowed for subscription."""
        if not url:
            return False

        # Must be http or https
        if not url.startswith(("http://", "https://")):
            return False

        # Block localhost and private IPs (security)
        blocked_patterns = [
            r"^https?://localhost",
            r"^https?://127\.",
            r"^https?://10\.",
            r"^https?://172\.(1[6-9]|2[0-9]|3[0-1])\.",
            r"^https?://192\.168\.",
            r"^https?://\[::1\]",
            r"^https?://\[fe80:",
        ]

        # Check if private network blocking is enabled
        if self._configuration.get("subscriptions", "block_private_networks"):
            for pattern in blocked_patterns:
                if re.match(pattern, url, re.IGNORECASE):
                    logger.warning("Blocked private network URL: %s", url)
                    return False

        return True

    def _validate_ics(self, data: str) -> bool:
        """Basic validation that data is iCalendar format."""
        return (
            "BEGIN:VCALENDAR" in data and
            "END:VCALENDAR" in data and
            "VERSION:" in data
        )

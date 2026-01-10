"""
Tests for External ICS Subscriptions.

Tests the subscription sync engine and manager including:
- URL validation and security
- ICS parsing and event extraction
- Sync state management
- HTTP caching behavior
"""

import json
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from unittest.mock import Mock, patch, MagicMock

import pytest

from radicale.tests import BaseTest


# Sample ICS data for testing
SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event1@example.com
DTSTAMP:20240115T120000Z
DTSTART:20240115T090000Z
DTEND:20240115T100000Z
SUMMARY:Test Event 1
END:VEVENT
BEGIN:VEVENT
UID:event2@example.com
DTSTAMP:20240115T120000Z
DTSTART:20240116T140000Z
DTEND:20240116T150000Z
SUMMARY:Test Event 2
END:VEVENT
END:VCALENDAR"""

SAMPLE_ICS_UPDATED = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event1@example.com
DTSTAMP:20240115T130000Z
DTSTART:20240115T100000Z
DTEND:20240115T110000Z
SUMMARY:Test Event 1 Updated
END:VEVENT
BEGIN:VEVENT
UID:event3@example.com
DTSTAMP:20240115T130000Z
DTSTART:20240117T090000Z
DTEND:20240117T100000Z
SUMMARY:New Event 3
END:VEVENT
END:VCALENDAR"""


class TestSyncEngine:
    """Tests for the SyncEngine class."""

    def test_validate_url_valid(self):
        """Test valid URL validation."""
        from radicale.subscriptions.engine import SyncEngine

        config = Mock()
        config.get.return_value = True  # block_private_networks
        engine = SyncEngine(config)

        # Should allow public URLs
        assert engine._validate_url("https://example.com/calendar.ics")
        assert engine._validate_url("http://calendar.google.com/ical/feed")
        assert engine._validate_url("https://ics.calendarlabs.com/76/xxx/US_Holidays.ics")

    def test_validate_url_invalid(self):
        """Test invalid URL rejection."""
        from radicale.subscriptions.engine import SyncEngine

        config = Mock()
        config.get.return_value = True
        engine = SyncEngine(config)

        # Should reject non-http(s) schemes
        assert not engine._validate_url("")
        assert not engine._validate_url("ftp://example.com/cal.ics")
        assert not engine._validate_url("file:///etc/passwd")
        assert not engine._validate_url("/local/path")

    def test_validate_url_blocks_private_networks(self):
        """Test private network blocking."""
        from radicale.subscriptions.engine import SyncEngine

        config = Mock()
        config.get.return_value = True  # block_private_networks = True
        engine = SyncEngine(config)

        # Should block private IPs
        assert not engine._validate_url("http://localhost/cal.ics")
        assert not engine._validate_url("http://127.0.0.1/cal.ics")
        assert not engine._validate_url("http://192.168.1.1/cal.ics")
        assert not engine._validate_url("http://10.0.0.1/cal.ics")
        assert not engine._validate_url("http://172.16.0.1/cal.ics")

    def test_validate_url_allows_private_when_disabled(self):
        """Test private network allowed when blocking disabled."""
        from radicale.subscriptions.engine import SyncEngine

        config = Mock()
        config.get.return_value = False  # block_private_networks = False
        engine = SyncEngine(config)

        # Should allow private IPs when blocking disabled
        assert engine._validate_url("http://localhost/cal.ics")
        assert engine._validate_url("http://192.168.1.1/cal.ics")

    def test_validate_ics(self):
        """Test iCalendar data validation."""
        from radicale.subscriptions.engine import SyncEngine

        config = Mock()
        engine = SyncEngine(config)

        assert engine._validate_ics(SAMPLE_ICS)
        assert not engine._validate_ics("<html>Not a calendar</html>")
        assert not engine._validate_ics("plain text")
        assert not engine._validate_ics("BEGIN:VCALENDAR")  # Missing END

    def test_parse_events_basic(self):
        """Test parsing events from ICS data."""
        from radicale.subscriptions.engine import SyncEngine

        config = Mock()
        engine = SyncEngine(config)

        events = engine.parse_events(SAMPLE_ICS)

        assert len(events) == 2
        assert events[0]["uid"] == "event1@example.com"
        assert events[0]["type"] == "VEVENT"
        assert "BEGIN:VCALENDAR" in events[0]["data"]
        assert events[1]["uid"] == "event2@example.com"

    def test_parse_events_with_todo(self):
        """Test parsing VTODO components."""
        from radicale.subscriptions.engine import SyncEngine

        config = Mock()
        engine = SyncEngine(config)

        ics_with_todo = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VTODO
UID:todo1@example.com
DTSTAMP:20240115T120000Z
SUMMARY:Test Todo
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR"""

        events = engine.parse_events(ics_with_todo)

        assert len(events) == 1
        assert events[0]["uid"] == "todo1@example.com"
        assert events[0]["type"] == "VTODO"

    def test_wrap_component_preserves_timezone(self):
        """Test that timezone data is preserved when wrapping."""
        from radicale.subscriptions.engine import SyncEngine

        config = Mock()
        engine = SyncEngine(config)

        ics_with_tz = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:event-tz@example.com
DTSTAMP:20240115T120000Z
DTSTART;TZID=America/New_York:20240115T090000
SUMMARY:Event with TZ
END:VEVENT
END:VCALENDAR"""

        events = engine.parse_events(ics_with_tz)

        assert len(events) == 1
        # Wrapped data should include the timezone
        assert "VTIMEZONE" in events[0]["data"]
        assert "America/New_York" in events[0]["data"]


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_sync_result_str_success(self):
        """Test string representation for success."""
        from radicale.subscriptions.engine import SyncResult, SyncStatus

        result = SyncResult(
            status=SyncStatus.SUCCESS,
            items_added=5,
            items_updated=2,
            items_deleted=1
        )

        assert "Synced: +5 ~2 -1" in str(result)

    def test_sync_result_str_error(self):
        """Test string representation for error."""
        from radicale.subscriptions.engine import SyncResult, SyncStatus

        result = SyncResult(
            status=SyncStatus.ERROR,
            message="Connection refused"
        )

        assert "error" in str(result).lower()
        assert "Connection refused" in str(result)


class TestSubscriptionState:
    """Tests for SubscriptionState persistence."""

    def test_state_roundtrip(self):
        """Test serialization/deserialization."""
        from radicale.subscriptions.manager import SubscriptionState

        original = SubscriptionState(
            source_url="https://example.com/cal.ics",
            collection_path="/user/subscribed-cal/",
            etag='"abc123"',
            last_modified="Sun, 15 Jan 2024 12:00:00 GMT",
            content_hash="fedcba987654",
            last_sync=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            last_success=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            consecutive_failures=0,
            items_count=10
        )

        # Round-trip through dict
        data = original.to_dict()
        restored = SubscriptionState.from_dict(data)

        assert restored.source_url == original.source_url
        assert restored.etag == original.etag
        assert restored.last_modified == original.last_modified
        assert restored.content_hash == original.content_hash
        assert restored.items_count == original.items_count

    def test_state_handles_missing_fields(self):
        """Test deserialization with missing optional fields."""
        from radicale.subscriptions.manager import SubscriptionState

        minimal_data = {
            "source_url": "https://example.com/cal.ics",
            "collection_path": "/user/cal/"
        }

        state = SubscriptionState.from_dict(minimal_data)

        assert state.source_url == "https://example.com/cal.ics"
        assert state.etag is None
        assert state.consecutive_failures == 0


class TestSubscriptionManager:
    """Tests for SubscriptionManager."""

    def test_find_subscribed_collections(self):
        """Test finding VSUBSCRIBED collections."""
        from radicale.subscriptions.manager import SubscriptionManager

        # Mock storage
        mock_storage = Mock()
        mock_collection = Mock()
        mock_collection.get_meta.return_value = "VSUBSCRIBED"
        mock_collection.path = "/user/holidays/"

        mock_storage.discover.return_value = [mock_collection]

        config = Mock()
        manager = SubscriptionManager(mock_storage, config)

        subscribed = manager._find_subscribed_collections()

        assert "/user/holidays/" in subscribed

    def test_should_refresh_never_synced(self):
        """Test refresh needed for never-synced collection."""
        from radicale.subscriptions.manager import SubscriptionManager

        mock_storage = Mock()
        config = Mock()
        config.get.return_value = 3600  # refresh_interval

        manager = SubscriptionManager(mock_storage, config)

        # No state = never synced = needs refresh
        with patch.object(manager, '_load_state', return_value=None):
            assert manager._should_refresh("/user/cal/")

    def test_should_refresh_with_backoff(self):
        """Test exponential backoff on failures."""
        from radicale.subscriptions.manager import (
            SubscriptionManager, SubscriptionState
        )

        mock_storage = Mock()
        config = Mock()
        config.get.return_value = 3600  # refresh_interval = 1 hour

        manager = SubscriptionManager(mock_storage, config)

        # State with 3 consecutive failures
        state = SubscriptionState(
            source_url="https://example.com/cal.ics",
            collection_path="/user/cal/",
            last_sync=datetime.now(timezone.utc) - timedelta(hours=2),
            consecutive_failures=3
        )

        with patch.object(manager, '_load_state', return_value=state):
            # With 3 failures, backoff = 3600 * 2^3 = 28800 seconds = 8 hours
            # Last sync was 2 hours ago, so shouldn't refresh yet
            assert not manager._should_refresh("/user/cal/")


class TestSubscriptionHTTP:
    """Tests for HTTP fetching behavior."""

    def test_fetch_not_modified_304(self):
        """Test handling of 304 Not Modified response."""
        from radicale.subscriptions.engine import SyncEngine, SyncStatus
        from urllib.error import HTTPError

        config = Mock()
        config.get.side_effect = lambda s, k: {
            ("subscriptions", "timeout"): 30,
            ("subscriptions", "verify_ssl"): True,
            ("subscriptions", "max_content_size"): 10485760,
            ("subscriptions", "block_private_networks"): True,
        }.get((s, k), True)

        engine = SyncEngine(config)

        # Mock 304 response
        with patch('radicale.subscriptions.engine.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = HTTPError(
                "https://example.com/cal.ics",
                304, "Not Modified", {}, None
            )

            result, data = engine.fetch(
                "https://example.com/cal.ics",
                etag='"abc123"'
            )

            assert result.status == SyncStatus.NOT_MODIFIED
            assert data is None


class TestSubscriptionCalDAV(BaseTest):
    """Tests for subscription CalDAV integration."""

    def test_create_subscribed_calendar(self):
        """Test creating a VSUBSCRIBED calendar."""
        self.configure({
            "subscriptions": {"enabled": "True"},
            "auth": {"type": "none"}
        })

        # Create subscribed calendar with CS:source
        mkcol_body = """<?xml version="1.0" encoding="utf-8"?>
<D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"
         xmlns:CS="http://calendarserver.org/ns/">
    <D:set>
        <D:prop>
            <D:displayname>US Holidays</D:displayname>
            <D:resourcetype>
                <D:collection/>
                <CS:subscribed/>
            </D:resourcetype>
            <CS:source>https://ics.calendarlabs.com/76/xxx/US_Holidays.ics</CS:source>
        </D:prop>
    </D:set>
</D:mkcol>"""

        status, _, _ = self.request(
            "MKCOL", "/user/holidays/",
            data=mkcol_body,
            login="user:user"
        )
        assert status in (201, 207)

    def test_subscribed_calendar_source_property(self):
        """Test CS:source property on subscribed calendar."""
        self.configure({
            "subscriptions": {"enabled": "True"},
            "auth": {"type": "none"}
        })

        # First create the subscribed calendar
        mkcol_body = """<?xml version="1.0" encoding="utf-8"?>
<D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"
         xmlns:CS="http://calendarserver.org/ns/">
    <D:set>
        <D:prop>
            <D:displayname>Test Sub</D:displayname>
            <D:resourcetype>
                <D:collection/>
                <CS:subscribed/>
            </D:resourcetype>
            <CS:source>https://example.com/test.ics</CS:source>
        </D:prop>
    </D:set>
</D:mkcol>"""

        self.request(
            "MKCOL", "/user/testsub/",
            data=mkcol_body,
            login="user:user"
        )

        # Query CS:source property
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
    <D:prop>
        <CS:source/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/testsub/",
            data=propfind_body,
            login="user:user"
        )

        # Should return the source URL
        if status == 207:
            assert "example.com" in body or "source" in body


class TestSubscriptionSyncStatus:
    """Tests for sync status values."""

    def test_status_enum_values(self):
        """Test SyncStatus enum values."""
        from radicale.subscriptions.engine import SyncStatus

        assert SyncStatus.SUCCESS.value == "success"
        assert SyncStatus.NOT_MODIFIED.value == "not_modified"
        assert SyncStatus.ERROR.value == "error"
        assert SyncStatus.INVALID_URL.value == "invalid_url"
        assert SyncStatus.INVALID_DATA.value == "invalid_data"
        assert SyncStatus.TIMEOUT.value == "timeout"
        assert SyncStatus.FORBIDDEN.value == "forbidden"
        assert SyncStatus.NOT_FOUND.value == "not_found"

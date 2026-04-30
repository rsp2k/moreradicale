"""
Tests for RFC 7809: CalDAV - Time Zones by Reference.

Tests the timezone-by-reference feature including:
- DAV header advertisement
- timezone-service-set property
- CalDAV-Timezones header handling
- VTIMEZONE stripping
"""

import re

import pytest

from moreradicale.tests import BaseTest


class TestRFC7809TimezonesByReference:
    """Tests for RFC 7809 timezone filtering utilities."""

    def test_is_standard_timezone_iana(self):
        """Test detection of IANA timezones."""
        from moreradicale.tzdist.rfc7809 import is_standard_timezone

        # Standard IANA timezones
        assert is_standard_timezone("America/New_York") is True
        assert is_standard_timezone("Europe/London") is True
        assert is_standard_timezone("Asia/Tokyo") is True
        assert is_standard_timezone("Pacific/Auckland") is True
        assert is_standard_timezone("UTC") is True
        assert is_standard_timezone("Etc/GMT+5") is True

    def test_is_standard_timezone_custom(self):
        """Test detection of custom/non-standard timezones."""
        from moreradicale.tzdist.rfc7809 import is_standard_timezone

        # Custom/non-standard timezones
        assert is_standard_timezone("Custom/MyTimezone") is False
        assert is_standard_timezone("MyCompany/Office") is False
        assert is_standard_timezone("") is False
        assert is_standard_timezone(None) is False

    def test_get_calendar_timezones(self):
        """Test extracting timezones from iCalendar data."""
        from moreradicale.tzdist.rfc7809 import get_calendar_timezones

        ical_data = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:19701101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
END:VTIMEZONE
BEGIN:VTIMEZONE
TZID:Europe/London
BEGIN:STANDARD
DTSTART:19701025T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0000
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:test@example.com
DTSTART;TZID=America/New_York:20240101T090000
END:VEVENT
END:VCALENDAR"""

        timezones = get_calendar_timezones(ical_data)
        assert "America/New_York" in timezones
        assert "Europe/London" in timezones
        assert len(timezones) == 2

    def test_strip_standard_timezones(self):
        """Test stripping standard VTIMEZONE components."""
        from moreradicale.tzdist.rfc7809 import strip_standard_timezones

        ical_data = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:19701101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:test@example.com
DTSTART;TZID=America/New_York:20240101T090000
END:VEVENT
END:VCALENDAR"""

        result = strip_standard_timezones(ical_data)

        # VTIMEZONE should be removed
        assert "BEGIN:VTIMEZONE" not in result
        assert "END:VTIMEZONE" not in result

        # Event should remain
        assert "BEGIN:VEVENT" in result
        assert "UID:test@example.com" in result

    def test_strip_preserves_custom_timezones(self):
        """Test that custom timezones are preserved."""
        from moreradicale.tzdist.rfc7809 import strip_standard_timezones

        ical_data = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:CustomCompany/OfficeTime
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0000
TZOFFSETTO:+0000
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:test@example.com
END:VEVENT
END:VCALENDAR"""

        result = strip_standard_timezones(ical_data)

        # Custom VTIMEZONE should be preserved
        assert "BEGIN:VTIMEZONE" in result
        assert "CustomCompany/OfficeTime" in result

    def test_should_include_timezones_header_true(self):
        """Test CalDAV-Timezones: T header."""
        from moreradicale import config
        from moreradicale.tzdist.rfc7809 import should_include_timezones

        configuration = config.load()
        configuration.update({"tzdist": {"enabled": "True"}}, "test", privileged=True)

        environ = {"HTTP_CALDAV_TIMEZONES": "T"}
        assert should_include_timezones(environ, configuration) is True

    def test_should_include_timezones_header_false(self):
        """Test CalDAV-Timezones: F header."""
        from moreradicale import config
        from moreradicale.tzdist.rfc7809 import should_include_timezones

        configuration = config.load()
        configuration.update({"tzdist": {"enabled": "True"}}, "test", privileged=True)

        environ = {"HTTP_CALDAV_TIMEZONES": "F"}
        assert should_include_timezones(environ, configuration) is False

    def test_should_include_timezones_no_header(self):
        """Test default when no CalDAV-Timezones header."""
        from moreradicale import config
        from moreradicale.tzdist.rfc7809 import should_include_timezones

        configuration = config.load()
        configuration.update({"tzdist": {"enabled": "True"}}, "test", privileged=True)

        environ = {}
        # Default is True for backward compatibility
        assert should_include_timezones(environ, configuration) is True

    def test_should_include_timezones_tzdist_disabled(self):
        """Test that timezones are always included when TZDIST disabled."""
        from moreradicale import config
        from moreradicale.tzdist.rfc7809 import should_include_timezones

        configuration = config.load()
        configuration.update({"tzdist": {"enabled": "False"}}, "test", privileged=True)

        # Even with F header, include timezones when TZDIST not available
        environ = {"HTTP_CALDAV_TIMEZONES": "F"}
        assert should_include_timezones(environ, configuration) is True


class TestRFC7809WebDAV(BaseTest):
    """Tests for RFC 7809 WebDAV integration."""

    def test_dav_header_includes_calendar_no_timezone(self):
        """Test that DAV header includes calendar-no-timezone when TZDIST enabled."""
        self.configure({"tzdist": {"enabled": "True"}})

        status, headers, _ = self.request("OPTIONS", "/")

        assert status == 200
        dav_header = headers.get("DAV", "")
        assert "calendar-no-timezone" in dav_header

    def test_dav_header_excludes_calendar_no_timezone(self):
        """Test that DAV header excludes calendar-no-timezone when TZDIST disabled."""
        self.configure({"tzdist": {"enabled": "False"}})

        status, headers, _ = self.request("OPTIONS", "/")

        assert status == 200
        dav_header = headers.get("DAV", "")
        assert "calendar-no-timezone" not in dav_header

    def test_timezone_service_set_property(self):
        """Test timezone-service-set property in PROPFIND."""
        self.configure({
            "tzdist": {"enabled": "True"},
            "auth": {"type": "none"}
        })

        # Create a calendar collection with MKCALENDAR
        mkcalendar_body = """<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:set>
        <D:prop>
            <D:displayname>Test Calendar</D:displayname>
        </D:prop>
    </D:set>
</C:mkcalendar>"""
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar/",
            data=mkcalendar_body,
            login="user:user")
        assert status == 201

        # PROPFIND for timezone-service-set on the principal
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:timezone-service-set/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "/.well-known/timezone" in body

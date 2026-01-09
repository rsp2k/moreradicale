"""
Tests for Apple CalDAV Default Alarm Properties.

Tests the default alarm properties used by iOS/macOS clients:
- default-alarm-vevent-datetime
- default-alarm-vevent-date
- default-alarm-vtodo-datetime
- default-alarm-vtodo-date
"""

import pytest

from radicale.tests import BaseTest


class TestDefaultAlarms(BaseTest):
    """Tests for Apple CalDAV default alarm properties."""

    SAMPLE_VALARM = """BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT30M
DESCRIPTION:Event reminder
END:VALARM"""

    def test_set_default_alarm_vevent_datetime(self):
        """Test setting default-alarm-vevent-datetime via PROPPATCH."""
        self.configure({"auth": {"type": "none"}})

        # Create a calendar
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

        # Set default alarm via PROPPATCH
        proppatch_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <C:default-alarm-vevent-datetime>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT30M
DESCRIPTION:Event reminder
END:VALARM</C:default-alarm-vevent-datetime>
        </D:prop>
    </D:set>
</D:propertyupdate>"""

        status, _, body = self.request(
            "PROPPATCH", "/user/calendar/",
            data=proppatch_body,
            login="user:user")

        assert status == 207
        assert "200 OK" in body

    def test_get_default_alarm_vevent_datetime(self):
        """Test getting default-alarm-vevent-datetime via PROPFIND."""
        self.configure({"auth": {"type": "none"}})

        # Create a calendar
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

        # Set default alarm via PROPPATCH
        proppatch_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <C:default-alarm-vevent-datetime>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT30M
DESCRIPTION:Event reminder
END:VALARM</C:default-alarm-vevent-datetime>
        </D:prop>
    </D:set>
</D:propertyupdate>"""

        status, _, _ = self.request(
            "PROPPATCH", "/user/calendar/",
            data=proppatch_body,
            login="user:user")
        assert status == 207

        # Get default alarm via PROPFIND
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:default-alarm-vevent-datetime/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/calendar/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "BEGIN:VALARM" in body
        assert "TRIGGER:-PT30M" in body
        assert "200 OK" in body

    def test_set_default_alarm_vevent_date(self):
        """Test setting default-alarm-vevent-date for all-day events."""
        self.configure({"auth": {"type": "none"}})

        # Create a calendar
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

        # Set default alarm for all-day events (day before at 9am)
        proppatch_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <C:default-alarm-vevent-date>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER;VALUE=DATE-TIME:19760401T090000Z
DESCRIPTION:All-day event reminder
END:VALARM</C:default-alarm-vevent-date>
        </D:prop>
    </D:set>
</D:propertyupdate>"""

        status, _, body = self.request(
            "PROPPATCH", "/user/calendar/",
            data=proppatch_body,
            login="user:user")

        assert status == 207
        assert "200 OK" in body

    def test_remove_default_alarm(self):
        """Test removing default alarm via PROPPATCH."""
        self.configure({"auth": {"type": "none"}})

        # Create a calendar
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

        # Set default alarm
        proppatch_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <C:default-alarm-vevent-datetime>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT30M
END:VALARM</C:default-alarm-vevent-datetime>
        </D:prop>
    </D:set>
</D:propertyupdate>"""

        status, _, _ = self.request(
            "PROPPATCH", "/user/calendar/",
            data=proppatch_body,
            login="user:user")
        assert status == 207

        # Remove default alarm
        proppatch_remove_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:remove>
        <D:prop>
            <C:default-alarm-vevent-datetime/>
        </D:prop>
    </D:remove>
</D:propertyupdate>"""

        status, _, body = self.request(
            "PROPPATCH", "/user/calendar/",
            data=proppatch_remove_body,
            login="user:user")

        assert status == 207
        assert "200 OK" in body

        # Verify alarm is gone
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:default-alarm-vevent-datetime/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/calendar/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "404" in body

    def test_default_alarm_not_set_returns_404(self):
        """Test that unset default alarm returns 404."""
        self.configure({"auth": {"type": "none"}})

        # Create a calendar
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

        # Request unset default alarm
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:default-alarm-vevent-datetime/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/calendar/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "404" in body

    def test_set_multiple_default_alarms(self):
        """Test setting all four default alarm properties."""
        self.configure({"auth": {"type": "none"}})

        # Create a calendar
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

        # Set all four default alarm properties
        proppatch_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <C:default-alarm-vevent-datetime>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
END:VALARM</C:default-alarm-vevent-datetime>
            <C:default-alarm-vevent-date>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-P1D
END:VALARM</C:default-alarm-vevent-date>
            <C:default-alarm-vtodo-datetime>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT10M
END:VALARM</C:default-alarm-vtodo-datetime>
            <C:default-alarm-vtodo-date>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT0M
END:VALARM</C:default-alarm-vtodo-date>
        </D:prop>
    </D:set>
</D:propertyupdate>"""

        status, _, body = self.request(
            "PROPPATCH", "/user/calendar/",
            data=proppatch_body,
            login="user:user")

        assert status == 207
        assert "200 OK" in body

        # Verify all four are set
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:default-alarm-vevent-datetime/>
        <C:default-alarm-vevent-date/>
        <C:default-alarm-vtodo-datetime/>
        <C:default-alarm-vtodo-date/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/calendar/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "200 OK" in body
        assert "TRIGGER:-PT15M" in body
        assert "TRIGGER:-P1D" in body
        assert "TRIGGER:-PT10M" in body
        assert "TRIGGER:-PT0M" in body

    def test_default_alarms_in_allprop(self):
        """Test that default alarm properties appear in allprop if set."""
        self.configure({"auth": {"type": "none"}})

        # Create a calendar
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

        # Set a default alarm
        proppatch_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <C:default-alarm-vevent-datetime>BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT30M
END:VALARM</C:default-alarm-vevent-datetime>
        </D:prop>
    </D:set>
</D:propertyupdate>"""

        status, _, _ = self.request(
            "PROPPATCH", "/user/calendar/",
            data=proppatch_body,
            login="user:user")
        assert status == 207

        # Request allprop
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
    <D:allprop/>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/calendar/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        # The set alarm should appear in allprop response
        assert "default-alarm-vevent-datetime" in body
        assert "TRIGGER:-PT30M" in body

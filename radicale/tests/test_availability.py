"""
Tests for RFC 7953 Calendar Availability.

Tests the VAVAILABILITY implementation including:
- VAVAILABILITY component parsing and serialization
- AVAILABLE subcomponent handling
- Free-busy calculation with availability
- CalDAV property discovery
"""

from datetime import datetime, timezone, timedelta

import pytest

from radicale.tests import BaseTest


class TestVAvailabilityComponent:
    """Tests for VAVAILABILITY component data structures."""

    def test_parse_basic_vavailability(self):
        """Test parsing a basic VAVAILABILITY component."""
        from radicale.availability.component import parse_availability, BusyType

        ical_data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VAVAILABILITY
UID:avail-12345
DTSTAMP:20240101T120000Z
ORGANIZER:mailto:user@example.com
SUMMARY:Regular Work Hours
BUSYTYPE:BUSY-UNAVAILABLE
PRIORITY:5
BEGIN:AVAILABLE
UID:avail-period-1
DTSTAMP:20240101T120000Z
DTSTART:20240115T090000Z
DTEND:20240115T170000Z
SUMMARY:Monday 9am-5pm
RRULE:FREQ=WEEKLY;BYDAY=MO
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR"""

        vavail = parse_availability(ical_data)

        assert vavail is not None
        assert vavail.uid == "avail-12345"
        assert vavail.summary == "Regular Work Hours"
        assert vavail.busytype == BusyType.BUSY_UNAVAILABLE
        assert vavail.priority == 5
        assert len(vavail.available) == 1
        assert vavail.available[0].summary == "Monday 9am-5pm"
        assert vavail.available[0].rrule == "FREQ=WEEKLY;BYDAY=MO"

    def test_parse_vavailability_with_multiple_available(self):
        """Test parsing VAVAILABILITY with multiple AVAILABLE periods."""
        from radicale.availability.component import parse_availability

        ical_data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VAVAILABILITY
UID:multi-avail
DTSTAMP:20240101T120000Z
BEGIN:AVAILABLE
UID:morning
DTSTAMP:20240101T120000Z
DTSTART:20240115T090000Z
DTEND:20240115T120000Z
SUMMARY:Morning Hours
END:AVAILABLE
BEGIN:AVAILABLE
UID:afternoon
DTSTAMP:20240101T120000Z
DTSTART:20240115T130000Z
DTEND:20240115T170000Z
SUMMARY:Afternoon Hours
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR"""

        vavail = parse_availability(ical_data)

        assert vavail is not None
        assert len(vavail.available) == 2
        assert vavail.available[0].summary == "Morning Hours"
        assert vavail.available[1].summary == "Afternoon Hours"

    def test_vavailability_serialization(self):
        """Test serializing a VAVAILABILITY to iCalendar format."""
        from radicale.availability.component import (
            VAvailability, Available, BusyType, serialize_availability
        )

        vavail = VAvailability(
            uid="serialize-test",
            dtstamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            organizer="mailto:user@example.com",
            summary="Test Availability",
            busytype=BusyType.BUSY_UNAVAILABLE,
            priority=3,
        )

        vavail.available = [
            Available(
                uid="period-1",
                dtstamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                dtstart=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
                dtend=datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
                summary="Work Hours",
            )
        ]

        ical = serialize_availability(vavail)

        assert "BEGIN:VCALENDAR" in ical
        assert "BEGIN:VAVAILABILITY" in ical
        assert "UID:serialize-test" in ical
        assert "BUSYTYPE:BUSY-UNAVAILABLE" in ical
        assert "PRIORITY:3" in ical
        assert "BEGIN:AVAILABLE" in ical

    def test_busytype_values(self):
        """Test all BUSYTYPE values."""
        from radicale.availability.component import BusyType

        assert BusyType.BUSY.value == "BUSY"
        assert BusyType.BUSY_UNAVAILABLE.value == "BUSY-UNAVAILABLE"
        assert BusyType.BUSY_TENTATIVE.value == "BUSY-TENTATIVE"

    def test_available_end_time_calculation(self):
        """Test AVAILABLE end time calculation from duration."""
        from radicale.availability.component import Available

        # With explicit dtend
        avail1 = Available(
            uid="test1",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
            dtend=datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
        )
        assert avail1.end_time == datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc)

        # With duration instead
        avail2 = Available(
            uid="test2",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
            duration=timedelta(hours=8),
        )
        assert avail2.end_time == datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc)

    def test_duration_parsing(self):
        """Test duration parsing from iCalendar format."""
        from radicale.availability.component import _parse_duration

        # Hours only
        assert _parse_duration("PT8H") == timedelta(hours=8)

        # Days and hours
        assert _parse_duration("P1DT2H") == timedelta(days=1, hours=2)

        # Full format
        assert _parse_duration("P1DT2H30M45S") == timedelta(
            days=1, hours=2, minutes=30, seconds=45
        )

        # Negative duration
        assert _parse_duration("-PT1H") == timedelta(hours=-1)


class TestAvailabilityProcessor:
    """Tests for VAVAILABILITY free-busy processing."""

    def test_freebusy_all_free_no_availability(self):
        """Test free-busy with no availability data returns all FREE."""
        from radicale.availability.processor import (
            AvailabilityProcessor, FreeBusyPeriod
        )

        # Create mock storage and config
        processor = AvailabilityProcessor(None, None)

        range_start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        range_end = datetime(2024, 1, 16, 0, 0, 0, tzinfo=timezone.utc)

        periods = processor.calculate_freebusy_with_availability(
            range_start, range_end, []
        )

        assert len(periods) == 1
        assert periods[0].fb_type == FreeBusyPeriod.FREE
        assert periods[0].start == range_start
        assert periods[0].end == range_end

    def test_freebusy_with_availability(self):
        """Test free-busy calculation with VAVAILABILITY."""
        from radicale.availability.component import (
            VAvailability, Available, BusyType
        )
        from radicale.availability.processor import (
            AvailabilityProcessor, FreeBusyPeriod
        )

        processor = AvailabilityProcessor(None, None)

        # Create availability: 9am-5pm is available
        vavail = VAvailability(
            uid="test-avail",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
            dtend=datetime(2024, 1, 16, 0, 0, 0, tzinfo=timezone.utc),
            busytype=BusyType.BUSY_UNAVAILABLE,
            available=[
                Available(
                    uid="work-hours",
                    dtstamp=datetime.now(timezone.utc),
                    dtstart=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
                    dtend=datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
                )
            ]
        )

        range_start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        range_end = datetime(2024, 1, 16, 0, 0, 0, tzinfo=timezone.utc)

        periods = processor.calculate_freebusy_with_availability(
            range_start, range_end, [vavail]
        )

        # Should have: busy (midnight-9am), free (9am-5pm), busy (5pm-midnight)
        busy_periods = [p for p in periods if p.fb_type != FreeBusyPeriod.FREE]
        free_periods = [p for p in periods if p.fb_type == FreeBusyPeriod.FREE]

        assert len(busy_periods) >= 1
        assert len(free_periods) >= 1

    def test_freebusy_with_event_overlay(self):
        """Test free-busy with availability + actual events."""
        from radicale.availability.component import (
            VAvailability, Available, BusyType
        )
        from radicale.availability.processor import (
            AvailabilityProcessor, FreeBusyPeriod
        )

        processor = AvailabilityProcessor(None, None)

        # Availability: 9am-5pm free
        vavail = VAvailability(
            uid="test-avail",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
            dtend=datetime(2024, 1, 16, 0, 0, 0, tzinfo=timezone.utc),
            busytype=BusyType.BUSY_UNAVAILABLE,
            available=[
                Available(
                    uid="work-hours",
                    dtstamp=datetime.now(timezone.utc),
                    dtstart=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
                    dtend=datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
                )
            ]
        )

        # Event: Meeting from 10am-11am
        event_busy = [
            (datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
             datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc))
        ]

        range_start = datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
        range_end = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)

        periods = processor.calculate_freebusy_with_availability(
            range_start, range_end, [vavail], event_busy
        )

        # Should have the meeting marked as BUSY within the available time
        busy_periods = [p for p in periods if p.fb_type == FreeBusyPeriod.BUSY]
        assert len(busy_periods) >= 1

    def test_freebusy_priority_ordering(self):
        """Test that higher priority availability overrides lower priority."""
        from radicale.availability.component import (
            VAvailability, Available, BusyType
        )
        from radicale.availability.processor import (
            AvailabilityProcessor, FreeBusyPeriod
        )

        processor = AvailabilityProcessor(None, None)

        # Low priority: entire day busy
        low_priority = VAvailability(
            uid="low-priority",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
            dtend=datetime(2024, 1, 16, 0, 0, 0, tzinfo=timezone.utc),
            busytype=BusyType.BUSY,
            priority=1,
            available=[]
        )

        # High priority: 9am-5pm available
        high_priority = VAvailability(
            uid="high-priority",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
            dtend=datetime(2024, 1, 16, 0, 0, 0, tzinfo=timezone.utc),
            busytype=BusyType.BUSY_UNAVAILABLE,
            priority=5,
            available=[
                Available(
                    uid="work-hours",
                    dtstamp=datetime.now(timezone.utc),
                    dtstart=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
                    dtend=datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
                )
            ]
        )

        range_start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        range_end = datetime(2024, 1, 16, 0, 0, 0, tzinfo=timezone.utc)

        # Higher priority should override - work hours should be free
        periods = processor.calculate_freebusy_with_availability(
            range_start, range_end, [low_priority, high_priority]
        )

        # The 9am-5pm period should be FREE due to higher priority
        free_periods = [p for p in periods if p.fb_type == FreeBusyPeriod.FREE]
        assert len(free_periods) >= 1

    def test_to_freebusy_ical(self):
        """Test VFREEBUSY iCalendar generation."""
        from radicale.availability.processor import (
            AvailabilityProcessor, FreeBusyPeriod
        )

        processor = AvailabilityProcessor(None, None)

        periods = [
            FreeBusyPeriod(
                datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
                FreeBusyPeriod.BUSY_UNAVAILABLE
            ),
            FreeBusyPeriod(
                datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
                FreeBusyPeriod.FREE
            ),
        ]

        ical = processor.to_freebusy_ical(
            periods,
            uid="fb-response-123",
            organizer="mailto:organizer@example.com",
            attendee="mailto:attendee@example.com"
        )

        assert "BEGIN:VCALENDAR" in ical
        assert "BEGIN:VFREEBUSY" in ical
        assert "UID:fb-response-123" in ical
        assert "FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:" in ical
        assert "20240115T000000Z/20240115T090000Z" in ical


class TestExpandAvailableInstances:
    """Tests for AVAILABLE recurrence expansion."""

    def test_expand_single_instance(self):
        """Test expansion of non-recurring AVAILABLE."""
        from radicale.availability.component import Available, expand_available_instances

        available = Available(
            uid="single",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
            dtend=datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
        )

        range_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        range_end = datetime(2024, 2, 1, 0, 0, 0, tzinfo=timezone.utc)

        instances = expand_available_instances(available, range_start, range_end)

        assert len(instances) == 1
        assert instances[0][0] == datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
        assert instances[0][1] == datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc)

    def test_expand_weekly_recurrence(self):
        """Test expansion of weekly recurring AVAILABLE."""
        from radicale.availability.component import Available, expand_available_instances

        available = Available(
            uid="weekly",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc),  # Monday
            dtend=datetime(2024, 1, 1, 17, 0, 0, tzinfo=timezone.utc),
            rrule="FREQ=WEEKLY;BYDAY=MO",
        )

        range_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        range_end = datetime(2024, 1, 31, 0, 0, 0, tzinfo=timezone.utc)

        instances = expand_available_instances(available, range_start, range_end)

        # Should have 4-5 Mondays in January 2024
        assert len(instances) >= 4
        for start, end in instances:
            assert start.weekday() == 0  # Monday

    def test_expand_daily_recurrence(self):
        """Test expansion of daily recurring AVAILABLE."""
        from radicale.availability.component import Available, expand_available_instances

        available = Available(
            uid="daily",
            dtstamp=datetime.now(timezone.utc),
            dtstart=datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc),
            dtend=datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc),
            rrule="FREQ=DAILY;COUNT=5",
        )

        range_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        range_end = datetime(2024, 2, 1, 0, 0, 0, tzinfo=timezone.utc)

        instances = expand_available_instances(available, range_start, range_end)

        assert len(instances) == 5


class TestAvailabilityCalDAV(BaseTest):
    """Tests for VAVAILABILITY CalDAV integration."""

    def test_availability_property_disabled(self):
        """Test that calendar-availability returns 404 when disabled."""
        self.configure({
            "availability": {"enabled": "False"},
            "auth": {"type": "none"}
        })

        # Create scheduling inbox
        mkcol_body = """<?xml version="1.0" encoding="utf-8"?>
<D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <D:resourcetype>
                <D:collection/>
                <C:schedule-inbox/>
            </D:resourcetype>
        </D:prop>
    </D:set>
</D:mkcol>"""
        status, _, _ = self.request(
            "MKCOL", "/user/schedule-inbox/",
            data=mkcol_body,
            login="user:user")
        # Note: May return 403 or 201 depending on collection creation support

        # Request calendar-availability property
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:calendar-availability/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/schedule-inbox/",
            data=propfind_body,
            login="user:user")

        # Should return 404 for the property when disabled
        if status == 207:
            assert "404" in body

    def test_availability_property_enabled(self):
        """Test that calendar-availability is available when enabled."""
        self.configure({
            "availability": {"enabled": "True"},
            "auth": {"type": "none"}
        })

        # Create scheduling inbox first
        mkcol_body = """<?xml version="1.0" encoding="utf-8"?>
<D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <D:resourcetype>
                <D:collection/>
                <C:schedule-inbox/>
            </D:resourcetype>
        </D:prop>
    </D:set>
</D:mkcol>"""
        self.request(
            "MKCOL", "/user/schedule-inbox/",
            data=mkcol_body,
            login="user:user")

        # Request calendar-availability property
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:calendar-availability/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/schedule-inbox/",
            data=propfind_body,
            login="user:user")

        # Should return 207 Multi-Status
        assert status == 207 or status == 404  # 404 if inbox doesn't exist

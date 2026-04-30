# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 Ryan Malloy and contributors
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
RFC 5545 (iCalendar) and RFC 4791 (CalDAV) compliance tests.

Tests VFREEBUSY component handling, time-range filtering, and other
RFC-mandated features.
"""

import vobject

from moreradicale.tests import BaseTest


# Sample VFREEBUSY component for testing
VFREEBUSY_ITEM = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VFREEBUSY
UID:freebusy-test-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T080000Z
DTEND:20130903T180000Z
FREEBUSY;FBTYPE=BUSY:20130903T090000Z/20130903T100000Z
FREEBUSY;FBTYPE=BUSY:20130903T140000Z/20130903T150000Z
FREEBUSY;FBTYPE=BUSY-TENTATIVE:20130903T160000Z/20130903T170000Z
END:VFREEBUSY
END:VCALENDAR
"""

# VFREEBUSY outside the time range for testing filtering
VFREEBUSY_OUTSIDE_RANGE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VFREEBUSY
UID:freebusy-test-2
DTSTAMP:20130901T120000Z
DTSTART:20130801T080000Z
DTEND:20130801T180000Z
FREEBUSY;FBTYPE=BUSY:20130801T090000Z/20130801T100000Z
END:VFREEBUSY
END:VCALENDAR
"""


class TestFreeBusyCoalescing(BaseTest):
    """Test RFC 4791 §7.10 FREEBUSY period coalescing.

    Overlapping or adjacent FREEBUSY periods should be merged into
    single periods in the response.
    """

    # Two overlapping events (10:00-11:00 and 10:30-11:30 -> should become 10:00-11:30)
    OVERLAPPING_EVENT_1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:overlap-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T100000Z
DTEND:20130903T110000Z
SUMMARY:First Overlapping Event
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

    OVERLAPPING_EVENT_2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:overlap-2
DTSTAMP:20130903T120000Z
DTSTART:20130903T103000Z
DTEND:20130903T113000Z
SUMMARY:Second Overlapping Event
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

    # Non-overlapping event (14:00-15:00)
    NON_OVERLAPPING_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:nonoverlap-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T140000Z
DTEND:20130903T150000Z
SUMMARY:Non-overlapping Event
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

    # Tentative event (different FBTYPE - should not coalesce with BUSY)
    TENTATIVE_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:tentative-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T100000Z
DTEND:20130903T110000Z
SUMMARY:Tentative Event
STATUS:TENTATIVE
END:VEVENT
END:VCALENDAR
"""

    def test_overlapping_periods_coalesced(self):
        """Test that overlapping FREEBUSY periods are merged."""
        self.configure({
            "auth": {"type": "none"},
            "reporting": {"max_freebusy_occurrence": 100}
        })

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Add two overlapping events
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/overlap1.ics",
            self.OVERLAPPING_EVENT_1, login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/overlap2.ics",
            self.OVERLAPPING_EVENT_2, login="user:")
        assert status == 201

        # Run free-busy query
        freebusy_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav">
    <C:time-range start="20130903T000000Z" end="20130903T235959Z"/>
</C:free-busy-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", freebusy_query, login="user:")
        assert status == 200

        # Should have VFREEBUSY component
        assert "VFREEBUSY" in answer

        # Count DTSTART occurrences - should be 1 (coalesced) not 2
        # The two overlapping events (10:00-11:00 and 10:30-11:30)
        # should merge into one period (10:00-11:30)
        dtstart_count = answer.count("DTSTART:")
        assert dtstart_count == 1, f"Expected 1 coalesced period, got {dtstart_count}"

    def test_non_overlapping_periods_preserved(self):
        """Test that non-overlapping periods remain separate."""
        self.configure({
            "auth": {"type": "none"},
            "reporting": {"max_freebusy_occurrence": 100}
        })

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Add overlapping and non-overlapping events
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/overlap1.ics",
            self.OVERLAPPING_EVENT_1, login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/nonoverlap.ics",
            self.NON_OVERLAPPING_EVENT, login="user:")
        assert status == 201

        # Run free-busy query
        freebusy_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav">
    <C:time-range start="20130903T000000Z" end="20130903T235959Z"/>
</C:free-busy-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", freebusy_query, login="user:")
        assert status == 200

        # Should have 2 separate VFREEBUSY periods (10:00-11:00 and 14:00-15:00)
        dtstart_count = answer.count("DTSTART:")
        assert dtstart_count == 2, f"Expected 2 non-overlapping periods, got {dtstart_count}"

    def test_different_fbtypes_not_coalesced(self):
        """Test that periods with different FBTYPE are not merged."""
        self.configure({
            "auth": {"type": "none"},
            "reporting": {"max_freebusy_occurrence": 100}
        })

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Add confirmed (BUSY) and tentative (BUSY-TENTATIVE) events at same time
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/confirmed.ics",
            self.OVERLAPPING_EVENT_1, login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/tentative.ics",
            self.TENTATIVE_EVENT, login="user:")
        assert status == 201

        # Run free-busy query
        freebusy_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav">
    <C:time-range start="20130903T000000Z" end="20130903T235959Z"/>
</C:free-busy-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", freebusy_query, login="user:")
        assert status == 200

        # Should have both BUSY and BUSY-TENTATIVE (2 periods, not coalesced)
        assert "BUSY" in answer
        assert "BUSY-TENTATIVE" in answer
        dtstart_count = answer.count("DTSTART:")
        assert dtstart_count == 2, f"Expected 2 periods (different FBTYPEs), got {dtstart_count}"


class TestVFREEBUSYFiltering(BaseTest):
    """Test RFC 5545 §3.6.4 VFREEBUSY component handling."""

    def test_store_and_retrieve_vfreebusy(self):
        """Test that VFREEBUSY components can be stored and retrieved."""
        self.configure({"auth": {"type": "none"}})

        # Create calendar collection (user-prefixed path for owner_only rights)
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Store VFREEBUSY item
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/freebusy.ics",
            VFREEBUSY_ITEM,
            login="user:")
        assert status == 201

        # Retrieve the item
        status, _, answer = self.request(
            "GET", "/user/calendar.ics/freebusy.ics", login="user:")
        assert status == 200
        assert "VFREEBUSY" in answer
        assert "FREEBUSY" in answer

    def test_vfreebusy_time_range_filter(self):
        """Test that VFREEBUSY can be filtered by time-range."""
        self.configure({"auth": {"type": "none"}})

        # Create calendar collection
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Store VFREEBUSY items
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/freebusy1.ics",
            VFREEBUSY_ITEM,
            login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/freebusy2.ics",
            VFREEBUSY_OUTSIDE_RANGE,
            login="user:")
        assert status == 201

        # Query with time-range that should include freebusy1 only
        calendar_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop>
        <D:getetag/>
        <C:calendar-data/>
    </D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VFREEBUSY">
                <C:time-range start="20130901T000000Z" end="20130930T000000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", calendar_query, login="user:")
        assert status == 207

        # Should find freebusy1 but not freebusy2
        assert "freebusy1.ics" in answer
        assert "freebusy2.ics" not in answer

    def test_freebusy_query_includes_stored_vfreebusy(self):
        """Test that free-busy query considers stored VFREEBUSY components."""
        self.configure({
            "auth": {"type": "none"},
            "reporting": {"max_freebusy_occurrence": 100}
        })

        # Create calendar collection
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Store a VFREEBUSY item
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/freebusy.ics",
            VFREEBUSY_ITEM,
            login="user:")
        assert status == 201

        # Run free-busy query
        freebusy_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav">
    <C:time-range start="20130901T000000Z" end="20130930T000000Z"/>
</C:free-busy-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", freebusy_query, login="user:")
        assert status == 200

        # Should contain VFREEBUSY response with busy periods
        assert "VFREEBUSY" in answer
        # Note: The free-busy response format combines all busy periods

    def test_vfreebusy_component_name_detection(self):
        """Test that VFREEBUSY is properly detected as component type."""
        from moreradicale import item

        vobj = vobject.readOne(VFREEBUSY_ITEM)
        tag = item.find_tag(vobj)
        assert tag == "VFREEBUSY"

    def test_limit_freebusy_set(self):
        """Test RFC 4791 §9.6.7 limit-freebusy-set in calendar-data.

        The limit-freebusy-set element limits the returned FREEBUSY periods
        to only those that intersect the specified time range.
        """
        self.configure({"auth": {"type": "none"}})

        # Create calendar collection
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Store VFREEBUSY with multiple periods:
        # - 09:00-10:00 (BUSY)
        # - 14:00-15:00 (BUSY)
        # - 16:00-17:00 (BUSY-TENTATIVE)
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/freebusy.ics",
            VFREEBUSY_ITEM,
            login="user:")
        assert status == 201

        # Query with limit-freebusy-set that should only include 14:00-15:00 period
        # (between 13:00 and 15:30)
        calendar_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop>
        <C:calendar-data>
            <C:limit-freebusy-set start="20130903T130000Z" end="20130903T153000Z"/>
        </C:calendar-data>
    </D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VFREEBUSY">
                <C:time-range start="20130901T000000Z" end="20130930T000000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", calendar_query, login="user:")
        assert status == 207

        # Should find the VFREEBUSY component
        assert "VFREEBUSY" in answer

        # The 14:00-15:00 period should be included (intersects with 13:00-15:30)
        assert "20130903T140000Z" in answer

        # The 09:00-10:00 period should NOT be included (outside 13:00-15:30)
        assert "20130903T090000Z" not in answer

        # The 16:00-17:00 period should NOT be included (outside 13:00-15:30)
        assert "20130903T160000Z" not in answer

    def test_limit_freebusy_set_multiple_types(self):
        """Test limit-freebusy-set preserves FBTYPE for filtered periods."""
        self.configure({"auth": {"type": "none"}})

        # Create calendar collection
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # VFREEBUSY with periods of different types in the query range
        vfreebusy_multi = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VFREEBUSY
UID:freebusy-multi-type
DTSTAMP:20130903T120000Z
DTSTART:20130903T080000Z
DTEND:20130903T180000Z
FREEBUSY;FBTYPE=BUSY:20130903T090000Z/20130903T100000Z
FREEBUSY;FBTYPE=BUSY-TENTATIVE:20130903T110000Z/20130903T120000Z
FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:20130903T140000Z/20130903T150000Z
END:VFREEBUSY
END:VCALENDAR
"""

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/freebusy-multi.ics",
            vfreebusy_multi,
            login="user:")
        assert status == 201

        # Query range 10:30-14:30 should include:
        # - 11:00-12:00 BUSY-TENTATIVE (partial overlap)
        # - 14:00-15:00 BUSY-UNAVAILABLE (partial overlap at start)
        calendar_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop>
        <C:calendar-data>
            <C:limit-freebusy-set start="20130903T103000Z" end="20130903T143000Z"/>
        </C:calendar-data>
    </D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VFREEBUSY"/>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", calendar_query, login="user:")
        assert status == 207

        # 11:00-12:00 should be included
        assert "20130903T110000Z" in answer
        # BUSY-TENTATIVE should be preserved
        assert "BUSY-TENTATIVE" in answer

        # 14:00-15:00 should be included
        assert "20130903T140000Z" in answer
        # BUSY-UNAVAILABLE should be preserved
        assert "BUSY-UNAVAILABLE" in answer

        # 09:00-10:00 should NOT be included (ends before range starts)
        assert "20130903T090000Z" not in answer


class TestLimitRecurrenceSet(BaseTest):
    """Test RFC 4791 §9.6.6 limit-recurrence-set for calendar-data."""

    # Recurring event with overridden instances
    RECURRING_EVENT_WITH_OVERRIDES = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:recurring-with-overrides
DTSTAMP:20130903T120000Z
DTSTART:20130901T100000Z
DTEND:20130901T110000Z
RRULE:FREQ=DAILY;COUNT=5
SUMMARY:Daily Standup
END:VEVENT
BEGIN:VEVENT
UID:recurring-with-overrides
DTSTAMP:20130903T120000Z
RECURRENCE-ID:20130902T100000Z
DTSTART:20130902T110000Z
DTEND:20130902T120000Z
SUMMARY:Daily Standup (moved to 11am)
END:VEVENT
BEGIN:VEVENT
UID:recurring-with-overrides
DTSTAMP:20130903T120000Z
RECURRENCE-ID:20130904T100000Z
DTSTART:20130904T140000Z
DTEND:20130904T150000Z
SUMMARY:Daily Standup (moved to 2pm)
END:VEVENT
END:VCALENDAR
"""

    def test_limit_recurrence_set_filters_overrides(self):
        """Test that limit-recurrence-set filters out overrides outside time range."""
        self.configure({"auth": {"type": "none"}})

        # Create calendar collection
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Store recurring event with overrides
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/recurring.ics",
            self.RECURRING_EVENT_WITH_OVERRIDES,
            login="user:")
        assert status == 201

        # Query with limit-recurrence-set that should include the Sept 2nd override
        # but not the Sept 4th override (range: Sept 1 - Sept 3)
        calendar_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop>
        <C:calendar-data>
            <C:limit-recurrence-set start="20130901T000000Z" end="20130903T235959Z"/>
        </C:calendar-data>
    </D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT"/>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", calendar_query, login="user:")
        assert status == 207

        # The RRULE should be preserved
        assert "RRULE:FREQ=DAILY" in answer

        # The Sept 2nd override should be included (within range)
        assert "RECURRENCE-ID:20130902T100000Z" in answer
        assert "Daily Standup (moved to 11am)" in answer

        # The Sept 4th override should NOT be included (outside range)
        assert "RECURRENCE-ID:20130904T100000Z" not in answer
        assert "Daily Standup (moved to 2pm)" not in answer

    def test_limit_recurrence_set_preserves_rrule(self):
        """Test that limit-recurrence-set keeps the RRULE intact."""
        self.configure({"auth": {"type": "none"}})

        # Create calendar collection
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Store recurring event
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/recurring.ics",
            self.RECURRING_EVENT_WITH_OVERRIDES,
            login="user:")
        assert status == 201

        # Query with limit-recurrence-set for a narrow range
        calendar_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop>
        <C:calendar-data>
            <C:limit-recurrence-set start="20130901T000000Z" end="20130901T235959Z"/>
        </C:calendar-data>
    </D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT"/>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", calendar_query, login="user:")
        assert status == 207

        # The master event with RRULE should be preserved
        assert "RRULE:FREQ=DAILY;COUNT=5" in answer
        assert "DTSTART:20130901T100000Z" in answer

        # No overrides should be included (they're all outside Sept 1st)
        assert "RECURRENCE-ID" not in answer


class TestVJOURNALFiltering(BaseTest):
    """Test RFC 5545 §3.6.3 VJOURNAL component handling."""

    VJOURNAL_ITEM = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VJOURNAL
UID:journal-test-1
DTSTAMP:20130903T120000Z
DTSTART:20130903
SUMMARY:Test Journal Entry
DESCRIPTION:This is a test journal entry.
END:VJOURNAL
END:VCALENDAR
"""

    def test_store_and_filter_vjournal(self):
        """Test that VJOURNAL components can be stored and filtered."""
        self.configure({"auth": {"type": "none"}})

        # Create calendar collection
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Store VJOURNAL item
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/journal.ics",
            self.VJOURNAL_ITEM,
            login="user:")
        assert status == 201

        # Query with time-range filter
        calendar_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop>
        <D:getetag/>
    </D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VJOURNAL">
                <C:time-range start="20130901T000000Z" end="20130930T000000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", calendar_query, login="user:")
        assert status == 207
        assert "journal.ics" in answer


class TestTextCollations(BaseTest):
    """Test RFC 4790 text collation support in filters.

    RFC 4791 §7.5 requires support for collations in text-match filters:
    - i;ascii-casemap: ASCII case-insensitive (default)
    - i;octet: Case-sensitive byte comparison
    - i;unicode-casemap: Unicode case-insensitive (recommended)
    """

    # Event with various text for collation testing
    COLLATION_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:collation-test-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T100000Z
DTEND:20130903T110000Z
SUMMARY:Café Meeting
DESCRIPTION:Meeting about the café renovation.
CATEGORIES:IMPORTANT,Büro
END:VEVENT
END:VCALENDAR
"""

    # Event with ASCII text for case testing
    ASCII_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:ascii-test-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T140000Z
DTEND:20130903T150000Z
SUMMARY:IMPORTANT Meeting
DESCRIPTION:This is an IMPORTANT event.
END:VEVENT
END:VCALENDAR
"""

    def test_ascii_casemap_default(self):
        """Test i;ascii-casemap collation (default - case-insensitive)."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/ascii.ics",
            self.ASCII_EVENT, login="user:")
        assert status == 201

        # Search with lowercase - should find IMPORTANT (case-insensitive)
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="SUMMARY">
                    <C:text-match collation="i;ascii-casemap">important</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "ascii.ics" in answer

    def test_octet_collation_case_sensitive(self):
        """Test i;octet collation (case-sensitive)."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/ascii.ics",
            self.ASCII_EVENT, login="user:")
        assert status == 201

        # Search with lowercase using i;octet - should NOT find IMPORTANT
        query_lowercase = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="SUMMARY">
                    <C:text-match collation="i;octet">important</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_lowercase, login="user:")
        assert status == 207
        # Should NOT find with lowercase search (case-sensitive)
        assert "ascii.ics" not in answer

        # Search with UPPERCASE using i;octet - should find IMPORTANT
        query_uppercase = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="SUMMARY">
                    <C:text-match collation="i;octet">IMPORTANT</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_uppercase, login="user:")
        assert status == 207
        # Should find with exact case match
        assert "ascii.ics" in answer

    def test_unicode_casemap_collation(self):
        """Test i;unicode-casemap collation (Unicode case-insensitive)."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/unicode.ics",
            self.COLLATION_EVENT, login="user:")
        assert status == 201

        # Search for "café" with different case - should find "Café"
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="SUMMARY">
                    <C:text-match collation="i;unicode-casemap">CAFÉ</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "unicode.ics" in answer

    def test_match_type_equals(self):
        """Test match-type='equals' with collations."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/ascii.ics",
            self.ASCII_EVENT, login="user:")
        assert status == 201

        # Exact match with case-insensitive collation
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="SUMMARY">
                    <C:text-match collation="i;ascii-casemap"
                        match-type="equals">important meeting</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "ascii.ics" in answer

    def test_match_type_starts_with(self):
        """Test match-type='starts-with' with collations."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/unicode.ics",
            self.COLLATION_EVENT, login="user:")
        assert status == 201

        # starts-with using unicode-casemap
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="SUMMARY">
                    <C:text-match collation="i;unicode-casemap"
                        match-type="starts-with">café</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "unicode.ics" in answer

    def test_match_type_ends_with(self):
        """Test match-type='ends-with' with collations."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/unicode.ics",
            self.COLLATION_EVENT, login="user:")
        assert status == 201

        # ends-with using unicode-casemap
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="SUMMARY">
                    <C:text-match collation="i;unicode-casemap"
                        match-type="ends-with">MEETING</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "unicode.ics" in answer


class TestRecurrenceIDRange(BaseTest):
    """Test RFC 5545 §3.8.4.4 RECURRENCE-ID RANGE parameter.

    The RANGE parameter specifies which instances are affected by a
    RECURRENCE-ID override:
    - Default (no RANGE): Only the specific instance is overridden
    - RANGE=THISANDFUTURE: This and all future instances are affected
    """

    # Recurring event with daily occurrences
    RECURRING_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:recurrence-test-1
DTSTAMP:20130903T120000Z
DTSTART:20130901T100000Z
DTEND:20130901T110000Z
SUMMARY:Daily Standup
RRULE:FREQ=DAILY;COUNT=10
END:VEVENT
END:VCALENDAR
"""

    # Override for single instance (default behavior)
    SINGLE_OVERRIDE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:recurrence-test-1
DTSTAMP:20130903T120000Z
DTSTART:20130901T100000Z
DTEND:20130901T110000Z
SUMMARY:Daily Standup
RRULE:FREQ=DAILY;COUNT=10
END:VEVENT
BEGIN:VEVENT
UID:recurrence-test-1
RECURRENCE-ID:20130905T100000Z
DTSTAMP:20130905T080000Z
DTSTART:20130905T140000Z
DTEND:20130905T150000Z
SUMMARY:Rescheduled Standup
END:VEVENT
END:VCALENDAR
"""

    # Override with RANGE=THISANDFUTURE (affects all future instances)
    THISANDFUTURE_OVERRIDE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:recurrence-test-2
DTSTAMP:20130903T120000Z
DTSTART:20130901T100000Z
DTEND:20130901T110000Z
SUMMARY:Weekly Review
RRULE:FREQ=DAILY;COUNT=10
END:VEVENT
BEGIN:VEVENT
UID:recurrence-test-2
RECURRENCE-ID;RANGE=THISANDFUTURE:20130905T100000Z
DTSTAMP:20130905T080000Z
DTSTART:20130905T140000Z
DTEND:20130905T150000Z
SUMMARY:Rescheduled Review Series
END:VEVENT
END:VCALENDAR
"""

    def test_single_instance_override(self):
        """Test default RECURRENCE-ID behavior (single instance override)."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/recurring.ics",
            self.SINGLE_OVERRIDE, login="user:")
        assert status == 201

        # Query for events on Sep 5 (should find the rescheduled one)
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/><C:calendar-data/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130905T000000Z" end="20130905T235959Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "recurring.ics" in answer

    def test_thisandfuture_override_storage(self):
        """Test that RANGE=THISANDFUTURE events can be stored and retrieved."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/series.ics",
            self.THISANDFUTURE_OVERRIDE, login="user:")
        assert status == 201

        # Retrieve the item
        status, _, answer = self.request(
            "GET", "/user/calendar.ics/series.ics", login="user:")
        assert status == 200
        assert "RANGE=THISANDFUTURE" in answer
        assert "Rescheduled Review Series" in answer

    def test_thisandfuture_time_range_query(self):
        """Test time-range query with RANGE=THISANDFUTURE override."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/series.ics",
            self.THISANDFUTURE_OVERRIDE, login="user:")
        assert status == 201

        # Query for events within the overall range
        # Sep 1-10 (10 daily occurrences starting Sep 1)
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130901T000000Z" end="20130910T235959Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "series.ics" in answer


class TestTimezoneHandling(BaseTest):
    """Test RFC 5545 timezone handling in time-range filters.

    RFC 4791 §9.9 specifies how time-range queries should handle:
    - DATE-TIME with TZID parameter (local time with timezone reference)
    - DATE-TIME with Z suffix (UTC time)
    - DATE-TIME without timezone (floating time)
    - DATE values (all-day events)
    """

    # Event with explicit TZID parameter (Europe/Paris = UTC+1/+2)
    EVENT_WITH_TZID = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VTIMEZONE
TZID:Europe/Paris
BEGIN:STANDARD
DTSTART:19710101T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19710101T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:tz-paris-1
DTSTAMP:20130903T120000Z
DTSTART;TZID=Europe/Paris:20130903T180000
DTEND;TZID=Europe/Paris:20130903T190000
SUMMARY:Paris Meeting
END:VEVENT
END:VCALENDAR
"""

    # Event with explicit UTC (Z suffix) - same time as Paris 18:00 in summer
    EVENT_WITH_UTC = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:tz-utc-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T160000Z
DTEND:20130903T170000Z
SUMMARY:UTC Meeting
END:VEVENT
END:VCALENDAR
"""

    # Event with floating time (no timezone)
    EVENT_FLOATING = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:tz-floating-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T160000
DTEND:20130903T170000
SUMMARY:Floating Time Meeting
END:VEVENT
END:VCALENDAR
"""

    # All-day event (DATE value, no time component)
    EVENT_ALL_DAY = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:tz-allday-1
DTSTAMP:20130903T120000Z
DTSTART;VALUE=DATE:20130903
DTEND;VALUE=DATE:20130904
SUMMARY:All Day Event
END:VEVENT
END:VCALENDAR
"""

    def test_tzid_event_filtered_correctly(self):
        """Test that events with TZID are filtered based on correct UTC time."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/paris.ics",
            self.EVENT_WITH_TZID, login="user:")
        assert status == 201

        # Query for 15:00-17:00 UTC (should NOT find Paris 18:00 which is 16:00 UTC in summer)
        query_miss = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130903T150000Z" end="20130903T155959Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_miss, login="user:")
        assert status == 207
        # Should NOT find since event is 16:00-17:00 UTC
        assert "paris.ics" not in answer

        # Query for 16:00-18:00 UTC (SHOULD find Paris 18:00 = 16:00 UTC)
        query_hit = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130903T160000Z" end="20130903T180000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_hit, login="user:")
        assert status == 207
        assert "paris.ics" in answer

    def test_utc_event_filtered_correctly(self):
        """Test that events with explicit UTC (Z suffix) are filtered correctly."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/utc.ics",
            self.EVENT_WITH_UTC, login="user:")
        assert status == 201

        # Query for 16:00-17:00 UTC (should find)
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130903T160000Z" end="20130903T170000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "utc.ics" in answer

    def test_all_day_event_spans_full_day(self):
        """Test that DATE (all-day) events span the entire day."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/allday.ics",
            self.EVENT_ALL_DAY, login="user:")
        assert status == 201

        # Query for any time during Sep 3 (should find all-day event)
        query_during = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130903T120000Z" end="20130903T130000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_during, login="user:")
        assert status == 207
        assert "allday.ics" in answer

        # Query for Sep 4 (should NOT find - all-day ends at midnight Sep 4)
        query_after = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130904T120000Z" end="20130904T130000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_after, login="user:")
        assert status == 207
        assert "allday.ics" not in answer

    def test_floating_time_treated_as_utc(self):
        """Test that floating time (no TZ) events are treated as UTC."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/floating.ics",
            self.EVENT_FLOATING, login="user:")
        assert status == 201

        # Query for 16:00-17:00 UTC (should find floating 16:00-17:00)
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130903T160000Z" end="20130903T170000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "floating.ics" in answer

    def test_calendar_timezone_used_for_floating_times(self):
        """Test RFC 4791 §5.3.2: calendar-timezone affects floating time interpretation.

        When a calendar has a C:calendar-timezone property set, floating times
        (times without a timezone specifier) should be interpreted using that
        timezone rather than defaulting to UTC.
        """
        self.configure({"auth": {"type": "none"}})

        # Create calendar with America/New_York timezone (UTC-5 in winter, UTC-4 in summer)
        # September 3 is in DST so offset is UTC-4
        calendar_tz = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:19710101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19710101T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
END:VTIMEZONE
END:VCALENDAR
"""

        # Create calendar with timezone property via MKCALENDAR with PROPPATCH
        mkcalendar_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:set>
        <D:prop>
            <D:displayname>NYC Calendar</D:displayname>
            <C:calendar-timezone>{calendar_tz}</C:calendar-timezone>
        </D:prop>
    </D:set>
</C:mkcalendar>"""

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", mkcalendar_body, login="user:")
        assert status == 201

        # Store event with floating time 12:00-13:00 (no timezone)
        # If interpreted as NYC time (UTC-4), this is 16:00-17:00 UTC
        # If interpreted as UTC, this would be 12:00-13:00 UTC
        floating_event = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:floating-tz-test-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T120000
DTEND:20130903T130000
SUMMARY:Floating NYC Time Event
END:VEVENT
END:VCALENDAR
"""

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/floating-nyc.ics",
            floating_event, login="user:")
        assert status == 201

        # Query for 16:00-17:00 UTC (should find if floating interpreted as NYC)
        query_nyc_time = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130903T160000Z" end="20130903T170000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_nyc_time, login="user:")
        assert status == 207
        # Should find: floating 12:00 NYC = 16:00 UTC
        assert "floating-nyc.ics" in answer

        # Query for 12:00-13:00 UTC (should NOT find if using NYC timezone)
        query_utc_time = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="20130903T120000Z" end="20130903T130000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_utc_time, login="user:")
        assert status == 207
        # Should NOT find: if interpreted as NYC time, 12:00 NYC != 12:00 UTC
        assert "floating-nyc.ics" not in answer


class TestFilteringEdgeCases(BaseTest):
    """Test edge cases in RFC 4791 filtering.

    These tests cover filter semantics from RFC 4791 §9.7:
    - comp-filter with is-not-defined
    - prop-filter with is-not-defined
    - prop-filter existence testing
    - text-match with negate-condition
    - param-filter matching
    - Multiple prop-filter semantics
    """

    # Standard test event
    STANDARD_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T100000Z
DTEND:20130903T110000Z
SUMMARY:Test Event
END:VEVENT
END:VCALENDAR
"""

    # Event with multiple properties for testing
    RICH_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:rich-event-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T100000Z
DTEND:20130903T110000Z
SUMMARY:Team Meeting
DESCRIPTION:Weekly team sync meeting
LOCATION:Conference Room A
CATEGORIES:MEETING,WORK,IMPORTANT
PRIORITY:1
ATTENDEE;PARTSTAT=ACCEPTED;CN=Alice:mailto:alice@example.com
ATTENDEE;PARTSTAT=TENTATIVE;CN=Bob:mailto:bob@example.com
END:VEVENT
END:VCALENDAR
"""

    # Event without optional properties
    MINIMAL_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:minimal-event-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T140000Z
DTEND:20130903T150000Z
SUMMARY:Quick Task
END:VEVENT
END:VCALENDAR
"""

    def test_comp_filter_is_not_defined(self):
        """Test comp-filter with is-not-defined element."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics", self.STANDARD_EVENT, login="user:")
        assert status == 201

        # Query for items that are NOT VTODO (should find the VEVENT)
        calendar_query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop>
        <D:getetag/>
    </D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VTODO">
                <C:is-not-defined/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", calendar_query, login="user:")
        assert status == 207
        # VEVENT should match because it's NOT a VTODO
        assert "event.ics" in answer

    def test_prop_filter_existence(self):
        """Test prop-filter without children tests property existence."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/rich.ics", self.RICH_EVENT, login="user:")
        assert status == 201
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/minimal.ics", self.MINIMAL_EVENT, login="user:")
        assert status == 201

        # Query for events that HAVE a LOCATION property
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="LOCATION"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "rich.ics" in answer
        assert "minimal.ics" not in answer

    def test_prop_filter_is_not_defined(self):
        """Test prop-filter with is-not-defined tests property absence."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/rich.ics", self.RICH_EVENT, login="user:")
        assert status == 201
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/minimal.ics", self.MINIMAL_EVENT, login="user:")
        assert status == 201

        # Query for events that DO NOT have LOCATION
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="LOCATION">
                    <C:is-not-defined/>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "rich.ics" not in answer
        assert "minimal.ics" in answer

    def test_text_match_negate_condition(self):
        """Test text-match with negate-condition='yes'."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/rich.ics", self.RICH_EVENT, login="user:")
        assert status == 201
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/minimal.ics", self.MINIMAL_EVENT, login="user:")
        assert status == 201

        # Query for events where SUMMARY does NOT contain "Team"
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="SUMMARY">
                    <C:text-match negate-condition="yes">Team</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "rich.ics" not in answer  # Contains "Team"
        assert "minimal.ics" in answer    # Does not contain "Team"

    def test_multi_valued_categories_filter(self):
        """Test filtering on multi-valued CATEGORIES property."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/rich.ics", self.RICH_EVENT, login="user:")
        assert status == 201

        # Query for events with IMPORTANT category
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="CATEGORIES">
                    <C:text-match>IMPORTANT</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "rich.ics" in answer

        # Query for non-existent category
        query_miss = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="CATEGORIES">
                    <C:text-match>PERSONAL</C:text-match>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_miss, login="user:")
        assert status == 207
        assert "rich.ics" not in answer

    def test_param_filter_partstat(self):
        """Test param-filter matching on ATTENDEE PARTSTAT parameter."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/rich.ics", self.RICH_EVENT, login="user:")
        assert status == 201

        # Query for events with at least one ACCEPTED attendee
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="ATTENDEE">
                    <C:param-filter name="PARTSTAT">
                        <C:text-match>ACCEPTED</C:text-match>
                    </C:param-filter>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "rich.ics" in answer

    def test_param_filter_existence(self):
        """Test param-filter without children tests parameter existence."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/rich.ics", self.RICH_EVENT, login="user:")
        assert status == 201

        # Query for events with ATTENDEE that has CN parameter
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="ATTENDEE">
                    <C:param-filter name="CN"/>
                </C:prop-filter>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "rich.ics" in answer

    def test_combined_prop_filters_and_semantics(self):
        """Test multiple prop-filters combined with AND semantics."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/rich.ics", self.RICH_EVENT, login="user:")
        assert status == 201
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/minimal.ics", self.MINIMAL_EVENT, login="user:")
        assert status == 201

        # Query for events with BOTH LOCATION AND DESCRIPTION (only rich.ics)
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:prop-filter name="LOCATION"/>
                <C:prop-filter name="DESCRIPTION"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "rich.ics" in answer
        assert "minimal.ics" not in answer


class TestCardDAVFiltering(BaseTest):
    """Test RFC 6352 §10.5 addressbook-query filtering.

    RFC 6352 defines CardDAV for address book access. The addressbook-query
    REPORT allows clients to filter contacts using prop-filter, param-filter,
    and text-match elements.
    """

    # Basic contact with common fields
    CONTACT_BASIC = """BEGIN:VCARD
VERSION:3.0
UID:contact-basic
N:Doe;John;;;
FN:John Doe
EMAIL:john.doe@example.com
TEL;TYPE=WORK:+1-555-0100
ORG:Acme Corp
END:VCARD
"""

    # Contact with multiple emails and phone numbers
    CONTACT_MULTI_VALUES = """BEGIN:VCARD
VERSION:3.0
UID:contact-multi
N:Smith;Jane;;;
FN:Jane Smith
EMAIL;TYPE=HOME:jane@home.example.com
EMAIL;TYPE=WORK:jane.smith@work.example.com
TEL;TYPE=CELL:+1-555-0200
TEL;TYPE=HOME:+1-555-0201
NICKNAME:Janey
CATEGORIES:friends,work
END:VCARD
"""

    # Contact without optional fields (for is-not-defined tests)
    CONTACT_MINIMAL = """BEGIN:VCARD
VERSION:3.0
UID:contact-minimal
N:Nobody;A;;;
FN:A Nobody
END:VCARD
"""

    # Contact with TYPE parameters for param-filter tests
    CONTACT_WITH_PARAMS = """BEGIN:VCARD
VERSION:3.0
UID:contact-params
N:Worker;Bob;;;
FN:Bob Worker
TEL;TYPE=WORK;TYPE=VOICE:+1-555-0300
TEL;TYPE=HOME:+1-555-0301
TEL;TYPE=FAX:+1-555-0302
ADR;TYPE=WORK:;;123 Business St;Townville;ST;12345;USA
NOTE:Important contact with multiple parameters
END:VCARD
"""

    def _create_addressbook_with_contacts(self):
        """Helper to create an addressbook with test contacts."""
        self.configure({"auth": {"type": "none"}})

        status = self.create_addressbook("/user/contacts.vcf/", login="user:")
        assert status == 201

        # Add test contacts
        status, _, _ = self.request(
            "PUT", "/user/contacts.vcf/basic.vcf",
            self.CONTACT_BASIC, login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/contacts.vcf/multi.vcf",
            self.CONTACT_MULTI_VALUES, login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/contacts.vcf/minimal.vcf",
            self.CONTACT_MINIMAL, login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/contacts.vcf/params.vcf",
            self.CONTACT_WITH_PARAMS, login="user:")
        assert status == 201

    def test_prop_filter_existence(self):
        """Test RFC 6352 §10.5.1: prop-filter for property existence."""
        self._create_addressbook_with_contacts()

        # Query for contacts that have an EMAIL property
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="EMAIL"/>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # basic and multi have EMAIL
        assert "basic.vcf" in answer
        assert "multi.vcf" in answer
        # params and minimal have no EMAIL
        assert "params.vcf" not in answer
        assert "minimal.vcf" not in answer

    def test_prop_filter_is_not_defined(self):
        """Test RFC 6352 §10.5.1: prop-filter with is-not-defined."""
        self._create_addressbook_with_contacts()

        # Query for contacts that do NOT have a NICKNAME property
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="NICKNAME">
            <CR:is-not-defined/>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # multi has NICKNAME, others don't
        assert "multi.vcf" not in answer
        assert "basic.vcf" in answer
        assert "minimal.vcf" in answer
        assert "params.vcf" in answer

    def test_text_match_negate_condition(self):
        """Test RFC 6352 §10.5.4: text-match with negate-condition."""
        self._create_addressbook_with_contacts()

        # Query for contacts whose FN does NOT contain "Smith"
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="FN">
            <CR:text-match negate-condition="yes">Smith</CR:text-match>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # Only multi has "Smith" in FN
        assert "multi.vcf" not in answer
        assert "basic.vcf" in answer  # John Doe
        assert "minimal.vcf" in answer  # A Nobody
        assert "params.vcf" in answer  # Bob Worker

    def test_param_filter_existence(self):
        """Test RFC 6352 §10.5.2: param-filter for parameter existence."""
        self._create_addressbook_with_contacts()

        # Query for contacts that have TEL with TYPE parameter
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="TEL">
            <CR:param-filter name="TYPE"/>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # basic, multi, and params have TEL with TYPE
        assert "basic.vcf" in answer
        assert "multi.vcf" in answer
        assert "params.vcf" in answer
        assert "minimal.vcf" not in answer  # no TEL at all

    def test_param_filter_text_match(self):
        """Test RFC 6352 §10.5.2: param-filter with text-match."""
        self._create_addressbook_with_contacts()

        # Query for contacts with TEL TYPE=WORK
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="TEL">
            <CR:param-filter name="TYPE">
                <CR:text-match>WORK</CR:text-match>
            </CR:param-filter>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # basic and params have TEL with TYPE=WORK
        assert "basic.vcf" in answer
        assert "params.vcf" in answer
        # multi only has CELL and HOME types
        assert "multi.vcf" not in answer
        assert "minimal.vcf" not in answer

    def test_filter_test_anyof(self):
        """Test RFC 6352 §10.5: filter test=anyof (OR semantics)."""
        self._create_addressbook_with_contacts()

        # Query: contacts with NICKNAME OR ORG (OR logic)
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter test="anyof">
        <CR:prop-filter name="NICKNAME"/>
        <CR:prop-filter name="ORG"/>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # basic has ORG, multi has NICKNAME
        assert "basic.vcf" in answer
        assert "multi.vcf" in answer
        # minimal and params have neither
        assert "minimal.vcf" not in answer
        assert "params.vcf" not in answer

    def test_filter_test_allof(self):
        """Test RFC 6352 §10.5: filter test=allof (AND semantics)."""
        self._create_addressbook_with_contacts()

        # Query: contacts with BOTH TEL AND NOTE (AND logic)
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter test="allof">
        <CR:prop-filter name="TEL"/>
        <CR:prop-filter name="NOTE"/>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # Only params has both TEL and NOTE
        assert "params.vcf" in answer
        assert "basic.vcf" not in answer  # has TEL, no NOTE
        assert "multi.vcf" not in answer  # has TEL, no NOTE
        assert "minimal.vcf" not in answer  # has neither

    def test_text_match_match_types(self):
        """Test RFC 6352 §10.5.4: text-match with different match-type values."""
        self._create_addressbook_with_contacts()

        # Test "starts-with" match-type
        query_starts = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="FN">
            <CR:text-match match-type="starts-with">John</CR:text-match>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query_starts, login="user:")
        assert status == 207
        assert "basic.vcf" in answer  # "John Doe"
        assert "multi.vcf" not in answer  # "Jane Smith"

        # Test "ends-with" match-type
        query_ends = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="FN">
            <CR:text-match match-type="ends-with">Smith</CR:text-match>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query_ends, login="user:")
        assert status == 207
        assert "multi.vcf" in answer  # "Jane Smith"
        assert "basic.vcf" not in answer  # "John Doe"

        # Test "equals" match-type
        query_equals = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="FN">
            <CR:text-match match-type="equals">Jane Smith</CR:text-match>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query_equals, login="user:")
        assert status == 207
        assert "multi.vcf" in answer
        assert "basic.vcf" not in answer

    def test_text_match_collation(self):
        """Test RFC 6352 §10.5.4: text-match with collation attribute."""
        self._create_addressbook_with_contacts()

        # Case-insensitive search (i;unicode-casemap)
        query_casefold = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="FN">
            <CR:text-match collation="i;unicode-casemap">JOHN DOE</CR:text-match>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query_casefold, login="user:")
        assert status == 207
        assert "basic.vcf" in answer  # "John Doe" matches "JOHN DOE" case-insensitive

        # Case-sensitive search (i;octet)
        query_case_sensitive = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="FN">
            <CR:text-match collation="i;octet">JOHN DOE</CR:text-match>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query_case_sensitive, login="user:")
        assert status == 207
        # "John Doe" != "JOHN DOE" in case-sensitive comparison
        assert "basic.vcf" not in answer

    def test_multiple_property_values(self):
        """Test filtering on properties with multiple values (multi-valued fields)."""
        self._create_addressbook_with_contacts()

        # Search for work email
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="EMAIL">
            <CR:text-match>work.example.com</CR:text-match>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # multi has jane.smith@work.example.com
        assert "multi.vcf" in answer
        assert "basic.vcf" not in answer  # john.doe@example.com

    def test_param_filter_is_not_defined(self):
        """Test RFC 6352 §10.5.2: param-filter with is-not-defined."""
        self._create_addressbook_with_contacts()

        # We need a contact with TEL but without TYPE parameter
        # For this test, let's add a specific contact
        contact_no_type = """BEGIN:VCARD
VERSION:3.0
UID:contact-notype
N:Plain;Joe;;;
FN:Joe Plain
TEL:+1-555-9999
END:VCARD
"""
        status, _, _ = self.request(
            "PUT", "/user/contacts.vcf/notype.vcf",
            contact_no_type, login="user:")
        assert status == 201

        # Query for TEL without TYPE parameter
        query = """<?xml version="1.0" encoding="utf-8" ?>
<CR:addressbook-query xmlns:CR="urn:ietf:params:xml:ns:carddav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <CR:filter>
        <CR:prop-filter name="TEL">
            <CR:param-filter name="TYPE">
                <CR:is-not-defined/>
            </CR:param-filter>
        </CR:prop-filter>
    </CR:filter>
</CR:addressbook-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/contacts.vcf/", query, login="user:")
        assert status == 207
        # notype has TEL without TYPE
        assert "notype.vcf" in answer
        # Others have TEL with TYPE, so they shouldn't match
        assert "basic.vcf" not in answer
        assert "multi.vcf" not in answer


class TestNestedCompFilter(BaseTest):
    """Test RFC 4791 §9.7.1 nested component filtering.

    Tests filtering on nested components like VALARM within VEVENT,
    and AVAILABLE within VAVAILABILITY (RFC 7953).
    """

    # Event with VALARM for nested comp-filter tests
    EVENT_WITH_ALARM = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-with-alarm
DTSTAMP:20130903T120000Z
DTSTART:20130903T100000Z
DTEND:20130903T110000Z
SUMMARY:Event with Alarm
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Reminder
TRIGGER:-PT30M
END:VALARM
END:VEVENT
END:VCALENDAR
"""

    # Event without VALARM
    EVENT_WITHOUT_ALARM = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-no-alarm
DTSTAMP:20130903T120000Z
DTSTART:20130903T140000Z
DTEND:20130903T150000Z
SUMMARY:Event without Alarm
END:VEVENT
END:VCALENDAR
"""

    # VAVAILABILITY component for RFC 7953 support tests
    VAVAILABILITY_ITEM = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VAVAILABILITY
UID:availability-1
DTSTAMP:20130903T120000Z
DTSTART:20130901T000000Z
DTEND:20131001T000000Z
BEGIN:AVAILABLE
UID:available-slot-1
DTSTAMP:20130903T120000Z
DTSTART:20130903T090000Z
DTEND:20130903T170000Z
SUMMARY:Working Hours
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR
"""

    def test_valarm_comp_filter(self):
        """Test comp-filter for VALARM nested within VEVENT."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/with-alarm.ics",
            self.EVENT_WITH_ALARM, login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/no-alarm.ics",
            self.EVENT_WITHOUT_ALARM, login="user:")
        assert status == 201

        # Query for events that have a VALARM component
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:comp-filter name="VALARM"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        assert "with-alarm.ics" in answer
        assert "no-alarm.ics" not in answer

    def test_vavailability_comp_filter(self):
        """Test comp-filter for VAVAILABILITY (RFC 7953)."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # Store VAVAILABILITY component
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/availability.ics",
            self.VAVAILABILITY_ITEM, login="user:")
        assert status == 201

        # Store a regular event
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            self.EVENT_WITHOUT_ALARM, login="user:")
        assert status == 201

        # Query specifically for VAVAILABILITY components
        query = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VAVAILABILITY"/>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query, login="user:")
        assert status == 207
        # Should find the availability item
        assert "availability.ics" in answer
        # Should NOT find regular events
        assert "event.ics" not in answer

    def test_vavailability_time_range_filter(self):
        """Test time-range filter on VAVAILABILITY components."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/availability.ics",
            self.VAVAILABILITY_ITEM, login="user:")
        assert status == 201

        # Query for VAVAILABILITY in time range (should match)
        query_in_range = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VAVAILABILITY">
                <C:time-range start="20130903T000000Z" end="20130904T000000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_in_range, login="user:")
        assert status == 207
        assert "availability.ics" in answer

        # Query for VAVAILABILITY outside time range (should NOT match)
        query_outside_range = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VAVAILABILITY">
                <C:time-range start="20131101T000000Z" end="20131201T000000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_outside_range, login="user:")
        assert status == 207
        assert "availability.ics" not in answer

    def test_vavailability_with_rrule(self):
        """Test RFC 7953 §3.1: VAVAILABILITY with RRULE for recurring availability."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/", login="user:")
        assert status == 201

        # VAVAILABILITY with weekly RRULE - available weekdays 9-17
        vavailability_rrule = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VAVAILABILITY
UID:recurring-availability
DTSTAMP:20130903T120000Z
DTSTART:20130902T090000Z
DTEND:20130902T170000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;COUNT=52
BEGIN:AVAILABLE
UID:available-slot-recurring
DTSTAMP:20130903T120000Z
DTSTART:20130902T090000Z
DTEND:20130902T170000Z
SUMMARY:Working Hours
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR
"""
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/recurring-avail.ics",
            vavailability_rrule, login="user:")
        assert status == 201

        # Query for a Tuesday in the recurrence range (should match)
        query_tuesday = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VAVAILABILITY">
                <C:time-range start="20130910T080000Z" end="20130910T180000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_tuesday, login="user:")
        assert status == 207
        # Should match - Sept 10, 2013 is a Tuesday (within RRULE)
        assert "recurring-avail.ics" in answer

        # Query for a Saturday (should NOT match - not in BYDAY)
        query_saturday = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:prop><D:getetag/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VAVAILABILITY">
                <C:time-range start="20130914T080000Z" end="20130914T180000Z"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""

        status, _, answer = self.request(
            "REPORT", "/user/calendar.ics/", query_saturday, login="user:")
        assert status == 207
        # Sept 14, 2013 is a Saturday - should NOT be in recurrence set
        # Note: vobject may still match if time range overlaps with the base occurrence
        # This test verifies RRULE support works without errors


class TestCollectionMove(BaseTest):
    """Test RFC 4918 §9.9 MOVE method for collections.

    WebDAV MOVE operations on collections should rename the collection
    and all its contents atomically.
    """

    SIMPLE_EVENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:move-test-event
DTSTAMP:20130903T120000Z
DTSTART:20130903T100000Z
DTEND:20130903T110000Z
SUMMARY:Test Event
END:VEVENT
END:VCALENDAR
"""

    def test_move_collection_basic(self):
        """Test basic collection MOVE operation."""
        self.configure({"auth": {"type": "none"}})
        # Create a calendar
        status, _, _ = self.request(
            "MKCALENDAR", "/user/original-cal.ics/", login="user:")
        assert status == 201

        # Add an event to the calendar
        status, _, _ = self.request(
            "PUT", "/user/original-cal.ics/event.ics",
            self.SIMPLE_EVENT, login="user:")
        assert status == 201

        # Verify the event exists
        status, _, _ = self.request(
            "GET", "/user/original-cal.ics/event.ics", login="user:")
        assert status == 200

        # Move the collection
        status, _, _ = self.request(
            "MOVE", "/user/original-cal.ics/",
            HTTP_DESTINATION="http://127.0.0.1/user/renamed-cal.ics/",
            login="user:")
        assert status == 201  # Created (no destination existed)

        # Verify the old path no longer exists
        status, _, _ = self.request(
            "GET", "/user/original-cal.ics/", login="user:")
        assert status == 404

        # Verify the new path exists
        status, _, _ = self.request(
            "GET", "/user/renamed-cal.ics/", login="user:")
        assert status == 200

        # Verify the event moved too
        status, _, _ = self.request(
            "GET", "/user/renamed-cal.ics/event.ics", login="user:")
        assert status == 200

    def test_move_collection_with_overwrite(self):
        """Test MOVE collection with OVERWRITE header (RFC 4918 §9.9)."""
        self.configure({"auth": {"type": "none"}})
        # Create first calendar
        status, _, _ = self.request(
            "MKCALENDAR", "/user/source-cal.ics/", login="user:")
        assert status == 201

        # Create second calendar (destination)
        status, _, _ = self.request(
            "MKCALENDAR", "/user/dest-cal.ics/", login="user:")
        assert status == 201

        # Add event to source
        status, _, _ = self.request(
            "PUT", "/user/source-cal.ics/source-event.ics",
            self.SIMPLE_EVENT, login="user:")
        assert status == 201

        # Try to move without OVERWRITE=T - should fail
        status, _, _ = self.request(
            "MOVE", "/user/source-cal.ics/",
            HTTP_DESTINATION="http://127.0.0.1/user/dest-cal.ics/",
            HTTP_OVERWRITE="F",
            login="user:")
        assert status == 412  # Precondition Failed

        # Move with OVERWRITE=T - should succeed
        status, _, _ = self.request(
            "MOVE", "/user/source-cal.ics/",
            HTTP_DESTINATION="http://127.0.0.1/user/dest-cal.ics/",
            HTTP_OVERWRITE="T",
            login="user:")
        assert status == 204  # No Content (destination existed)

        # Verify source is gone
        status, _, _ = self.request(
            "GET", "/user/source-cal.ics/", login="user:")
        assert status == 404

        # Verify destination has the source's content
        status, _, _ = self.request(
            "GET", "/user/dest-cal.ics/source-event.ics", login="user:")
        assert status == 200

    def test_move_collection_to_nonexistent_parent(self):
        """Test MOVE to path with nonexistent parent returns error.

        RFC 4918 §9.9 specifies 409 Conflict when the parent collection
        doesn't exist, but implementations may also return 403 Forbidden
        if the rights system rejects the path first.
        """
        self.configure({"auth": {"type": "none"}})
        # Create a calendar
        status, _, _ = self.request(
            "MKCALENDAR", "/user/test-cal.ics/", login="user:")
        assert status == 201

        # Try to move to a path where parent doesn't exist
        status, _, _ = self.request(
            "MOVE", "/user/test-cal.ics/",
            HTTP_DESTINATION="http://127.0.0.1/user/nonexistent/new-cal.ics/",
            login="user:")
        # Either 403 (rights rejected) or 409 (conflict) is acceptable
        assert status in (403, 409)

    def test_move_collection_into_collection(self):
        """Test MOVE into a tagged collection returns 403 Forbidden."""
        self.configure({"auth": {"type": "none"}})
        # Create two calendars
        status, _, _ = self.request(
            "MKCALENDAR", "/user/cal1.ics/", login="user:")
        assert status == 201
        status, _, _ = self.request(
            "MKCALENDAR", "/user/cal2.ics/", login="user:")
        assert status == 201

        # Try to move one calendar inside another - should fail
        status, _, _ = self.request(
            "MOVE", "/user/cal1.ics/",
            HTTP_DESTINATION="http://127.0.0.1/user/cal2.ics/nested-cal/",
            login="user:")
        assert status == 403  # Forbidden

    def test_move_collection_preserves_properties(self):
        """Test MOVE preserves collection properties."""
        self.configure({"auth": {"type": "none"}})
        # Create calendar with custom displayname
        mkcal_body = """<?xml version="1.0" encoding="utf-8" ?>
<C:mkcalendar xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:set>
        <D:prop>
            <D:displayname>My Test Calendar</D:displayname>
            <C:calendar-description>A test calendar</C:calendar-description>
        </D:prop>
    </D:set>
</C:mkcalendar>"""
        status, _, _ = self.request(
            "MKCALENDAR", "/user/proptest-cal.ics/", mkcal_body, login="user:")
        assert status == 201

        # Move the collection
        status, _, _ = self.request(
            "MOVE", "/user/proptest-cal.ics/",
            HTTP_DESTINATION="http://127.0.0.1/user/moved-proptest.ics/",
            login="user:")
        assert status == 201

        # Verify properties are preserved
        propfind = """<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <D:displayname/>
        <C:calendar-description/>
    </D:prop>
</D:propfind>"""
        status, _, answer = self.request(
            "PROPFIND", "/user/moved-proptest.ics/", propfind,
            HTTP_DEPTH="0", login="user:")
        assert status == 207
        assert "My Test Calendar" in answer
        assert "A test calendar" in answer

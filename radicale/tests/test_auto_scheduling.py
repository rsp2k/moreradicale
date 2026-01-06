"""
Tests for CalDAV Auto-Scheduling (RFC 6638 SCHEDULE-AGENT=SERVER).

Tests the AutoScheduler class and its integration with ITIPProcessor
for automatic resource scheduling.
"""

import os
import pytest
import tempfile
from datetime import datetime, timedelta
from radicale import Application
from radicale.tests import BaseTest


class TestAutoScheduling(BaseTest):
    """Test auto-scheduling for resource calendars."""

    def setup_method(self):
        """Set up test configuration with auto-scheduling enabled."""
        self.configuration = self.Configuration({
            "storage": {"filesystem_folder": self.colpath},
            "auth": {"type": "none"},
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com",
                "auto_accept_policy": "if-free"
            }
        })
        self.application = Application(self.configuration)

    def test_auto_accept_no_conflict(self):
        """Test resource auto-accepts when no conflicts exist."""
        # Create organizer's calendar
        self.request("MKCALENDAR", "/alice/calendar.ics/")

        # Create resource's calendar
        self.request("MKCALENDAR", "/conference-room/calendar.ics/")

        # Organizer invites resource to meeting
        event = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:test-resource-1@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Team Meeting
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:conference-room@example.com
END:VEVENT
END:VCALENDAR"""

        # PUT event as organizer
        response = self.request(
            "PUT",
            "/alice/calendar.ics/test-resource-1.ics",
            event
        )
        assert response.status == 201

        # Check resource's calendar - should have auto-accepted
        response = self.request(
            "GET",
            "/conference-room/calendar.ics/test-resource-1.ics"
        )
        assert response.status == 200
        assert b"PARTSTAT=ACCEPTED" in response.body

    def test_auto_decline_on_conflict(self):
        """Test resource auto-declines when conflicts exist."""
        # Create calendars
        self.request("MKCALENDAR", "/alice/calendar.ics/")
        self.request("MKCALENDAR", "/bob/calendar.ics/")
        self.request("MKCALENDAR", "/conference-room/calendar.ics/")

        # Alice books the room first
        event1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:existing-booking@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Alice's Meeting
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=ACCEPTED;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:conference-room@example.com
END:VEVENT
END:VCALENDAR"""

        response = self.request(
            "PUT",
            "/alice/calendar.ics/existing-booking.ics",
            event1
        )
        assert response.status == 201

        # Manually add to resource calendar (simulate successful booking)
        response = self.request(
            "PUT",
            "/conference-room/calendar.ics/existing-booking.ics",
            event1
        )
        assert response.status == 201

        # Bob tries to book at the same time
        event2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:conflicting-booking@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Bob's Meeting
ORGANIZER:mailto:bob@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:conference-room@example.com
END:VEVENT
END:VCALENDAR"""

        response = self.request(
            "PUT",
            "/bob/calendar.ics/conflicting-booking.ics",
            event2
        )
        assert response.status == 201

        # Check Bob's calendar - resource should have declined
        response = self.request(
            "GET",
            "/bob/calendar.ics/conflicting-booking.ics"
        )
        assert response.status == 200
        assert b"PARTSTAT=DECLINED" in response.body

        # Check resource calendar - should NOT have Bob's event
        response = self.request(
            "GET",
            "/conference-room/calendar.ics/conflicting-booking.ics"
        )
        assert response.status == 404

    def test_auto_accept_always_policy(self):
        """Test ALWAYS policy allows double-booking."""
        # Set policy to ALWAYS
        self.configuration = self.Configuration({
            "storage": {"filesystem_folder": self.colpath},
            "auth": {"type": "none"},
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com",
                "auto_accept_policy": "always"
            }
        })
        self.application = Application(self.configuration)

        # Create calendars
        self.request("MKCALENDAR", "/alice/calendar.ics/")
        self.request("MKCALENDAR", "/bob/calendar.ics/")
        self.request("MKCALENDAR", "/projector/calendar.ics/")

        # Alice books the projector
        event1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:alice-projector@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Alice's Presentation
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=ACCEPTED;CUTYPE=RESOURCE;SCHEDULE-AGENT=SERVER:mailto:projector@example.com
END:VEVENT
END:VCALENDAR"""

        self.request("PUT", "/alice/calendar.ics/alice-projector.ics", event1)
        self.request("PUT", "/projector/calendar.ics/alice-projector.ics", event1)

        # Bob books at same time (should succeed with ALWAYS policy)
        event2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:bob-projector@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Bob's Presentation
ORGANIZER:mailto:bob@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=RESOURCE;SCHEDULE-AGENT=SERVER:mailto:projector@example.com
END:VEVENT
END:VCALENDAR"""

        response = self.request(
            "PUT",
            "/bob/calendar.ics/bob-projector.ics",
            event2
        )
        assert response.status == 201

        # Check Bob's calendar - should be ACCEPTED (ALWAYS policy)
        response = self.request(
            "GET",
            "/bob/calendar.ics/bob-projector.ics"
        )
        assert response.status == 200
        assert b"PARTSTAT=ACCEPTED" in response.body

        # Projector should have both events (double-booked)
        response = self.request(
            "GET",
            "/projector/calendar.ics/bob-projector.ics"
        )
        assert response.status == 200

    def test_auto_tentative_on_conflict(self):
        """Test TENTATIVE_IF_CONFLICT policy."""
        # Set policy to tentative on conflict
        self.configuration = self.Configuration({
            "storage": {"filesystem_folder": self.colpath},
            "auth": {"type": "none"},
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com",
                "auto_accept_policy": "tentative-if-conflict"
            }
        })
        self.application = Application(self.configuration)

        # Create calendars
        self.request("MKCALENDAR", "/alice/calendar.ics/")
        self.request("MKCALENDAR", "/bob/calendar.ics/")
        self.request("MKCALENDAR", "/conference-room/calendar.ics/")

        # Alice books the room
        event1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:alice-meeting@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Alice's Meeting
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=ACCEPTED;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:conference-room@example.com
END:VEVENT
END:VCALENDAR"""

        self.request("PUT", "/alice/calendar.ics/alice-meeting.ics", event1)
        self.request("PUT", "/conference-room/calendar.ics/alice-meeting.ics", event1)

        # Bob books at same time (should get TENTATIVE)
        event2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:bob-meeting@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Bob's Meeting
ORGANIZER:mailto:bob@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:conference-room@example.com
END:VEVENT
END:VCALENDAR"""

        response = self.request(
            "PUT",
            "/bob/calendar.ics/bob-meeting.ics",
            event2
        )
        assert response.status == 201

        # Check Bob's calendar - should be TENTATIVE
        response = self.request(
            "GET",
            "/bob/calendar.ics/bob-meeting.ics"
        )
        assert response.status == 200
        assert b"PARTSTAT=TENTATIVE" in response.body

    def test_manual_policy_no_auto_accept(self):
        """Test MANUAL policy prevents auto-scheduling."""
        # Set policy to MANUAL
        self.configuration = self.Configuration({
            "storage": {"filesystem_folder": self.colpath},
            "auth": {"type": "none"},
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com",
                "auto_accept_policy": "manual"
            }
        })
        self.application = Application(self.configuration)

        # Create calendars
        self.request("MKCALENDAR", "/alice/calendar.ics/")
        self.request("MKCALENDAR", "/ceo-calendar/calendar.ics/")

        # Invite CEO calendar (should require manual accept)
        event = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:meeting-with-ceo@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Strategy Meeting
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:ceo-calendar@example.com
END:VEVENT
END:VCALENDAR"""

        response = self.request(
            "PUT",
            "/alice/calendar.ics/meeting-with-ceo.ics",
            event
        )
        assert response.status == 201

        # Check organizer's calendar - should remain NEEDS-ACTION
        response = self.request(
            "GET",
            "/alice/calendar.ics/meeting-with-ceo.ics"
        )
        assert response.status == 200
        assert b"PARTSTAT=NEEDS-ACTION" in response.body

        # CEO calendar should NOT have the event
        response = self.request(
            "GET",
            "/ceo-calendar/calendar.ics/meeting-with-ceo.ics"
        )
        assert response.status == 404

    def test_schedule_agent_client_skipped(self):
        """Test SCHEDULE-AGENT=CLIENT skips auto-scheduling."""
        self.request("MKCALENDAR", "/alice/calendar.ics/")
        self.request("MKCALENDAR", "/conference-room/calendar.ics/")

        # Event with SCHEDULE-AGENT=CLIENT
        event = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:client-scheduled@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Client-Scheduled Meeting
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=ROOM;SCHEDULE-AGENT=CLIENT:mailto:conference-room@example.com
END:VEVENT
END:VCALENDAR"""

        response = self.request(
            "PUT",
            "/alice/calendar.ics/client-scheduled.ics",
            event
        )
        assert response.status == 201

        # Resource should NOT auto-accept (SCHEDULE-AGENT=CLIENT)
        response = self.request(
            "GET",
            "/conference-room/calendar.ics/client-scheduled.ics"
        )
        assert response.status == 404

    def test_transparent_events_dont_conflict(self):
        """Test that TRANSP=TRANSPARENT events don't cause conflicts."""
        self.request("MKCALENDAR", "/alice/calendar.ics/")
        self.request("MKCALENDAR", "/bob/calendar.ics/")
        self.request("MKCALENDAR", "/conference-room/calendar.ics/")

        # Alice creates transparent event (doesn't block time)
        event1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:transparent-event@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Tentative Booking
TRANSP:TRANSPARENT
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=ACCEPTED;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:conference-room@example.com
END:VEVENT
END:VCALENDAR"""

        self.request("PUT", "/alice/calendar.ics/transparent-event.ics", event1)
        self.request("PUT", "/conference-room/calendar.ics/transparent-event.ics", event1)

        # Bob books at same time (should succeed - transparent doesn't block)
        event2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:real-booking@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Bob's Meeting
ORGANIZER:mailto:bob@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:conference-room@example.com
END:VEVENT
END:VCALENDAR"""

        response = self.request(
            "PUT",
            "/bob/calendar.ics/real-booking.ics",
            event2
        )
        assert response.status == 201

        # Should be ACCEPTED (transparent events don't conflict)
        response = self.request(
            "GET",
            "/bob/calendar.ics/real-booking.ics"
        )
        assert response.status == 200
        assert b"PARTSTAT=ACCEPTED" in response.body

    def test_per_resource_policy_file(self):
        """Test per-resource policies from JSON file."""
        # Create temporary policy file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("""
            {
                "vip-room@example.com": "manual",
                "projector-a@example.com": "always"
            }
            """)
            policy_file = f.name

        try:
            # Configure with policy file
            self.configuration = self.Configuration({
                "storage": {"filesystem_folder": self.colpath},
                "auth": {"type": "none"},
                "scheduling": {
                    "enabled": "True",
                    "internal_domain": "example.com",
                    "auto_accept_policy": "if-free",
                    "resource_policies_file": policy_file
                }
            })
            self.application = Application(self.configuration)

            # Create calendars
            self.request("MKCALENDAR", "/alice/calendar.ics/")
            self.request("MKCALENDAR", "/vip-room/calendar.ics/")
            self.request("MKCALENDAR", "/projector-a/calendar.ics/")

            # VIP room should use MANUAL policy
            event1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:vip-meeting@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:VIP Meeting
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:vip-room@example.com
END:VEVENT
END:VCALENDAR"""

            self.request("PUT", "/alice/calendar.ics/vip-meeting.ics", event1)

            # VIP room should NOT auto-accept
            response = self.request("GET", "/vip-room/calendar.ics/vip-meeting.ics")
            assert response.status == 404

            # Projector should use ALWAYS policy (even with conflicts)
            event2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:projector-meeting@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DTEND:20250110T150000Z
SUMMARY:Presentation
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=RESOURCE;SCHEDULE-AGENT=SERVER:mailto:projector-a@example.com
END:VEVENT
END:VCALENDAR"""

            response = self.request(
                "PUT",
                "/alice/calendar.ics/projector-meeting.ics",
                event2
            )
            assert response.status == 201

            # Projector should have auto-accepted
            response = self.request(
                "GET",
                "/projector-a/calendar.ics/projector-meeting.ics"
            )
            assert response.status == 200
            assert b"PARTSTAT=ACCEPTED" in response.body

        finally:
            # Clean up policy file
            if os.path.exists(policy_file):
                os.unlink(policy_file)

    def test_vtodo_auto_scheduling(self):
        """Test auto-scheduling works for VTODO (tasks)."""
        self.request("MKCALENDAR", "/alice/calendar.ics/")
        self.request("MKCALENDAR", "/review-queue/calendar.ics/")

        # Create task assigned to resource
        task = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VTODO
UID:review-task@example.com
DTSTAMP:20250101T120000Z
DTSTART:20250110T140000Z
DUE:20250110T170000Z
SUMMARY:Code Review Task
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CUTYPE=RESOURCE;SCHEDULE-AGENT=SERVER:mailto:review-queue@example.com
END:VTODO
END:VCALENDAR"""

        response = self.request(
            "PUT",
            "/alice/calendar.ics/review-task.ics",
            task
        )
        assert response.status == 201

        # Resource should auto-accept task
        response = self.request(
            "GET",
            "/review-queue/calendar.ics/review-task.ics"
        )
        assert response.status == 200
        assert b"PARTSTAT=ACCEPTED" in response.body


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

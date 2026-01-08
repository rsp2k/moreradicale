"""
Tests for CalDAV Auto-Scheduling (RFC 6638 SCHEDULE-AGENT=SERVER).

Tests the AutoScheduler class and its integration with ITIPProcessor
for automatic resource scheduling.
"""

import os
import tempfile

import pytest

from radicale.tests import BaseTest


class TestAutoScheduling(BaseTest):
    """Test auto-scheduling for resource calendars."""

    def setup_method(self):
        """Set up test configuration with auto-scheduling enabled."""
        super().setup_method()
        self.configure({
            "auth": {"type": "none"},
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com",
                "auto_accept_policy": "if-free"
            }
        })

    def _create_principal(self, user: str) -> None:
        """Create a principal collection for a user."""
        self.propfind(f"/{user}/", HTTP_DEPTH="1", login=f"{user}:")

    def _create_calendar(self, user: str, calendar: str = "calendar") -> None:
        """Create a calendar for a user (creates principal if needed)."""
        self._create_principal(user)
        self.request("MKCALENDAR", f"/{user}/{calendar}/",
                     CONTENT_TYPE="application/xml", login=f"{user}:")

    def test_auto_accept_no_conflict(self):
        """Test resource auto-accepts when no conflicts exist."""
        # Create organizer's calendar
        self._create_calendar("alice")

        # Create resource's calendar
        self._create_calendar("conference-room")

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
        status, _, _ = self.request(
            "PUT",
            "/alice/calendar/test-resource-1.ics",
            event,
            login="alice:"
        )
        assert status == 201

        # Check resource's calendar - should have auto-accepted
        # File is named with sanitized UID (@ replaced with -)
        status, _, content = self.request(
            "GET",
            "/conference-room/calendar/test-resource-1-example.com.ics",
            login="conference-room:"
        )
        assert status == 200
        assert "PARTSTAT=ACCEPTED" in content

    def test_auto_decline_on_conflict(self):
        """Test resource auto-declines when conflicts exist."""
        # Create calendars
        self._create_calendar("alice")
        self._create_calendar("bob")
        self._create_calendar("conference-room")

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

        status, _, _ = self.request(
            "PUT",
            "/alice/calendar/existing-booking.ics",
            event1,
            login="alice:"
        )
        assert status == 201

        # Auto-scheduler already added event to resource calendar
        # Verify it's there with ACCEPTED status
        status, _, content = self.request(
            "GET",
            "/conference-room/calendar/existing-booking-example.com.ics",
            login="conference-room:"
        )
        assert status == 200
        assert "PARTSTAT=ACCEPTED" in content

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

        status, _, _ = self.request(
            "PUT",
            "/bob/calendar/conflicting-booking.ics",
            event2,
            login="bob:"
        )
        assert status == 201

        # Check Bob's calendar - resource should have declined
        status, _, content = self.request(
            "GET",
            "/bob/calendar/conflicting-booking.ics",
            login="bob:"
        )
        assert status == 200
        assert "PARTSTAT=DECLINED" in content

        # Check resource calendar - should NOT have Bob's event
        status, _, _ = self.request(
            "GET",
            "/conference-room/calendar/conflicting-booking-example.com.ics",
            login="conference-room:"
        )
        assert status == 404

    def test_auto_accept_always_policy(self):
        """Test ALWAYS policy allows double-booking."""
        # Set policy to ALWAYS
        self.configure({
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com",
                "auto_accept_policy": "always"
            }
        })

        # Create calendars
        self._create_calendar("alice")
        self._create_calendar("bob")
        self._create_calendar("projector")

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

        self.request("PUT", "/alice/calendar/alice-projector.ics",
                     event1, login="alice:")
        # Auto-scheduler already added to projector's calendar

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

        status, _, _ = self.request(
            "PUT",
            "/bob/calendar/bob-projector.ics",
            event2,
            login="bob:"
        )
        assert status == 201

        # Check Bob's calendar - should be ACCEPTED (ALWAYS policy)
        status, _, content = self.request(
            "GET",
            "/bob/calendar/bob-projector.ics",
            login="bob:"
        )
        assert status == 200
        assert "PARTSTAT=ACCEPTED" in content

        # Projector should have both events (double-booked)
        status, _, _ = self.request(
            "GET",
            "/projector/calendar/bob-projector-example.com.ics",
            login="projector:"
        )
        assert status == 200

    def test_auto_tentative_on_conflict(self):
        """Test TENTATIVE_IF_CONFLICT policy."""
        # Set policy to tentative on conflict
        self.configure({
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com",
                "auto_accept_policy": "tentative-if-conflict"
            }
        })

        # Create calendars
        self._create_calendar("alice")
        self._create_calendar("bob")
        self._create_calendar("conference-room")

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

        self.request("PUT", "/alice/calendar/alice-meeting.ics",
                     event1, login="alice:")
        # Auto-scheduler already added to conference-room's calendar

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

        status, _, _ = self.request(
            "PUT",
            "/bob/calendar/bob-meeting.ics",
            event2,
            login="bob:"
        )
        assert status == 201

        # Check Bob's calendar - should be TENTATIVE
        status, _, content = self.request(
            "GET",
            "/bob/calendar/bob-meeting.ics",
            login="bob:"
        )
        assert status == 200
        assert "PARTSTAT=TENTATIVE" in content

    def test_manual_policy_no_auto_accept(self):
        """Test MANUAL policy prevents auto-scheduling."""
        # Set policy to MANUAL
        self.configure({
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com",
                "auto_accept_policy": "manual"
            }
        })

        # Create calendars
        self._create_calendar("alice")
        self._create_calendar("ceo-calendar")

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

        status, _, _ = self.request(
            "PUT",
            "/alice/calendar/meeting-with-ceo.ics",
            event,
            login="alice:"
        )
        assert status == 201

        # Check organizer's calendar - should remain NEEDS-ACTION
        status, _, content = self.request(
            "GET",
            "/alice/calendar/meeting-with-ceo.ics",
            login="alice:"
        )
        assert status == 200
        assert "PARTSTAT=NEEDS-ACTION" in content

        # CEO calendar should NOT have the event
        status, _, _ = self.request(
            "GET",
            "/ceo-calendar/calendar/meeting-with-ceo.ics",
            login="ceo-calendar:"
        )
        assert status == 404

    def test_schedule_agent_client_skipped(self):
        """Test SCHEDULE-AGENT=CLIENT skips auto-scheduling."""
        self._create_calendar("alice")
        self._create_calendar("conference-room")

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

        status, _, _ = self.request(
            "PUT",
            "/alice/calendar/client-scheduled.ics",
            event,
            login="alice:"
        )
        assert status == 201

        # Resource should NOT auto-accept (SCHEDULE-AGENT=CLIENT)
        status, _, _ = self.request(
            "GET",
            "/conference-room/calendar/client-scheduled.ics",
            login="conference-room:"
        )
        assert status == 404

    def test_transparent_events_dont_conflict(self):
        """Test that TRANSP=TRANSPARENT events don't cause conflicts."""
        self._create_calendar("alice")
        self._create_calendar("bob")
        self._create_calendar("conference-room")

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

        self.request("PUT", "/alice/calendar/transparent-event.ics",
                     event1, login="alice:")
        # Auto-scheduler added to conference-room's calendar

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

        status, _, _ = self.request(
            "PUT",
            "/bob/calendar/real-booking.ics",
            event2,
            login="bob:"
        )
        assert status == 201

        # Should be ACCEPTED (transparent events don't conflict)
        status, _, content = self.request(
            "GET",
            "/bob/calendar/real-booking.ics",
            login="bob:"
        )
        assert status == 200
        assert "PARTSTAT=ACCEPTED" in content

    def test_per_resource_policy_file(self):
        """Test per-resource policies from JSON file."""
        # Create temporary policy file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                         delete=False) as f:
            f.write("""
            {
                "vip-room@example.com": "manual",
                "projector-a@example.com": "always"
            }
            """)
            policy_file = f.name

        try:
            # Configure with policy file
            self.configure({
                "scheduling": {
                    "enabled": "True",
                    "internal_domain": "example.com",
                    "auto_accept_policy": "if-free",
                    "resource_policies_file": policy_file
                }
            })

            # Create calendars
            self._create_calendar("alice")
            self._create_calendar("vip-room")
            self._create_calendar("projector-a")

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

            self.request("PUT", "/alice/calendar/vip-meeting.ics",
                         event1, login="alice:")

            # VIP room should NOT auto-accept (MANUAL policy)
            status, _, _ = self.request(
                "GET",
                "/vip-room/calendar/vip-meeting-example.com.ics",
                login="vip-room:"
            )
            assert status == 404

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

            status, _, _ = self.request(
                "PUT",
                "/alice/calendar/projector-meeting.ics",
                event2,
                login="alice:"
            )
            assert status == 201

            # Projector should have auto-accepted
            status, _, content = self.request(
                "GET",
                "/projector-a/calendar/projector-meeting-example.com.ics",
                login="projector-a:"
            )
            assert status == 200
            assert "PARTSTAT=ACCEPTED" in content

        finally:
            # Clean up policy file
            if os.path.exists(policy_file):
                os.unlink(policy_file)

    def test_vtodo_auto_scheduling(self):
        """Test auto-scheduling works for VTODO (tasks)."""
        self._create_calendar("alice")
        self._create_calendar("review-queue")

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

        status, _, _ = self.request(
            "PUT",
            "/alice/calendar/review-task.ics",
            task,
            login="alice:"
        )
        assert status == 201

        # Resource should auto-accept task
        status, _, content = self.request(
            "GET",
            "/review-queue/calendar/review-task-example.com.ics",
            login="review-queue:"
        )
        assert status == 200
        assert "PARTSTAT=ACCEPTED" in content


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025-2025 RFC 6638 Scheduling Implementation
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
Tests for RFC 6638 CalDAV Scheduling support.

Tests cover:
- Infrastructure (collection auto-creation, properties)
- iTIP message parsing and validation
- Attendee routing
- Internal scheduling workflow
"""

import xml.etree.ElementTree as ET

import pytest
import vobject

from radicale import xmlutils
from radicale.itip import models, router, validator
from radicale.tests import BaseTest


class TestSchedulingInfrastructure(BaseTest):
    """Test scheduling infrastructure (collections, properties)."""

    def test_inbox_outbox_autocreate(self):
        """Test automatic creation of schedule-inbox and schedule-outbox."""
        # Enable scheduling
        self.configure({"auth": {"type": "none"}})
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # Create principal and discover with depth=1
        status, _, answer = self.request(
            "PROPFIND", "/alice/",
            """<?xml version="1.0" encoding="utf-8"?>
            <propfind xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
                <prop>
                    <resourcetype/>
                </prop>
            </propfind>""",
            HTTP_DEPTH="1", login="alice:")

        assert status == 207

        # Parse response and check for inbox/outbox
        responses = self.parse_responses(answer)
        paths = list(responses.keys())
        print(f"DEBUG: Paths returned: {paths}")

        assert any("schedule-inbox" in path for path in paths), \
            f"schedule-inbox not auto-created. Paths: {paths}"
        assert any("schedule-outbox" in path for path in paths), \
            "schedule-outbox not auto-created"

    def test_scheduling_properties_on_principal(self):
        """Test PROPFIND returns scheduling URLs on principal."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # PROPFIND for scheduling properties (auto-creates principal)
        status, _, answer = self.request(
            "PROPFIND", "/alice/",
            """<?xml version="1.0" encoding="utf-8"?>
            <propfind xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
                <prop>
                    <C:schedule-inbox-URL/>
                    <C:schedule-outbox-URL/>
                    <C:calendar-user-type/>
                </prop>
            </propfind>""",
            login="alice:")

        assert status == 207

        # Parse and verify properties
        responses = self.parse_responses(answer)
        props = responses["/alice/"]

        # Check schedule-inbox-URL
        inbox_status, inbox_elem = props["C:schedule-inbox-URL"]
        assert inbox_status == 200
        href = inbox_elem.find(xmlutils.make_clark("D:href"))
        assert href is not None
        assert "schedule-inbox" in href.text

        # Check schedule-outbox-URL
        outbox_status, outbox_elem = props["C:schedule-outbox-URL"]
        assert outbox_status == 200
        href = outbox_elem.find(xmlutils.make_clark("D:href"))
        assert href is not None
        assert "schedule-outbox" in href.text

        # Check calendar-user-type
        usertype_status, usertype_elem = props["C:calendar-user-type"]
        assert usertype_status == 200
        assert usertype_elem.text == "INDIVIDUAL"

    def test_scheduling_disabled_by_default(self):
        """Test scheduling is disabled by default."""
        # Don't enable scheduling in config
        self.configure({"auth": {"type": "none"}})

        # Create principal (auto-created on propfind)
        self.propfind("/alice/", login="alice:")

        # Try to POST to schedule-outbox (should fail)
        status, _, _ = self.request(
            "POST", "/alice/schedule-outbox/",
            """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:test-event
END:VEVENT
END:VCALENDAR""",
            login="alice:")

        # Should return METHOD_NOT_ALLOWED (405)
        assert status == 405


class TestITIPParsing(BaseTest):
    """Test iTIP message parsing and validation."""

    def test_parse_valid_request(self):
        """Test parsing valid REQUEST message."""
        ical_text = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
METHOD:REQUEST
BEGIN:VEVENT
UID:meeting-123
DTSTAMP:20250101T120000Z
DTSTART:20250115T140000Z
DTEND:20250115T150000Z
SUMMARY:Team Meeting
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CN=Bob:mailto:bob@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;CN=Charlie:mailto:charlie@example.com
END:VEVENT
END:VCALENDAR"""

        vcal = vobject.readOne(ical_text)
        itip_msg = validator.parse_itip_message(vcal)

        assert itip_msg.method == models.ITIPMethod.REQUEST
        assert itip_msg.uid == "meeting-123"
        assert itip_msg.organizer == "alice@example.com"
        assert len(itip_msg.attendees) == 2
        assert itip_msg.attendees[0].email == "bob@example.com"
        assert itip_msg.attendees[0].cn == "Bob"
        assert itip_msg.attendees[0].partstat == models.AttendeePartStat.NEEDS_ACTION

    def test_validate_missing_method(self):
        """Test validation fails for missing METHOD."""
        ical_text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-event
DTSTAMP:20250101T120000Z
END:VEVENT
END:VCALENDAR"""

        vcal = vobject.readOne(ical_text)

        with pytest.raises(validator.ITIPValidationError, match="missing METHOD"):
            validator.validate_itip_message(vcal)

    def test_validate_missing_uid(self):
        """Test validation fails for missing UID."""
        ical_text = """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
DTSTAMP:20250101T120000Z
ORGANIZER:mailto:alice@example.com
ATTENDEE:mailto:bob@example.com
END:VEVENT
END:VCALENDAR"""

        vcal = vobject.readOne(ical_text)

        with pytest.raises(validator.ITIPValidationError, match="Missing required UID"):
            validator.validate_itip_message(vcal)

    def test_validate_request_needs_attendee(self):
        """Test REQUEST validation requires ATTENDEE."""
        ical_text = """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:meeting-123
DTSTAMP:20250101T120000Z
ORGANIZER:mailto:alice@example.com
END:VEVENT
END:VCALENDAR"""

        vcal = vobject.readOne(ical_text)

        with pytest.raises(validator.ITIPValidationError,
                          match="REQUEST requires at least one ATTENDEE"):
            validator.validate_itip_message(vcal)


class TestAttendeeRouting(BaseTest):
    """Test attendee routing logic."""

    def test_route_internal_attendee(self):
        """Test routing internal attendee (same domain, principal exists)."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # Create principal for bob (auto-created on propfind)
        self.propfind("/bob/", login="bob:")

        # Route attendee
        is_internal, principal_path = router.route_attendee(
            "bob@example.com",
            self.application._storage,
            self.configuration
        )

        assert is_internal is True
        assert principal_path == "/bob/"

    def test_route_external_attendee_wrong_domain(self):
        """Test routing external attendee (different domain)."""
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # Route attendee from different domain
        is_internal, principal_path = router.route_attendee(
            "alice@external.org",
            self.application._storage,
            self.configuration
        )

        assert is_internal is False
        assert principal_path is None

    def test_route_internal_nonexistent_user(self):
        """Test routing internal domain but user doesn't exist."""
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # Don't create principal for nonexistent user

        # Route attendee
        is_internal, principal_path = router.route_attendee(
            "nonexistent@example.com",
            self.application._storage,
            self.configuration
        )

        assert is_internal is False  # No principal found
        assert principal_path is None

    def test_validate_organizer_permission(self):
        """Test organizer permission validation."""
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # Valid: user matches organizer
        assert router.validate_organizer_permission(
            "alice@example.com", "alice", self.configuration) is True

        # Invalid: user doesn't match organizer (spoofing attempt)
        assert router.validate_organizer_permission(
            "bob@example.com", "alice", self.configuration) is False


class TestSchedulingWorkflow(BaseTest):
    """Test end-to-end scheduling workflows."""

    def test_post_to_outbox_delivers_to_inbox(self):
        """Test POST to schedule-outbox delivers to internal attendees."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com",
                                       "mode": "internal",
                                       "max_attendees": "100"}})

        # Create principals (auto-created on propfind with depth=1 to trigger scheduling collections)
        self.propfind("/alice/", HTTP_DEPTH="1", login="alice:")
        self.propfind("/bob/", HTTP_DEPTH="1", login="bob:")

        # Alice sends invitation to Bob
        itip_request = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
METHOD:REQUEST
BEGIN:VEVENT
UID:meeting-456
DTSTAMP:20250101T120000Z
DTSTART:20250115T140000Z
DTEND:20250115T150000Z
SUMMARY:Project Review
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@example.com
END:VEVENT
END:VCALENDAR"""

        status, headers, answer = self.request(
            "POST", "/alice/schedule-outbox/",
            itip_request,
            CONTENT_TYPE="text/calendar",
            login="alice:")

        # Should return success
        assert status == 200
        assert "application/xml" in headers.get("Content-Type", "")

        # Parse schedule-response
        xml = ET.fromstring(answer)
        assert xml.tag == xmlutils.make_clark("C:schedule-response")

        # Check recipient status
        responses = xml.findall(xmlutils.make_clark("C:response"))
        assert len(responses) == 1

        recipient = responses[0].find(xmlutils.make_clark("C:recipient"))
        href = recipient.find(xmlutils.make_clark("D:href"))
        assert href.text == "mailto:bob@example.com"

        req_status = responses[0].find(xmlutils.make_clark("C:request-status"))
        assert req_status.text == "2.0;Success"

        # Verify Bob received it in his inbox
        status, _, inbox_answer = self.request(
            "PROPFIND", "/bob/schedule-inbox/",
            """<?xml version="1.0" encoding="utf-8"?>
            <propfind xmlns="DAV:">
                <prop>
                    <displayname/>
                </prop>
            </propfind>""",
            HTTP_DEPTH="1", login="bob:")

        assert status == 207

        # Check that inbox contains the message
        responses_inbox = self.parse_responses(inbox_answer)
        # Should have at least the collection itself + 1 message
        assert len(responses_inbox) >= 2

    def test_post_unauthorized_organizer(self):
        """Test POST rejected when user is not the organizer."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # Create principals (auto-created on propfind with depth=1 to trigger scheduling collections)
        self.propfind("/alice/", HTTP_DEPTH="1", login="alice:")
        self.propfind("/bob/", HTTP_DEPTH="1", login="bob:")

        # Alice tries to send invitation AS Bob (spoofing)
        itip_request = """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:fake-meeting
DTSTAMP:20250101T120000Z
ORGANIZER:mailto:bob@example.com
ATTENDEE:mailto:alice@example.com
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "POST", "/alice/schedule-outbox/",
            itip_request,
            CONTENT_TYPE="text/calendar",
            login="alice:")

        # Should return FORBIDDEN (403)
        assert status == 403

    def test_post_exceeds_max_attendees(self):
        """Test POST rejected when exceeding max_attendees limit."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com",
                                       "max_attendees": "2"}})

        self.propfind("/alice/", HTTP_DEPTH="1", login="alice:")

        # Alice tries to invite 3 attendees (exceeds limit of 2)
        itip_request = """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:big-meeting
DTSTAMP:20250101T120000Z
ORGANIZER:mailto:alice@example.com
ATTENDEE:mailto:bob@example.com
ATTENDEE:mailto:charlie@example.com
ATTENDEE:mailto:david@example.com
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "POST", "/alice/schedule-outbox/",
            itip_request,
            CONTENT_TYPE="text/calendar",
            login="alice:")

        # Should return FORBIDDEN (403)
        assert status == 403

    def test_post_to_wrong_users_outbox(self):
        """Test POST rejected when posting to another user's outbox."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # Create principals (auto-created on propfind with depth=1 to trigger scheduling collections)
        self.propfind("/alice/", HTTP_DEPTH="1", login="alice:")
        self.propfind("/bob/", HTTP_DEPTH="1", login="bob:")

        # Alice tries to POST to Bob's outbox
        itip_request = """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:test-event
DTSTAMP:20250101T120000Z
ORGANIZER:mailto:alice@example.com
ATTENDEE:mailto:bob@example.com
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "POST", "/bob/schedule-outbox/",
            itip_request,
            CONTENT_TYPE="text/calendar",
            login="alice:")

        # Should return FORBIDDEN (403)
        assert status == 403

    def test_mixed_internal_external_attendees(self):
        """Test invitation with both internal and external attendees."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"scheduling": {"enabled": "True",
                                       "internal_domain": "example.com"}})

        # Create principals (auto-created on propfind with depth=1 to trigger scheduling collections)
        self.propfind("/alice/", HTTP_DEPTH="1", login="alice:")
        self.propfind("/bob/", HTTP_DEPTH="1", login="bob:")

        # Alice invites Bob (internal) and someone external
        itip_request = """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:mixed-meeting
DTSTAMP:20250101T120000Z
DTSTART:20250115T140000Z
DTEND:20250115T150000Z
ORGANIZER:mailto:alice@example.com
ATTENDEE:mailto:bob@example.com
ATTENDEE:mailto:external@other.org
END:VEVENT
END:VCALENDAR"""

        status, _, answer = self.request(
            "POST", "/alice/schedule-outbox/",
            itip_request,
            CONTENT_TYPE="text/calendar",
            login="alice:")

        assert status == 200

        # Parse schedule-response
        xml = ET.fromstring(answer)
        responses = xml.findall(xmlutils.make_clark("C:response"))
        assert len(responses) == 2

        # Find Bob's response (internal - should succeed)
        bob_response = None
        external_response = None

        for resp in responses:
            recipient = resp.find(xmlutils.make_clark("C:recipient"))
            href = recipient.find(xmlutils.make_clark("D:href"))

            if "bob@example.com" in href.text:
                bob_response = resp
            elif "external@other.org" in href.text:
                external_response = resp

        assert bob_response is not None
        bob_status = bob_response.find(xmlutils.make_clark("C:request-status"))
        assert "2.0;Success" in bob_status.text

        assert external_response is not None
        ext_status = external_response.find(xmlutils.make_clark("C:request-status"))
        # External should get NoAuthorization (not implemented yet)
        assert "2.8" in ext_status.text or "NoAuthorization" in ext_status.text

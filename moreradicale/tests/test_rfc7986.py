# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025 Ryan Malloy and contributors
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
Tests for RFC 7986 - New Properties for iCalendar.

Tests cover:
- COLOR property for events and calendars
- CONFERENCE property with FEATURE and LABEL parameters
- IMAGE property with DISPLAY parameter
- Round-trip preservation of all properties and parameters
"""

import vobject

from moreradicale.tests import BaseTest


class TestRFC7986Properties(BaseTest):
    """Test RFC 7986 iCalendar property support."""

    def test_color_property_stored_and_retrieved(self):
        """Test COLOR property is preserved in events."""
        self.configure({"auth": {"type": "none"}})

        # Create calendar
        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        # Create event with COLOR property
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:color-test-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Colorful Meeting
COLOR:dodgerblue
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/color-event.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Retrieve and verify COLOR is preserved
        status, _, content = self.request(
            "GET", "/alice/calendar/color-event.ics", login="alice:")
        assert status == 200
        assert "COLOR:dodgerblue" in content

        # Parse and verify via vobject
        vcal = vobject.readOne(content)
        assert hasattr(vcal.vevent, 'color')
        assert vcal.vevent.color.value == 'dodgerblue'

    def test_conference_property_single(self):
        """Test single CONFERENCE property with parameters."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        # Create event with CONFERENCE property
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:conf-test-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T140000Z
DTEND:20251230T150000Z
SUMMARY:Video Call
CONFERENCE;VALUE=URI;FEATURE=VIDEO,AUDIO;LABEL=Join Zoom:https://zoom.us/j/123
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/conf-event.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Retrieve and verify
        status, _, content = self.request(
            "GET", "/alice/calendar/conf-event.ics", login="alice:")
        assert status == 200
        assert "CONFERENCE" in content

        # Parse and verify parameters (vobject handles line unfolding)
        vcal = vobject.readOne(content)
        conf = vcal.vevent.conference
        assert "zoom.us" in conf.value
        assert "VIDEO" in conf.params.get("FEATURE", [])
        assert "AUDIO" in conf.params.get("FEATURE", [])

    def test_conference_property_multiple(self):
        """Test multiple CONFERENCE properties (video + phone dial-in)."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        # Event with multiple conference options (shorter URLs to avoid folding)
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:multi-conf-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T140000Z
DTEND:20251230T150000Z
SUMMARY:Hybrid Meeting
CONFERENCE;VALUE=URI;FEATURE=VIDEO,AUDIO:https://meet.example.com/abc
CONFERENCE;VALUE=URI;FEATURE=PHONE:tel:+1-555-1234
CONFERENCE;VALUE=URI;FEATURE=CHAT:xmpp:room@chat.example.com
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/multi-conf.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Retrieve and verify
        status, _, content = self.request(
            "GET", "/alice/calendar/multi-conf.ics", login="alice:")
        assert status == 200

        # Parse and count (vobject handles line unfolding)
        vcal = vobject.readOne(content)
        conferences = vcal.vevent.contents.get('conference', [])
        assert len(conferences) == 3

        # Verify FEATURE params
        features = []
        for conf in conferences:
            features.extend(conf.params.get('FEATURE', []))
        assert 'VIDEO' in features
        assert 'PHONE' in features
        assert 'CHAT' in features

    def test_image_property_uri(self):
        """Test IMAGE property with URI value."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        # Shorter URL to avoid line folding
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:image-test-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T180000Z
DTEND:20251230T210000Z
SUMMARY:Company Party
IMAGE;VALUE=URI;DISPLAY=BADGE;FMTTYPE=image/png:https://x.co/logo.png
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/image-event.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Retrieve and verify
        status, _, content = self.request(
            "GET", "/alice/calendar/image-event.ics", login="alice:")
        assert status == 200
        assert "IMAGE" in content

        # Parse and verify (vobject handles line unfolding)
        vcal = vobject.readOne(content)
        img = vcal.vevent.image
        assert "logo.png" in img.value
        assert img.params.get("DISPLAY", [None])[0] == "BADGE"
        assert img.params.get("FMTTYPE", [None])[0] == "image/png"

    def test_image_property_multiple(self):
        """Test multiple IMAGE properties with different DISPLAY values."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:multi-image-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251231T200000Z
DTEND:20260101T010000Z
SUMMARY:New Year Gala
IMAGE;VALUE=URI;DISPLAY=BADGE:https://example.com/badge.png
IMAGE;VALUE=URI;DISPLAY=THUMBNAIL:https://example.com/thumb.jpg
IMAGE;VALUE=URI;DISPLAY=FULLSIZE:https://example.com/full.jpg
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/multi-image.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/multi-image.ics", login="alice:")
        assert status == 200

        # Parse and verify all three images
        vcal = vobject.readOne(content)
        images = vcal.vevent.contents.get('image', [])
        assert len(images) == 3

        displays = [img.params.get('DISPLAY', [None])[0] for img in images]
        assert 'BADGE' in displays
        assert 'THUMBNAIL' in displays
        assert 'FULLSIZE' in displays

    def test_all_rfc7986_properties_combined(self):
        """Test event with COLOR, CONFERENCE, and IMAGE all together."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        # Complete RFC 7986 event (shorter URLs to avoid folding)
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:full-7986-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T090000Z
DTEND:20251230T100000Z
SUMMARY:All-Hands Meeting
DESCRIPTION:Quarterly company update
COLOR:coral
CONFERENCE;VALUE=URI;FEATURE=VIDEO,AUDIO:https://zoom.us/j/999
CONFERENCE;VALUE=URI;FEATURE=PHONE:tel:+1-800-555-0199
IMAGE;VALUE=URI;DISPLAY=BADGE:https://co.io/logo.svg
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@example.com
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/full-event.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/full-event.ics", login="alice:")
        assert status == 200

        # Verify properties present (raw string check)
        assert "COLOR:coral" in content
        assert "CONFERENCE" in content
        assert "IMAGE" in content

        # Parse and verify structure (vobject handles unfolding)
        vcal = vobject.readOne(content)
        vevent = vcal.vevent

        assert vevent.color.value == 'coral'
        assert len(vevent.contents.get('conference', [])) == 2
        assert len(vevent.contents.get('image', [])) == 1
        assert "zoom.us" in vevent.contents.get('conference', [])[0].value

    def test_color_css3_names(self):
        """Test various CSS3 color names are accepted."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        # Test various CSS3 color names
        colors = ['red', 'blue', 'green', 'coral', 'dodgerblue',
                  'forestgreen', 'mediumpurple', 'tomato']

        for i, color in enumerate(colors):
            event_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:color-{color}-{i}@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Event with {color}
COLOR:{color}
END:VEVENT
END:VCALENDAR"""

            status, _, _ = self.request(
                "PUT", f"/alice/calendar/color-{color}.ics",
                event_ics, CONTENT_TYPE="text/calendar", login="alice:")
            assert status == 201, f"Failed to store event with COLOR:{color}"

            status, _, content = self.request(
                "GET", f"/alice/calendar/color-{color}.ics", login="alice:")
            assert f"COLOR:{color}" in content, f"COLOR:{color} not preserved"


class TestRFC7986ConferenceFeatures(BaseTest):
    """Test CONFERENCE property FEATURE parameter values."""

    def test_conference_feature_moderator(self):
        """Test MODERATOR feature for conference hosts."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:moderator-test@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T140000Z
DTEND:20251230T150000Z
SUMMARY:Moderated Webinar
CONFERENCE;VALUE=URI;FEATURE=VIDEO,MODERATOR;LABEL=Host Link:https://webinar.example.com/host/abc123
CONFERENCE;VALUE=URI;FEATURE=VIDEO;LABEL=Attendee Link:https://webinar.example.com/join/abc123
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/moderator.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/moderator.ics", login="alice:")

        vcal = vobject.readOne(content)
        conferences = vcal.vevent.contents.get('conference', [])

        # Find the moderator one
        moderator_conf = None
        for conf in conferences:
            if 'MODERATOR' in conf.params.get('FEATURE', []):
                moderator_conf = conf
                break

        assert moderator_conf is not None
        assert 'host' in moderator_conf.value

    def test_conference_feature_screen_share(self):
        """Test SCREEN feature for screen sharing capability."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:screen-test@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T140000Z
DTEND:20251230T150000Z
SUMMARY:Screen Share Session
CONFERENCE;VALUE=URI;FEATURE=VIDEO,SCREEN;LABEL=Join with Screen Share:https://screenshare.example.com/room/xyz
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/screen.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/screen.ics", login="alice:")

        assert "SCREEN" in content
        vcal = vobject.readOne(content)
        conf = vcal.vevent.conference
        assert 'SCREEN' in conf.params.get('FEATURE', [])

    def test_conference_feature_feed(self):
        """Test FEED feature for live streaming."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:feed-test@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T190000Z
DTEND:20251230T210000Z
SUMMARY:Live Stream Event
CONFERENCE;VALUE=URI;FEATURE=FEED;LABEL=Watch Live:https://youtube.com/live/abc123
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/feed.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/feed.ics", login="alice:")

        assert "FEED" in content
        assert "youtube.com" in content


class TestRFC7986ImageDisplay(BaseTest):
    """Test IMAGE property DISPLAY parameter values."""

    def test_image_display_graphic(self):
        """Test GRAPHIC display type for larger images."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:graphic-test@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T120000Z
SUMMARY:Event with Graphic
IMAGE;VALUE=URI;DISPLAY=GRAPHIC:https://example.com/event-banner.jpg
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/graphic.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/graphic.ics", login="alice:")

        assert "DISPLAY=GRAPHIC" in content

    def test_image_with_altrep(self):
        """Test IMAGE with ALTREP parameter for alternative representation."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:altrep-test@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T120000Z
SUMMARY:Event with Alt Image
IMAGE;VALUE=URI;ALTREP="https://example.com/hi-res.png":https://example.com/lo-res.png
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/altrep.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/altrep.ics", login="alice:")

        # Both URLs should be preserved
        assert "lo-res.png" in content
        assert "hi-res.png" in content


class TestRFC7986VTODO(BaseTest):
    """Test RFC 7986 properties on VTODO components."""

    def test_vtodo_with_conference(self):
        """Test CONFERENCE property on tasks."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/tasks/", login="alice:")

        # Shorter URL to avoid line folding
        task_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VTODO
UID:task-conf-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T090000Z
DUE:20251230T170000Z
SUMMARY:Review meeting
PRIORITY:1
STATUS:NEEDS-ACTION
COLOR:orange
CONFERENCE;VALUE=URI;FEATURE=VIDEO:https://teams.ms/abc
END:VTODO
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/tasks/review-task.ics",
            task_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/tasks/review-task.ics", login="alice:")
        assert status == 200

        assert "COLOR:orange" in content
        assert "CONFERENCE" in content

        # Parse and verify (vobject handles line unfolding)
        vcal = vobject.readOne(content)
        vtodo = vcal.vtodo
        assert vtodo.color.value == 'orange'
        assert hasattr(vtodo, 'conference')
        assert "teams.ms" in vtodo.conference.value


class TestRFC7986Integration(BaseTest):
    """Integration tests for RFC 7986 with scheduling."""

    def test_scheduled_event_with_conference(self):
        """Test that CONFERENCE is preserved in scheduled events."""
        self.configure({"auth": {"type": "none"}})
        self.configure({
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com"
            }
        })

        # Create principals
        self.propfind("/alice/", HTTP_DEPTH="1", login="alice:")
        self.propfind("/bob/", HTTP_DEPTH="1", login="bob:")

        # Create alice's calendar
        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        # Create scheduled event with CONFERENCE (shorter URL)
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:scheduled-conf-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T150000Z
DTEND:20251230T160000Z
SUMMARY:Team Sync
COLOR:teal
CONFERENCE;VALUE=URI;FEATURE=VIDEO,AUDIO:https://meet.g.co/abc
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@example.com
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/team-sync.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Verify organizer's copy has CONFERENCE
        status, _, content = self.request(
            "GET", "/alice/calendar/team-sync.ics", login="alice:")
        assert "CONFERENCE" in content
        assert "COLOR:teal" in content

        # Parse and verify (vobject handles unfolding)
        vcal = vobject.readOne(content)
        assert hasattr(vcal.vevent, 'conference')
        assert "meet.g.co" in vcal.vevent.conference.value

    def test_conference_in_invitation(self):
        """Test that CONFERENCE is included in meeting invitations."""
        self.configure({"auth": {"type": "none"}})
        self.configure({
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com"
            }
        })

        self.propfind("/alice/", HTTP_DEPTH="1", login="alice:")
        self.propfind("/bob/", HTTP_DEPTH="1", login="bob:")

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")

        # Create meeting with conference link
        event_uid = "invite-conf-test-001"
        event_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:{event_uid}@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Meeting with Video Link
CONFERENCE;VALUE=URI;FEATURE=VIDEO;LABEL=Join Call:https://zoom.us/j/987654321
ORGANIZER:mailto:alice@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@example.com
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", f"/alice/calendar/{event_uid}.ics",
            event_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Check Bob's inbox for invitation
        status, _, answer = self.request(
            "PROPFIND", "/bob/schedule-inbox/",
            """<?xml version="1.0" encoding="utf-8"?>
            <propfind xmlns="DAV:">
                <prop><resourcetype/></prop>
            </propfind>""",
            HTTP_DEPTH="1", login="bob:")

        # Invitation should be in inbox
        assert status == 207

        # Parse to find invitation items
        responses = self.parse_responses(answer)
        inbox_items = [p for p in responses.keys()
                       if p.startswith("/bob/schedule-inbox/")
                       and p != "/bob/schedule-inbox/"
                       and not p.endswith("/")]

        # If there's an invitation, verify it has the CONFERENCE
        if inbox_items:
            status, _, invite_content = self.request(
                "GET", inbox_items[0], login="bob:")
            if status == 200:
                assert "CONFERENCE" in invite_content or "zoom.us" in invite_content


class TestRFC7986CalendarLevel(BaseTest):
    """Test RFC 7986 calendar-level properties (on VCALENDAR component)."""

    def test_calendar_name_property(self):
        """Test NAME property on VCALENDAR is preserved."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/work-calendar/", login="alice:")
        assert status == 201

        # Create calendar with NAME property
        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:My Work Calendar
BEGIN:VEVENT
UID:name-test-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Work Meeting
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/work-calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Retrieve and verify NAME is preserved
        status, _, content = self.request(
            "GET", "/alice/work-calendar/event.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)
        # Note: vcal.name returns component type ("VCALENDAR")
        # Access RFC 7986 NAME property via contents dict
        assert 'name' in vcal.contents
        assert vcal.contents['name'][0].value == 'My Work Calendar'

    def test_calendar_description_property(self):
        """Test DESCRIPTION property on VCALENDAR is preserved."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Team Calendar
DESCRIPTION:Shared calendar for team meetings and events
BEGIN:VEVENT
UID:desc-test-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Team Standup
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/event.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)
        assert hasattr(vcal, 'description')
        assert 'team meetings' in vcal.description.value.lower()

    def test_calendar_color_property(self):
        """Test COLOR property on VCALENDAR (not just VEVENT)."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Blue Calendar
COLOR:steelblue
BEGIN:VEVENT
UID:cal-color-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Blue Event
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/event.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)
        assert hasattr(vcal, 'color')
        assert vcal.color.value == 'steelblue'

    def test_refresh_interval_property(self):
        """Test REFRESH-INTERVAL property with VALUE=DURATION."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        # P1D = poll once per day
        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Subscribed Calendar
REFRESH-INTERVAL;VALUE=DURATION:P1D
BEGIN:VEVENT
UID:refresh-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Daily Sync
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/event.ics", login="alice:")
        assert status == 200

        # Verify the property is preserved
        vcal = vobject.readOne(content)
        assert hasattr(vcal, 'refresh_interval')
        # Check VALUE param is preserved
        params = getattr(vcal.refresh_interval, 'params', {})
        assert 'VALUE' in params
        assert 'DURATION' in params['VALUE']

    def test_source_property(self):
        """Test SOURCE property with VALUE=URI."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:External Calendar
SOURCE;VALUE=URI:https://example.com/cal.ics
BEGIN:VEVENT
UID:source-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Sourced Event
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/event.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)
        assert hasattr(vcal, 'source')
        assert 'example.com' in vcal.source.value

    def test_calendar_url_property(self):
        """Test URL property on VCALENDAR."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Project Calendar
URL:https://example.com/project
BEGIN:VEVENT
UID:url-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Project Review
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/event.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)
        assert hasattr(vcal, 'url')
        assert 'project' in vcal.url.value

    def test_calendar_last_modified_property(self):
        """Test LAST-MODIFIED property on VCALENDAR."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Tracked Calendar
LAST-MODIFIED:20251228T150000Z
BEGIN:VEVENT
UID:lastmod-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Tracked Event
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/event.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)
        assert hasattr(vcal, 'last_modified')

    def test_calendar_image_property(self):
        """Test IMAGE property on VCALENDAR level."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Branded Calendar
IMAGE;VALUE=URI;DISPLAY=BADGE:https://ex.co/logo.png
BEGIN:VEVENT
UID:calimg-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Branded Event
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/event.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)
        assert hasattr(vcal, 'image')
        params = getattr(vcal.image, 'params', {})
        assert 'DISPLAY' in params
        assert 'BADGE' in params['DISPLAY']

    def test_all_calendar_level_properties_combined(self):
        """Test multiple RFC 7986 calendar-level properties together."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/calendar/", login="alice:")
        assert status == 201

        calendar_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Complete Calendar
DESCRIPTION:A fully featured calendar with all RFC 7986 properties
COLOR:teal
REFRESH-INTERVAL;VALUE=DURATION:PT12H
SOURCE;VALUE=URI:https://example.com/complete.ics
URL:https://example.com/calendar-page
LAST-MODIFIED:20251229T090000Z
IMAGE;VALUE=URI;DISPLAY=THUMBNAIL:https://ex.co/thumb.png
BEGIN:VEVENT
UID:complete-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T100000Z
DTEND:20251230T110000Z
SUMMARY:Complete Event
COLOR:coral
CONFERENCE;VALUE=URI;FEATURE=VIDEO:https://meet.ex.co/123
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/calendar/event.ics",
            calendar_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/calendar/event.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)

        # Verify calendar-level properties
        # Note: vcal.name returns "VCALENDAR", use contents for NAME property
        assert 'name' in vcal.contents
        assert vcal.contents['name'][0].value == 'Complete Calendar'
        assert hasattr(vcal, 'description')
        assert 'RFC 7986' in vcal.description.value
        assert hasattr(vcal, 'color')
        assert vcal.color.value == 'teal'
        assert hasattr(vcal, 'refresh_interval')
        assert hasattr(vcal, 'source')
        assert hasattr(vcal, 'url')
        assert hasattr(vcal, 'last_modified')
        assert hasattr(vcal, 'image')

        # Verify event-level properties still work
        assert hasattr(vcal.vevent, 'color')
        assert vcal.vevent.color.value == 'coral'
        assert hasattr(vcal.vevent, 'conference')


class TestRFC7986CalendarIntegration(BaseTest):
    """Integration tests for RFC 7986 calendar-level properties with CalDAV."""

    def test_calendar_properties_with_multiple_events(self):
        """Verify calendar-level properties persist across multiple events."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/shared-calendar/", login="alice:")
        assert status == 201

        # First event with calendar properties
        event1_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Shared Team Calendar
COLOR:mediumseagreen
REFRESH-INTERVAL;VALUE=DURATION:PT6H
BEGIN:VEVENT
UID:multi-001@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T090000Z
DTEND:20251230T100000Z
SUMMARY:Morning Meeting
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/shared-calendar/event1.ics",
            event1_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Second event in same calendar
        event2_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
NAME:Shared Team Calendar
COLOR:mediumseagreen
BEGIN:VEVENT
UID:multi-002@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T140000Z
DTEND:20251230T150000Z
SUMMARY:Afternoon Meeting
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/shared-calendar/event2.ics",
            event2_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        # Retrieve both and verify calendar properties
        for path in ["/alice/shared-calendar/event1.ics",
                     "/alice/shared-calendar/event2.ics"]:
            status, _, content = self.request("GET", path, login="alice:")
            assert status == 200
            vcal = vobject.readOne(content)
            assert 'name' in vcal.contents
            assert vcal.contents['name'][0].value == 'Shared Team Calendar'
            assert hasattr(vcal, 'color')
            assert vcal.color.value == 'mediumseagreen'

    def test_subscribed_calendar_simulation(self):
        """Test properties typical of subscribed/external calendars."""
        self.configure({"auth": {"type": "none"}})

        status, _, _ = self.request(
            "MKCALENDAR", "/alice/holidays/", login="alice:")
        assert status == 201

        # Simulates an imported calendar with SOURCE and REFRESH-INTERVAL
        # Note: Radicale stores one item per file, so single event here
        holiday_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Holiday Provider//EN
NAME:US Holidays 2025
DESCRIPTION:Official US federal holidays
COLOR:crimson
SOURCE;VALUE=URI:https://holidays.example.com/us.ics
REFRESH-INTERVAL;VALUE=DURATION:P7D
BEGIN:VEVENT
UID:holiday-001@holidays.example.com
DTSTAMP:20251001T000000Z
DTSTART;VALUE=DATE:20251225
SUMMARY:Christmas Day
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/alice/holidays/christmas.ics",
            holiday_ics, CONTENT_TYPE="text/calendar", login="alice:")
        assert status == 201

        status, _, content = self.request(
            "GET", "/alice/holidays/christmas.ics", login="alice:")
        assert status == 200

        vcal = vobject.readOne(content)
        assert vcal.contents['name'][0].value == 'US Holidays 2025'
        assert 'federal holidays' in vcal.description.value.lower()
        assert vcal.color.value == 'crimson'
        assert hasattr(vcal, 'source')
        assert 'holidays.example.com' in vcal.source.value
        assert hasattr(vcal, 'refresh_interval')
        # Should have P7D (weekly refresh)
        assert 'P7D' in vcal.refresh_interval.value or '7' in str(vcal.refresh_interval.value)

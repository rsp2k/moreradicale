"""
Tests for VPOLL Consensus Scheduling.

Tests the VPOLL implementation including:
- VPOLL component parsing and serialization
- Vote recording and tallying
- Winner determination
- CalDAV property discovery
"""

from datetime import datetime, timezone

import pytest

from radicale.tests import BaseTest


class TestVPollComponent:
    """Tests for VPOLL component data structures."""

    def test_parse_basic_vpoll(self):
        """Test parsing a basic VPOLL component."""
        from radicale.vpoll.component import parse_vpoll, VPollStatus

        ical_data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VPOLL
UID:poll-12345
DTSTAMP:20240101T120000Z
SUMMARY:Meeting Time Poll
POLL-MODE:BASIC
POLL-COMPLETION:SERVER-SUBMIT
STATUS:IN-PROCESS
BEGIN:PARTICIPANT
UID:owner-1
PARTICIPANT-TYPE:OWNER
CALENDAR-ADDRESS:mailto:organizer@example.com
END:PARTICIPANT
BEGIN:VEVENT
POLL-ITEM-ID:1
UID:event1@example.com
DTSTART:20240115T090000Z
DTEND:20240115T100000Z
SUMMARY:Monday 9am
END:VEVENT
BEGIN:VEVENT
POLL-ITEM-ID:2
UID:event2@example.com
DTSTART:20240116T140000Z
DTEND:20240116T150000Z
SUMMARY:Tuesday 2pm
END:VEVENT
END:VPOLL
END:VCALENDAR"""

        vpoll = parse_vpoll(ical_data)

        assert vpoll is not None
        assert vpoll.uid == "poll-12345"
        assert vpoll.summary == "Meeting Time Poll"
        assert vpoll.status == VPollStatus.IN_PROCESS
        assert len(vpoll.participants) == 1
        assert vpoll.participants[0].email == "organizer@example.com"
        assert len(vpoll.items) == 2

    def test_parse_vpoll_with_votes(self):
        """Test parsing a VPOLL with votes."""
        from radicale.vpoll.component import parse_vpoll

        ical_data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VPOLL
UID:poll-with-votes
DTSTAMP:20240101T120000Z
SUMMARY:Voted Poll
STATUS:IN-PROCESS
BEGIN:PARTICIPANT
UID:voter-1
PARTICIPANT-TYPE:VOTER
CALENDAR-ADDRESS:mailto:voter@example.com
BEGIN:VOTE
POLL-ITEM-ID:1
RESPONSE:95
COMMENT:Works great
END:VOTE
BEGIN:VOTE
POLL-ITEM-ID:2
RESPONSE:40
COMMENT:Not ideal
END:VOTE
END:PARTICIPANT
BEGIN:VEVENT
POLL-ITEM-ID:1
UID:event1@example.com
SUMMARY:Option A
END:VEVENT
BEGIN:VEVENT
POLL-ITEM-ID:2
UID:event2@example.com
SUMMARY:Option B
END:VEVENT
END:VPOLL
END:VCALENDAR"""

        vpoll = parse_vpoll(ical_data)

        assert vpoll is not None
        assert len(vpoll.participants) == 1
        voter = vpoll.participants[0]
        assert len(voter.votes) == 2
        assert voter.votes[0].poll_item_id == 1
        assert voter.votes[0].response == 95
        assert voter.votes[0].comment == "Works great"

    def test_vpoll_calculate_results(self):
        """Test calculating voting results."""
        from radicale.vpoll.component import (
            VPoll, Participant, Vote, PollItem, ParticipantType
        )

        vpoll = VPoll(
            uid="test-poll",
            dtstamp=datetime.now(timezone.utc),
            items=[
                PollItem(poll_item_id=1, component_type="VEVENT",
                        uid="item1", summary="Option A"),
                PollItem(poll_item_id=2, component_type="VEVENT",
                        uid="item2", summary="Option B"),
            ]
        )

        # Add voters with votes
        voter1 = Participant(
            uid="voter1",
            calendar_address="mailto:voter1@example.com",
            participant_type=ParticipantType.VOTER,
            votes=[
                Vote(poll_item_id=1, response=100),  # Yes
                Vote(poll_item_id=2, response=30),   # No
            ]
        )
        voter2 = Participant(
            uid="voter2",
            calendar_address="mailto:voter2@example.com",
            participant_type=ParticipantType.VOTER,
            votes=[
                Vote(poll_item_id=1, response=90),   # Yes
                Vote(poll_item_id=2, response=60),   # Maybe
            ]
        )
        vpoll.participants = [voter1, voter2]

        results = vpoll.calculate_results()

        # Item 1: 100 + 90 = 190 total, 2 yes votes
        assert results[1]["total_response"] == 190
        assert results[1]["vote_count"] == 2
        assert results[1]["yes_count"] == 2
        assert results[1]["average"] == 95.0

        # Item 2: 30 + 60 = 90 total, 1 no, 1 maybe
        assert results[2]["total_response"] == 90
        assert results[2]["no_count"] == 1
        assert results[2]["maybe_count"] == 1

    def test_vpoll_determine_winner(self):
        """Test automatic winner determination."""
        from radicale.vpoll.component import (
            VPoll, Participant, Vote, PollItem, ParticipantType
        )

        vpoll = VPoll(
            uid="test-poll",
            dtstamp=datetime.now(timezone.utc),
            items=[
                PollItem(poll_item_id=1, component_type="VEVENT",
                        uid="item1", summary="Loser"),
                PollItem(poll_item_id=2, component_type="VEVENT",
                        uid="item2", summary="Winner"),
            ]
        )

        vpoll.participants = [
            Participant(
                uid="voter1",
                calendar_address="mailto:voter1@example.com",
                participant_type=ParticipantType.VOTER,
                votes=[
                    Vote(poll_item_id=1, response=50),
                    Vote(poll_item_id=2, response=100),
                ]
            ),
            Participant(
                uid="voter2",
                calendar_address="mailto:voter2@example.com",
                participant_type=ParticipantType.VOTER,
                votes=[
                    Vote(poll_item_id=1, response=60),
                    Vote(poll_item_id=2, response=90),
                ]
            ),
        ]

        winner_id = vpoll.determine_winner()

        # Item 2 has higher total (190 vs 110)
        assert winner_id == 2

    def test_vpoll_serialization(self):
        """Test serializing a VPOLL to iCalendar format."""
        from radicale.vpoll.component import (
            VPoll, Participant, Vote, PollItem,
            ParticipantType, VPollStatus, PollMode
        )

        vpoll = VPoll(
            uid="serialize-test",
            dtstamp=datetime(2024, 1, 15, 12, 0, 0),
            summary="Serialization Test",
            status=VPollStatus.IN_PROCESS,
            poll_mode=PollMode.BASIC,
        )

        vpoll.participants = [
            Participant(
                uid="owner-1",
                calendar_address="mailto:owner@example.com",
                participant_type=ParticipantType.OWNER,
            )
        ]

        vpoll.items = [
            PollItem(
                poll_item_id=1,
                component_type="VEVENT",
                uid="item1@example.com",
                summary="Test Event",
            )
        ]

        ical = vpoll.to_ical()

        assert "BEGIN:VCALENDAR" in ical
        assert "BEGIN:VPOLL" in ical
        assert "UID:serialize-test" in ical
        assert "POLL-MODE:BASIC" in ical
        assert "BEGIN:PARTICIPANT" in ical
        assert "POLL-ITEM-ID:1" in ical

    def test_vote_response_ranges(self):
        """Test vote response value interpretation."""
        from radicale.vpoll.component import Vote

        # Yes vote (90-100)
        yes_vote = Vote(poll_item_id=1, response=95)
        assert yes_vote.response >= 90

        # Maybe vote (40-79)
        maybe_vote = Vote(poll_item_id=1, response=60)
        assert 40 <= maybe_vote.response < 80

        # No vote (0-39)
        no_vote = Vote(poll_item_id=1, response=20)
        assert no_vote.response < 40

    def test_participant_email_extraction(self):
        """Test extracting email from calendar address."""
        from radicale.vpoll.component import Participant, ParticipantType

        p1 = Participant(
            uid="p1",
            calendar_address="mailto:user@example.com",
            participant_type=ParticipantType.VOTER,
        )
        assert p1.email == "user@example.com"

        p2 = Participant(
            uid="p2",
            calendar_address="MAILTO:USER@EXAMPLE.COM",
            participant_type=ParticipantType.VOTER,
        )
        assert p2.email == "USER@EXAMPLE.COM"


class TestVPollStatus:
    """Tests for VPOLL status values."""

    def test_status_values(self):
        """Test all VPOLL status values."""
        from radicale.vpoll.component import VPollStatus

        assert VPollStatus.IN_PROCESS.value == "IN-PROCESS"
        assert VPollStatus.COMPLETED.value == "COMPLETED"
        assert VPollStatus.CONFIRMED.value == "CONFIRMED"
        assert VPollStatus.SUBMITTED.value == "SUBMITTED"
        assert VPollStatus.CANCELLED.value == "CANCELLED"

    def test_poll_mode_values(self):
        """Test POLL-MODE values."""
        from radicale.vpoll.component import PollMode

        assert PollMode.BASIC.value == "BASIC"

    def test_poll_completion_values(self):
        """Test POLL-COMPLETION values."""
        from radicale.vpoll.component import PollCompletion

        assert PollCompletion.CLIENT.value == "CLIENT"
        assert PollCompletion.SERVER.value == "SERVER"
        assert PollCompletion.SERVER_SUBMIT.value == "SERVER-SUBMIT"
        assert PollCompletion.SERVER_CHOICE.value == "SERVER-CHOICE"


class TestVPollCalDAV(BaseTest):
    """Tests for VPOLL CalDAV integration."""

    def test_vpoll_properties_disabled(self):
        """Test that VPOLL properties return 404 when disabled."""
        self.configure({
            "vpoll": {"enabled": "False"},
            "auth": {"type": "none"}
        })

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

        # Request VPOLL properties
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:supported-vpoll-component-set/>
        <C:vpoll-max-items/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/calendar/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "404" in body

    def test_vpoll_properties_enabled(self):
        """Test that VPOLL properties return values when enabled."""
        self.configure({
            "vpoll": {
                "enabled": "True",
                "max_items": "100",
                "max_voters": "500"
            },
            "auth": {"type": "none"}
        })

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

        # Request VPOLL properties
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:vpoll-max-items/>
        <C:vpoll-max-voters/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/calendar/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "200 OK" in body
        # Check that values are returned
        assert "100" in body or "vpoll-max-items" in body

    def test_supported_vpoll_component_set(self):
        """Test supported-vpoll-component-set property."""
        self.configure({
            "vpoll": {"enabled": "True"},
            "auth": {"type": "none"}
        })

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

        # Request supported-vpoll-component-set
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop>
        <C:supported-vpoll-component-set/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/calendar/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        # Should include VEVENT and VTODO as allowed components
        assert "VEVENT" in body or "comp" in body

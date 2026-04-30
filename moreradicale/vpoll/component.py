"""
VPOLL Component Data Structures.

This module defines the data structures for VPOLL consensus scheduling
as specified in draft-ietf-calext-vpoll.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from moreradicale.log import logger


class VPollStatus(Enum):
    """VPOLL status values per draft-ietf-calext-vpoll."""
    IN_PROCESS = "IN-PROCESS"    # Active voting period
    COMPLETED = "COMPLETED"       # Voting closed, no winner yet
    CONFIRMED = "CONFIRMED"       # Winner determined
    SUBMITTED = "SUBMITTED"       # Winning choice distributed
    CANCELLED = "CANCELLED"       # Poll terminated


class PollMode(Enum):
    """POLL-MODE property values."""
    BASIC = "BASIC"  # Simple single-choice voting


class PollCompletion(Enum):
    """POLL-COMPLETION property values."""
    CLIENT = "CLIENT"             # Client handles both choosing and submission
    SERVER = "SERVER"             # Server handles both
    SERVER_SUBMIT = "SERVER-SUBMIT"  # Client chooses, server submits
    SERVER_CHOICE = "SERVER-CHOICE"  # Server chooses, client submits


class ParticipantType(Enum):
    """PARTICIPANT-TYPE property values."""
    OWNER = "OWNER"    # Poll organizer
    VOTER = "VOTER"    # Poll participant who can vote


@dataclass
class Vote:
    """
    Represents a single vote within a PARTICIPANT component.

    Attributes:
        poll_item_id: ID of the item being voted on (None for alternatives)
        response: Voting response (0-100)
        comment: Optional comment about the vote
    """
    poll_item_id: Optional[int] = None
    response: int = 0
    comment: Optional[str] = None

    def to_ical(self) -> str:
        """Convert vote to iCalendar format."""
        lines = ["BEGIN:VOTE"]
        if self.poll_item_id is not None:
            lines.append(f"POLL-ITEM-ID:{self.poll_item_id}")
        lines.append(f"RESPONSE:{self.response}")
        if self.comment:
            lines.append(f"COMMENT:{self.comment}")
        lines.append("END:VOTE")
        return "\r\n".join(lines)


@dataclass
class Participant:
    """
    Represents a PARTICIPANT component within VPOLL.

    Attributes:
        uid: Unique identifier for this participant entry
        calendar_address: mailto: URI for the participant
        participant_type: OWNER or VOTER
        votes: List of Vote objects (for voters)
        stay_informed: Whether to include as attendee when confirmed
    """
    uid: str
    calendar_address: str
    participant_type: ParticipantType = ParticipantType.VOTER
    votes: List[Vote] = field(default_factory=list)
    stay_informed: bool = False

    def to_ical(self) -> str:
        """Convert participant to iCalendar format."""
        lines = ["BEGIN:PARTICIPANT"]
        lines.append(f"UID:{self.uid}")
        lines.append(f"PARTICIPANT-TYPE:{self.participant_type.value}")
        lines.append(f"CALENDAR-ADDRESS:{self.calendar_address}")
        if self.stay_informed:
            lines.append("STAY-INFORMED:TRUE")
        for vote in self.votes:
            lines.append(vote.to_ical())
        lines.append("END:PARTICIPANT")
        return "\r\n".join(lines)

    @property
    def email(self) -> Optional[str]:
        """Extract email from calendar address."""
        if self.calendar_address.lower().startswith("mailto:"):
            return self.calendar_address[7:]
        return self.calendar_address


@dataclass
class PollItem:
    """
    Represents a voteable item within VPOLL.

    This wraps a VEVENT, VTODO, or other component that participants
    can vote on.

    Attributes:
        poll_item_id: Unique integer ID within this poll
        component_type: Type of component (VEVENT, VTODO, etc.)
        uid: UID of the component
        summary: Summary/title
        dtstart: Start time (if applicable)
        dtend: End time (if applicable)
        location: Location (if applicable)
        raw_component: Full iCalendar component text
    """
    poll_item_id: int
    component_type: str
    uid: str
    summary: Optional[str] = None
    dtstart: Optional[datetime] = None
    dtend: Optional[datetime] = None
    location: Optional[str] = None
    raw_component: Optional[str] = None

    def to_ical(self) -> str:
        """Convert poll item to iCalendar format."""
        if self.raw_component:
            # Inject POLL-ITEM-ID if not present
            if "POLL-ITEM-ID:" not in self.raw_component:
                lines = self.raw_component.split("\n")
                # Insert after BEGIN:VXXX
                for i, line in enumerate(lines):
                    if line.strip().startswith("BEGIN:"):
                        lines.insert(i + 1, f"POLL-ITEM-ID:{self.poll_item_id}")
                        break
                return "\n".join(lines)
            return self.raw_component

        # Build minimal component
        lines = [f"BEGIN:{self.component_type}"]
        lines.append(f"POLL-ITEM-ID:{self.poll_item_id}")
        lines.append(f"UID:{self.uid}")
        if self.summary:
            lines.append(f"SUMMARY:{self.summary}")
        if self.dtstart:
            lines.append(f"DTSTART:{self.dtstart.strftime('%Y%m%dT%H%M%SZ')}")
        if self.dtend:
            lines.append(f"DTEND:{self.dtend.strftime('%Y%m%dT%H%M%SZ')}")
        if self.location:
            lines.append(f"LOCATION:{self.location}")
        lines.append(f"END:{self.component_type}")
        return "\r\n".join(lines)


@dataclass
class VPoll:
    """
    Represents a VPOLL consensus scheduling component.

    Attributes:
        uid: Unique identifier for the poll
        dtstamp: Timestamp of creation/modification
        summary: Poll title/summary
        description: Detailed poll description
        dtstart: Poll start time (when voting begins)
        dtend: Poll end time (voting deadline)
        status: Current poll status
        poll_mode: Voting methodology (BASIC)
        poll_completion: Who determines/submits winner
        poll_properties: Properties significant for voting
        poll_winner: ID of winning item (when confirmed)
        sequence: Sequence number for updates
        organizer: Poll organizer (mailto: URI)
        participants: List of participants (owners and voters)
        items: List of voteable items
    """
    uid: str
    dtstamp: datetime
    summary: Optional[str] = None
    description: Optional[str] = None
    dtstart: Optional[datetime] = None
    dtend: Optional[datetime] = None
    status: VPollStatus = VPollStatus.IN_PROCESS
    poll_mode: PollMode = PollMode.BASIC
    poll_completion: PollCompletion = PollCompletion.CLIENT
    poll_properties: List[str] = field(default_factory=list)
    poll_winner: Optional[int] = None
    sequence: int = 0
    organizer: Optional[str] = None
    participants: List[Participant] = field(default_factory=list)
    items: List[PollItem] = field(default_factory=list)

    @property
    def owner(self) -> Optional[Participant]:
        """Get the poll owner participant."""
        for p in self.participants:
            if p.participant_type == ParticipantType.OWNER:
                return p
        return None

    @property
    def voters(self) -> List[Participant]:
        """Get all voter participants."""
        return [p for p in self.participants
                if p.participant_type == ParticipantType.VOTER]

    def get_participant(self, email: str) -> Optional[Participant]:
        """Find participant by email address."""
        email_lower = email.lower()
        for p in self.participants:
            if p.email and p.email.lower() == email_lower:
                return p
        return None

    def get_item(self, poll_item_id: int) -> Optional[PollItem]:
        """Find poll item by ID."""
        for item in self.items:
            if item.poll_item_id == poll_item_id:
                return item
        return None

    def calculate_results(self) -> Dict[int, Dict[str, Any]]:
        """
        Calculate voting results for all items.

        Returns:
            Dict mapping poll_item_id to result dict containing:
            - total_response: Sum of all responses
            - vote_count: Number of votes
            - average: Average response
            - yes_count: Votes >= 90
            - maybe_count: Votes 40-89
            - no_count: Votes < 40
        """
        results = {}
        for item in self.items:
            results[item.poll_item_id] = {
                "total_response": 0,
                "vote_count": 0,
                "average": 0.0,
                "yes_count": 0,
                "maybe_count": 0,
                "no_count": 0,
            }

        for participant in self.voters:
            for vote in participant.votes:
                if vote.poll_item_id is None:
                    continue
                if vote.poll_item_id not in results:
                    continue

                r = results[vote.poll_item_id]
                r["total_response"] += vote.response
                r["vote_count"] += 1

                if vote.response >= 90:
                    r["yes_count"] += 1
                elif vote.response >= 40:
                    r["maybe_count"] += 1
                else:
                    r["no_count"] += 1

        # Calculate averages
        for item_id, r in results.items():
            if r["vote_count"] > 0:
                r["average"] = r["total_response"] / r["vote_count"]

        return results

    def determine_winner(self) -> Optional[int]:
        """
        Determine the winning item based on BASIC mode rules.

        In BASIC mode, the item with highest total response wins.
        Ties are broken by most yes votes, then most maybe votes.

        Returns:
            poll_item_id of winner, or None if no votes
        """
        results = self.calculate_results()
        if not results:
            return None

        winner_id = None
        winner_score = (-1, -1, -1)  # (total, yes, maybe)

        for item_id, r in results.items():
            if r["vote_count"] == 0:
                continue

            score = (r["total_response"], r["yes_count"], r["maybe_count"])
            if score > winner_score:
                winner_score = score
                winner_id = item_id

        return winner_id

    def to_ical(self, method: Optional[str] = None) -> str:
        """
        Convert VPOLL to iCalendar format.

        Args:
            method: iTIP method (REQUEST, REPLY, STATUS, CANCEL)

        Returns:
            iCalendar formatted string
        """
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Radicale//VPOLL//EN"]

        if method:
            lines.append(f"METHOD:{method}")

        lines.append("BEGIN:VPOLL")
        lines.append(f"UID:{self.uid}")
        lines.append(f"DTSTAMP:{self.dtstamp.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"SEQUENCE:{self.sequence}")

        if self.summary:
            lines.append(f"SUMMARY:{self.summary}")
        if self.description:
            lines.append(f"DESCRIPTION:{self.description}")
        if self.dtstart:
            lines.append(f"DTSTART:{self.dtstart.strftime('%Y%m%dT%H%M%SZ')}")
        if self.dtend:
            lines.append(f"DTEND:{self.dtend.strftime('%Y%m%dT%H%M%SZ')}")
        if self.organizer:
            lines.append(f"ORGANIZER:{self.organizer}")

        lines.append(f"STATUS:{self.status.value}")
        lines.append(f"POLL-MODE:{self.poll_mode.value}")
        lines.append(f"POLL-COMPLETION:{self.poll_completion.value}")

        if self.poll_properties:
            lines.append(f"POLL-PROPERTIES:{','.join(self.poll_properties)}")
        if self.poll_winner is not None:
            lines.append(f"POLL-WINNER:{self.poll_winner}")

        # Add participants
        for participant in self.participants:
            lines.append(participant.to_ical())

        # Add poll items
        for item in self.items:
            lines.append(item.to_ical())

        lines.append("END:VPOLL")
        lines.append("END:VCALENDAR")

        return "\r\n".join(lines)


def parse_vpoll(ical_data: str) -> Optional[VPoll]:
    """
    Parse iCalendar data containing a VPOLL component.

    Args:
        ical_data: iCalendar formatted string

    Returns:
        VPoll object or None if parsing fails
    """
    if "BEGIN:VPOLL" not in ical_data:
        return None

    try:
        # Extract VPOLL block
        vpoll_match = re.search(
            r'BEGIN:VPOLL\r?\n(.*?)END:VPOLL',
            ical_data,
            re.DOTALL
        )
        if not vpoll_match:
            return None

        vpoll_content = vpoll_match.group(1)

        # Parse basic properties
        uid = _extract_property(vpoll_content, "UID")
        if not uid:
            logger.warning("VPOLL missing required UID property")
            return None

        dtstamp_str = _extract_property(vpoll_content, "DTSTAMP")
        dtstamp = _parse_datetime(dtstamp_str) if dtstamp_str else datetime.utcnow()

        vpoll = VPoll(uid=uid, dtstamp=dtstamp)

        # Parse optional properties
        vpoll.summary = _extract_property(vpoll_content, "SUMMARY")
        vpoll.description = _extract_property(vpoll_content, "DESCRIPTION")
        vpoll.organizer = _extract_property(vpoll_content, "ORGANIZER")

        dtstart_str = _extract_property(vpoll_content, "DTSTART")
        if dtstart_str:
            vpoll.dtstart = _parse_datetime(dtstart_str)

        dtend_str = _extract_property(vpoll_content, "DTEND")
        if dtend_str:
            vpoll.dtend = _parse_datetime(dtend_str)

        status_str = _extract_property(vpoll_content, "STATUS")
        if status_str:
            try:
                vpoll.status = VPollStatus(status_str)
            except ValueError:
                pass

        poll_mode_str = _extract_property(vpoll_content, "POLL-MODE")
        if poll_mode_str:
            try:
                vpoll.poll_mode = PollMode(poll_mode_str)
            except ValueError:
                pass

        poll_completion_str = _extract_property(vpoll_content, "POLL-COMPLETION")
        if poll_completion_str:
            try:
                vpoll.poll_completion = PollCompletion(poll_completion_str)
            except ValueError:
                pass

        poll_props_str = _extract_property(vpoll_content, "POLL-PROPERTIES")
        if poll_props_str:
            vpoll.poll_properties = [p.strip() for p in poll_props_str.split(",")]

        poll_winner_str = _extract_property(vpoll_content, "POLL-WINNER")
        if poll_winner_str:
            try:
                vpoll.poll_winner = int(poll_winner_str)
            except ValueError:
                pass

        sequence_str = _extract_property(vpoll_content, "SEQUENCE")
        if sequence_str:
            try:
                vpoll.sequence = int(sequence_str)
            except ValueError:
                pass

        # Parse PARTICIPANT components
        vpoll.participants = _parse_participants(vpoll_content)

        # Parse poll items (VEVENT, VTODO, etc.)
        vpoll.items = _parse_poll_items(vpoll_content)

        return vpoll

    except Exception as e:
        logger.warning("Error parsing VPOLL: %s", e, exc_info=True)
        return None


def serialize_vpoll(vpoll: VPoll, method: Optional[str] = None) -> str:
    """
    Serialize a VPoll object to iCalendar format.

    Args:
        vpoll: VPoll object to serialize
        method: Optional iTIP method

    Returns:
        iCalendar formatted string
    """
    return vpoll.to_ical(method)


def _extract_property(content: str, prop_name: str) -> Optional[str]:
    """Extract a property value from iCalendar content."""
    # Handle properties with parameters (e.g., DTSTART;VALUE=DATE:20240101)
    pattern = rf'^{prop_name}(?:;[^:]*)?:(.*)$'
    match = re.search(pattern, content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def _parse_datetime(dt_str: str) -> Optional[datetime]:
    """Parse iCalendar datetime string."""
    if not dt_str:
        return None

    # Remove trailing Z and parse
    dt_str = dt_str.rstrip("Z")

    formats = [
        "%Y%m%dT%H%M%S",
        "%Y%m%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue

    return None


def _parse_participants(content: str) -> List[Participant]:
    """Parse PARTICIPANT components from VPOLL content."""
    participants = []

    # Find all PARTICIPANT blocks
    pattern = r'BEGIN:PARTICIPANT\r?\n(.*?)END:PARTICIPANT'
    for match in re.finditer(pattern, content, re.DOTALL):
        participant_content = match.group(1)

        uid = _extract_property(participant_content, "UID")
        calendar_address = _extract_property(participant_content, "CALENDAR-ADDRESS")

        if not uid or not calendar_address:
            continue

        participant = Participant(uid=uid, calendar_address=calendar_address)

        # Parse participant type
        ptype_str = _extract_property(participant_content, "PARTICIPANT-TYPE")
        if ptype_str:
            try:
                participant.participant_type = ParticipantType(ptype_str)
            except ValueError:
                pass

        # Parse STAY-INFORMED
        stay_informed_str = _extract_property(participant_content, "STAY-INFORMED")
        if stay_informed_str and stay_informed_str.upper() == "TRUE":
            participant.stay_informed = True

        # Parse VOTE components
        participant.votes = _parse_votes(participant_content)

        participants.append(participant)

    return participants


def _parse_votes(content: str) -> List[Vote]:
    """Parse VOTE components from PARTICIPANT content."""
    votes = []

    # Find all VOTE blocks
    pattern = r'BEGIN:VOTE\r?\n(.*?)END:VOTE'
    for match in re.finditer(pattern, content, re.DOTALL):
        vote_content = match.group(1)

        vote = Vote()

        poll_item_id_str = _extract_property(vote_content, "POLL-ITEM-ID")
        if poll_item_id_str:
            try:
                vote.poll_item_id = int(poll_item_id_str)
            except ValueError:
                pass

        response_str = _extract_property(vote_content, "RESPONSE")
        if response_str:
            try:
                vote.response = int(response_str)
            except ValueError:
                pass

        vote.comment = _extract_property(vote_content, "COMMENT")

        votes.append(vote)

    return votes


def _parse_poll_items(content: str) -> List[PollItem]:
    """Parse voteable items (VEVENT, VTODO, etc.) from VPOLL content."""
    items = []

    # Look for VEVENT, VTODO, VFREEBUSY components
    component_types = ["VEVENT", "VTODO", "VFREEBUSY", "VAVAILABILITY"]

    for comp_type in component_types:
        pattern = rf'(BEGIN:{comp_type}\r?\n.*?END:{comp_type})'
        for match in re.finditer(pattern, content, re.DOTALL):
            comp_content = match.group(1)

            # Extract POLL-ITEM-ID
            poll_item_id_str = _extract_property(comp_content, "POLL-ITEM-ID")
            if not poll_item_id_str:
                continue

            try:
                poll_item_id = int(poll_item_id_str)
            except ValueError:
                continue

            uid = _extract_property(comp_content, "UID") or f"item-{poll_item_id}"

            item = PollItem(
                poll_item_id=poll_item_id,
                component_type=comp_type,
                uid=uid,
                raw_component=comp_content,
            )

            item.summary = _extract_property(comp_content, "SUMMARY")
            item.location = _extract_property(comp_content, "LOCATION")

            dtstart_str = _extract_property(comp_content, "DTSTART")
            if dtstart_str:
                item.dtstart = _parse_datetime(dtstart_str)

            dtend_str = _extract_property(comp_content, "DTEND")
            if dtend_str:
                item.dtend = _parse_datetime(dtend_str)

            items.append(item)

    return items

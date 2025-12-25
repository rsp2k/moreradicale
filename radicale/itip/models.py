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
iTIP data models for RFC 5546 and RFC 6638.

These classes represent the core iTIP concepts: methods, participant status,
attendees, and complete iTIP messages.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ITIPMethod(Enum):
    """RFC 5546 Section 1.4: iTIP Methods."""
    REQUEST = "REQUEST"          # Initial invitation or update
    REPLY = "REPLY"              # Response from attendee
    CANCEL = "CANCEL"            # Cancellation by organizer
    ADD = "ADD"                  # Add instances to recurring event
    REFRESH = "REFRESH"          # Request for latest version
    COUNTER = "COUNTER"          # Propose alternative time
    DECLINECOUNTER = "DECLINECOUNTER"  # Reject counter proposal


class AttendeePartStat(Enum):
    """RFC 5545 Section 3.2.12: Participation Status."""
    NEEDS_ACTION = "NEEDS-ACTION"    # No response yet
    ACCEPTED = "ACCEPTED"            # Accepted invitation
    DECLINED = "DECLINED"            # Declined invitation
    TENTATIVE = "TENTATIVE"          # Tentatively accepted
    DELEGATED = "DELEGATED"          # Delegated to another


class CalendarUserType(Enum):
    """RFC 6638 Section 2.4.2: Calendar User Types."""
    INDIVIDUAL = "INDIVIDUAL"    # Person
    GROUP = "GROUP"              # Group of users
    RESOURCE = "RESOURCE"        # Resource (e.g., projector)
    ROOM = "ROOM"                # Meeting room
    UNKNOWN = "UNKNOWN"          # Unknown type


class AttendeeRole(Enum):
    """RFC 5545 Section 3.2.16: Participation Role."""
    CHAIR = "CHAIR"              # Chair of meeting
    REQ_PARTICIPANT = "REQ-PARTICIPANT"  # Required attendee
    OPT_PARTICIPANT = "OPT-PARTICIPANT"  # Optional attendee
    NON_PARTICIPANT = "NON-PARTICIPANT"  # Informational only


@dataclass
class ITIPAttendee:
    """Represents an attendee in an iTIP message.

    Attributes:
        email: Attendee's email address (calendar user address)
        partstat: Participation status
        cn: Common name (display name)
        role: Participation role
        is_internal: Whether attendee is on same Radicale server
        principal_path: Path to principal if internal user
    """
    email: str
    partstat: AttendeePartStat = AttendeePartStat.NEEDS_ACTION
    cn: Optional[str] = None
    role: AttendeeRole = AttendeeRole.REQ_PARTICIPANT
    is_internal: bool = False
    principal_path: Optional[str] = None


@dataclass
class ITIPMessage:
    """Represents a complete iTIP message.

    This is the primary data structure for scheduling operations.
    An iTIP message contains all information needed to process
    a scheduling request, reply, or cancellation.

    Attributes:
        method: iTIP method (REQUEST, REPLY, etc.)
        uid: Event unique identifier
        sequence: Sequence number (increments on updates)
        organizer: Organizer's email address
        attendees: List of attendees
        vobject_data: Original iCalendar data as string
        summary: Event summary/title
        dtstart: Start date/time as string
        dtend: End date/time as string
        recurrence_id: Recurrence ID for instance updates
    """
    method: ITIPMethod
    uid: str
    sequence: int
    organizer: str
    attendees: List[ITIPAttendee] = field(default_factory=list)
    vobject_data: str = ""
    summary: Optional[str] = None
    dtstart: Optional[str] = None
    dtend: Optional[str] = None
    recurrence_id: Optional[str] = None

    @property
    def is_recurring(self) -> bool:
        """Check if this message relates to a recurring event."""
        return self.recurrence_id is not None

    @property
    def internal_attendees(self) -> List[ITIPAttendee]:
        """Get list of internal attendees only."""
        return [a for a in self.attendees if a.is_internal]

    @property
    def external_attendees(self) -> List[ITIPAttendee]:
        """Get list of external attendees only."""
        return [a for a in self.attendees if not a.is_internal]


@dataclass
class ScheduleResponse:
    """RFC 6638 Section 3.2.9: schedule-response format.

    Represents the server's response after processing a POST
    to the schedule-outbox. Contains per-recipient delivery status.

    Attributes:
        recipient: Attendee email address
        request_status: Status code (e.g., "2.0;Success")
        calendar_data: Optional VEVENT data for this recipient
    """
    recipient: str
    request_status: str
    calendar_data: Optional[str] = None

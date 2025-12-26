"""
Data models for iTIP (RFC 5546) messages.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, List


class ITIPMethod(Enum):
    """iTIP methods as defined in RFC 5546 Section 3.2."""
    REQUEST = "REQUEST"
    REPLY = "REPLY"
    CANCEL = "CANCEL"
    ADD = "ADD"
    REFRESH = "REFRESH"
    COUNTER = "COUNTER"
    DECLINECOUNTER = "DECLINECOUNTER"
    PUBLISH = "PUBLISH"


class AttendeePartStat(Enum):
    """Attendee participation status as defined in RFC 5545 Section 3.2.12."""
    NEEDS_ACTION = "NEEDS-ACTION"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    TENTATIVE = "TENTATIVE"
    DELEGATED = "DELEGATED"


@dataclass
class ITIPAttendee:
    """Represents an attendee in an iTIP message."""
    email: str
    partstat: AttendeePartStat = AttendeePartStat.NEEDS_ACTION
    cn: Optional[str] = None
    role: str = "REQ-PARTICIPANT"
    cutype: str = "INDIVIDUAL"
    is_internal: bool = False
    principal_path: Optional[str] = None


@dataclass
class ITIPMessage:
    """Represents a complete iTIP message."""
    method: ITIPMethod
    uid: str
    sequence: int
    organizer: str
    attendees: List[ITIPAttendee]
    component_type: str  # VEVENT, VTODO, VJOURNAL
    icalendar_text: str  # Full iCalendar text for delivery

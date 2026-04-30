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
    """Attendee participation status as defined in RFC 5545 Section 3.2.12.

    Common to VEVENT and VTODO:
        NEEDS-ACTION, ACCEPTED, DECLINED, TENTATIVE, DELEGATED

    VTODO-specific (RFC 5545 Section 3.2.12):
        COMPLETED - Attendee has completed the task
        IN-PROCESS - Attendee is actively working on the task
    """
    NEEDS_ACTION = "NEEDS-ACTION"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    TENTATIVE = "TENTATIVE"
    DELEGATED = "DELEGATED"
    # VTODO-specific PARTSTAT values
    COMPLETED = "COMPLETED"
    IN_PROCESS = "IN-PROCESS"


class ScheduleAgent(Enum):
    """SCHEDULE-AGENT parameter values as defined in RFC 6638 Section 2.1.

    Controls whether the server handles iTIP delivery for an attendee.

    SERVER - Server performs iTIP delivery automatically (default)
    CLIENT - Client handles iTIP, server skips delivery
    NONE - No scheduling processing at all
    """
    SERVER = "SERVER"
    CLIENT = "CLIENT"
    NONE = "NONE"


class ScheduleStatus(Enum):
    """SCHEDULE-STATUS values as defined in RFC 6638 Section 3.2.9.

    The SCHEDULE-STATUS property parameter indicates the result of
    iTIP message delivery to an attendee.

    1.x - Informational
    2.x - Successful
    3.x - Client Error (permanent failure)
    5.x - Scheduling Error (could not deliver)
    """
    # 1.x - Informational
    UNKNOWN = "1.0"           # Unknown status
    PENDING = "1.1"           # Pending processing
    DELIVERED = "1.2"         # Delivered to calendar user

    # 2.x - Successful
    SUCCESS = "2.0"           # Delivered and processed successfully

    # 3.x - Client Errors (permanent failures)
    INVALID_USER = "3.7"      # Invalid calendar user address
    NO_SCHEDULING = "3.8"     # Calendar user has no scheduling privileges

    # 5.x - Scheduling Errors
    DELIVERY_FAILED = "5.1"   # Could not be delivered
    INVALID_PROPERTY = "5.2"  # Invalid property value
    INVALID_DATE = "5.3"      # Invalid date/time in request


@dataclass
class ITIPAttendee:
    """Represents an attendee in an iTIP message.

    RFC 5546 Delegation Support:
        delegated_to: Email of person this attendee delegated to
        delegated_from: Email of person who delegated to this attendee

    RFC 6638 Scheduling Control:
        schedule_agent: Controls server-side scheduling (SERVER, CLIENT, NONE)
        schedule_status: Result of iTIP delivery (SCHEDULE-STATUS parameter)
    """
    email: str
    partstat: AttendeePartStat = AttendeePartStat.NEEDS_ACTION
    cn: Optional[str] = None
    role: str = "REQ-PARTICIPANT"
    cutype: str = "INDIVIDUAL"
    is_internal: bool = False
    principal_path: Optional[str] = None
    # RFC 5546 Delegation
    delegated_to: Optional[str] = None
    delegated_from: Optional[str] = None
    # RFC 6638 Schedule Agent - controls server-side scheduling
    schedule_agent: 'ScheduleAgent' = None  # type: ignore  # Default to SERVER
    # RFC 6638 Schedule Status
    schedule_status: Optional['ScheduleStatus'] = None

    def __post_init__(self):
        """Set default schedule_agent to SERVER if not specified."""
        if self.schedule_agent is None:
            self.schedule_agent = ScheduleAgent.SERVER


@dataclass
class ITIPMessage:
    """Represents a complete iTIP message.

    Supports VEVENT, VTODO, and VJOURNAL components.
    VTODO-specific fields: due, completed, percent_complete
    """
    method: ITIPMethod
    uid: str
    sequence: int
    organizer: str
    attendees: List[ITIPAttendee]
    component_type: str = "VEVENT"  # VEVENT, VTODO, VJOURNAL
    icalendar_text: str = ""  # Full iCalendar text for delivery
    # Optional properties for parsing (common)
    summary: Optional[str] = None
    dtstart: Optional[str] = None
    dtend: Optional[str] = None
    recurrence_id: Optional[str] = None
    # VTODO-specific properties (RFC 5545)
    due: Optional[str] = None  # DUE property (like DTEND for tasks)
    completed: Optional[str] = None  # COMPLETED timestamp
    percent_complete: Optional[int] = None  # 0-100

    @property
    def internal_attendees(self) -> List['ITIPAttendee']:
        """Return only internal attendees."""
        return [a for a in self.attendees if a.is_internal]

    @property
    def external_attendees(self) -> List['ITIPAttendee']:
        """Return only external attendees."""
        return [a for a in self.attendees if not a.is_internal]

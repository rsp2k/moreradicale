"""
VAVAILABILITY and AVAILABLE component structures.

RFC 7953 defines these components for expressing calendar
user availability preferences.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, List


class BusyType(Enum):
    """BUSYTYPE property values from RFC 7953."""
    BUSY = "BUSY"
    BUSY_UNAVAILABLE = "BUSY-UNAVAILABLE"
    BUSY_TENTATIVE = "BUSY-TENTATIVE"


@dataclass
class Available:
    """
    AVAILABLE subcomponent within VAVAILABILITY.

    Defines a specific time period when the user is available.
    Supports recurrence rules for repeating availability patterns.

    Required: uid, dtstamp, dtstart
    """
    uid: str
    dtstamp: datetime
    dtstart: datetime
    dtend: Optional[datetime] = None
    duration: Optional[timedelta] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    rrule: Optional[str] = None  # Recurrence rule
    rdate: List[datetime] = field(default_factory=list)
    exdate: List[datetime] = field(default_factory=list)
    recurrence_id: Optional[datetime] = None

    @property
    def end_time(self) -> Optional[datetime]:
        """Get the end time, calculating from duration if needed."""
        if self.dtend:
            return self.dtend
        if self.duration and self.dtstart:
            return self.dtstart + self.duration
        return None


@dataclass
class VAvailability:
    """
    VAVAILABILITY component from RFC 7953.

    Represents a user's availability preferences. Time outside
    the defined AVAILABLE periods is considered busy according
    to the BUSYTYPE property.

    Required: uid, dtstamp
    """
    uid: str
    dtstamp: datetime
    dtstart: Optional[datetime] = None
    dtend: Optional[datetime] = None
    duration: Optional[timedelta] = None
    organizer: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    priority: int = 0  # 0-9, higher takes precedence
    busytype: BusyType = BusyType.BUSY_UNAVAILABLE
    available: List[Available] = field(default_factory=list)
    created: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    sequence: int = 0
    categories: List[str] = field(default_factory=list)

    @property
    def end_time(self) -> Optional[datetime]:
        """Get the end time, calculating from duration if needed."""
        if self.dtend:
            return self.dtend
        if self.duration and self.dtstart:
            return self.dtstart + self.duration
        return None

    def to_ical(self) -> str:
        """Serialize to iCalendar format."""
        return serialize_availability(self)


def _parse_datetime(value: str) -> Optional[datetime]:
    """
    Parse an iCalendar date-time value.

    Supports UTC (Z suffix) and local time formats.
    """
    if not value:
        return None

    # Clean up the value
    value = value.strip()

    # UTC format: 20240115T120000Z
    if value.endswith("Z"):
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass

    # Local format: 20240115T120000
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        pass

    # Date only: 20240115
    try:
        return datetime.strptime(value, "%Y%m%d")
    except ValueError:
        pass

    return None


def _format_datetime(dt: datetime) -> str:
    """Format datetime for iCalendar output."""
    if dt.tzinfo == timezone.utc:
        return dt.strftime("%Y%m%dT%H%M%SZ")
    return dt.strftime("%Y%m%dT%H%M%S")


def _parse_duration(value: str) -> Optional[timedelta]:
    """
    Parse an iCalendar DURATION value.

    Format: [+/-]P[n]D[T[n]H[n]M[n]S]
    """
    if not value:
        return None

    # Simple regex for common duration formats
    match = re.match(
        r"([+-])?P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
        value.upper()
    )
    if not match:
        return None

    sign = -1 if match.group(1) == "-" else 1
    days = int(match.group(2) or 0)
    hours = int(match.group(3) or 0)
    minutes = int(match.group(4) or 0)
    seconds = int(match.group(5) or 0)

    return sign * timedelta(
        days=days, hours=hours, minutes=minutes, seconds=seconds
    )


def _format_duration(td: timedelta) -> str:
    """Format timedelta as iCalendar DURATION."""
    total_seconds = int(td.total_seconds())
    sign = "" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)

    days = total_seconds // 86400
    remaining = total_seconds % 86400
    hours = remaining // 3600
    remaining %= 3600
    minutes = remaining // 60
    seconds = remaining % 60

    result = f"{sign}P"
    if days:
        result += f"{days}D"
    if hours or minutes or seconds:
        result += "T"
        if hours:
            result += f"{hours}H"
        if minutes:
            result += f"{minutes}M"
        if seconds:
            result += f"{seconds}S"

    return result if result != f"{sign}P" else f"{sign}PT0S"


def parse_availability(ical_data: str) -> Optional[VAvailability]:
    """
    Parse a VAVAILABILITY component from iCalendar data.

    Args:
        ical_data: iCalendar formatted string

    Returns:
        VAvailability object or None if parsing fails
    """
    if "BEGIN:VAVAILABILITY" not in ical_data:
        return None

    # Extract VAVAILABILITY component
    vavail_match = re.search(
        r"BEGIN:VAVAILABILITY\r?\n(.*?)END:VAVAILABILITY",
        ical_data,
        re.DOTALL
    )
    if not vavail_match:
        return None

    vavail_content = vavail_match.group(1)

    # Parse properties
    uid = _extract_property(vavail_content, "UID")
    if not uid:
        return None

    dtstamp_str = _extract_property(vavail_content, "DTSTAMP")
    dtstamp = _parse_datetime(dtstamp_str) if dtstamp_str else None
    if not dtstamp:
        dtstamp = datetime.now(timezone.utc)

    dtstart_str = _extract_property(vavail_content, "DTSTART")
    dtend_str = _extract_property(vavail_content, "DTEND")
    duration_str = _extract_property(vavail_content, "DURATION")

    busytype_str = _extract_property(vavail_content, "BUSYTYPE")
    busytype = BusyType.BUSY_UNAVAILABLE
    if busytype_str:
        try:
            busytype = BusyType(busytype_str.replace("-", "_").replace("_", "-"))
        except ValueError:
            # Try without transformation
            for bt in BusyType:
                if bt.value == busytype_str:
                    busytype = bt
                    break

    priority_str = _extract_property(vavail_content, "PRIORITY")
    priority = int(priority_str) if priority_str and priority_str.isdigit() else 0

    vavailability = VAvailability(
        uid=uid,
        dtstamp=dtstamp,
        dtstart=_parse_datetime(dtstart_str) if dtstart_str else None,
        dtend=_parse_datetime(dtend_str) if dtend_str else None,
        duration=_parse_duration(duration_str) if duration_str else None,
        organizer=_extract_property(vavail_content, "ORGANIZER"),
        summary=_extract_property(vavail_content, "SUMMARY"),
        description=_extract_property(vavail_content, "DESCRIPTION"),
        url=_extract_property(vavail_content, "URL"),
        priority=priority,
        busytype=busytype,
        sequence=int(_extract_property(vavail_content, "SEQUENCE") or 0),
    )

    # Parse AVAILABLE subcomponents
    for avail_match in re.finditer(
        r"BEGIN:AVAILABLE\r?\n(.*?)END:AVAILABLE",
        vavail_content,
        re.DOTALL
    ):
        avail_content = avail_match.group(1)
        available = _parse_available(avail_content)
        if available:
            vavailability.available.append(available)

    return vavailability


def _parse_available(content: str) -> Optional[Available]:
    """Parse an AVAILABLE subcomponent."""
    uid = _extract_property(content, "UID")
    if not uid:
        return None

    dtstamp_str = _extract_property(content, "DTSTAMP")
    dtstamp = _parse_datetime(dtstamp_str) if dtstamp_str else None
    if not dtstamp:
        dtstamp = datetime.now(timezone.utc)

    dtstart_str = _extract_property(content, "DTSTART")
    dtstart = _parse_datetime(dtstart_str) if dtstart_str else None
    if not dtstart:
        return None

    dtend_str = _extract_property(content, "DTEND")
    duration_str = _extract_property(content, "DURATION")
    recur_id_str = _extract_property(content, "RECURRENCE-ID")

    return Available(
        uid=uid,
        dtstamp=dtstamp,
        dtstart=dtstart,
        dtend=_parse_datetime(dtend_str) if dtend_str else None,
        duration=_parse_duration(duration_str) if duration_str else None,
        summary=_extract_property(content, "SUMMARY"),
        description=_extract_property(content, "DESCRIPTION"),
        location=_extract_property(content, "LOCATION"),
        rrule=_extract_property(content, "RRULE"),
        recurrence_id=_parse_datetime(recur_id_str) if recur_id_str else None,
    )


def _extract_property(content: str, prop_name: str) -> Optional[str]:
    """Extract a property value from iCalendar content."""
    # Handle properties with parameters (e.g., DTSTART;TZID=...)
    pattern = rf"^{prop_name}(?:;[^:]+)?:(.+?)(?:\r?\n(?![ \t])|\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if match:
        value = match.group(1)
        # Handle line folding (RFC 5545)
        value = re.sub(r"\r?\n[ \t]", "", value)
        return value.strip()
    return None


def serialize_availability(vavailability: VAvailability) -> str:
    """
    Serialize a VAVAILABILITY to iCalendar format.

    Args:
        vavailability: VAvailability object to serialize

    Returns:
        iCalendar formatted string
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Radicale//RFC 7953//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VAVAILABILITY",
        f"UID:{vavailability.uid}",
        f"DTSTAMP:{_format_datetime(vavailability.dtstamp)}",
    ]

    if vavailability.dtstart:
        lines.append(f"DTSTART:{_format_datetime(vavailability.dtstart)}")
    if vavailability.dtend:
        lines.append(f"DTEND:{_format_datetime(vavailability.dtend)}")
    if vavailability.duration:
        lines.append(f"DURATION:{_format_duration(vavailability.duration)}")

    if vavailability.organizer:
        lines.append(f"ORGANIZER:{vavailability.organizer}")
    if vavailability.summary:
        lines.append(f"SUMMARY:{vavailability.summary}")
    if vavailability.description:
        lines.append(f"DESCRIPTION:{vavailability.description}")
    if vavailability.url:
        lines.append(f"URL:{vavailability.url}")

    if vavailability.priority > 0:
        lines.append(f"PRIORITY:{vavailability.priority}")

    lines.append(f"BUSYTYPE:{vavailability.busytype.value}")

    if vavailability.sequence > 0:
        lines.append(f"SEQUENCE:{vavailability.sequence}")

    # Serialize AVAILABLE subcomponents
    for available in vavailability.available:
        lines.extend(_serialize_available(available))

    lines.append("END:VAVAILABILITY")
    lines.append("END:VCALENDAR")

    return "\r\n".join(lines)


def _serialize_available(available: Available) -> List[str]:
    """Serialize an AVAILABLE subcomponent."""
    lines = [
        "BEGIN:AVAILABLE",
        f"UID:{available.uid}",
        f"DTSTAMP:{_format_datetime(available.dtstamp)}",
        f"DTSTART:{_format_datetime(available.dtstart)}",
    ]

    if available.dtend:
        lines.append(f"DTEND:{_format_datetime(available.dtend)}")
    if available.duration:
        lines.append(f"DURATION:{_format_duration(available.duration)}")

    if available.summary:
        lines.append(f"SUMMARY:{available.summary}")
    if available.description:
        lines.append(f"DESCRIPTION:{available.description}")
    if available.location:
        lines.append(f"LOCATION:{available.location}")

    if available.rrule:
        lines.append(f"RRULE:{available.rrule}")
    if available.recurrence_id:
        lines.append(f"RECURRENCE-ID:{_format_datetime(available.recurrence_id)}")

    lines.append("END:AVAILABLE")
    return lines


def expand_available_instances(
    available: Available,
    range_start: datetime,
    range_end: datetime
) -> List[tuple]:
    """
    Expand AVAILABLE component to instances within a date range.

    Handles recurrence rules to generate all available periods
    within the specified range.

    Args:
        available: AVAILABLE component to expand
        range_start: Start of query range
        range_end: End of query range

    Returns:
        List of (start, end) datetime tuples
    """
    instances = []

    # Calculate end time for this available period
    end_time = available.end_time
    if not end_time:
        # Default to 1 hour if no end specified
        end_time = available.dtstart + timedelta(hours=1)

    duration = end_time - available.dtstart

    if available.rrule:
        # Parse and expand recurrence rule
        instances.extend(
            _expand_rrule(available.dtstart, duration, available.rrule,
                         range_start, range_end)
        )
    else:
        # Single occurrence
        if available.dtstart < range_end and end_time > range_start:
            instances.append((available.dtstart, end_time))

    # Filter out EXDATE
    if available.exdate:
        instances = [
            (start, end) for start, end in instances
            if start not in available.exdate
        ]

    # Add RDATE
    for rdate in available.rdate:
        if rdate >= range_start and rdate < range_end:
            instances.append((rdate, rdate + duration))

    return instances


def _expand_rrule(
    dtstart: datetime,
    duration: timedelta,
    rrule: str,
    range_start: datetime,
    range_end: datetime
) -> List[tuple]:
    """
    Expand a recurrence rule to instances.

    Simple implementation supporting common patterns.
    For full RFC 5545 compliance, use the dateutil library.
    """
    instances = []

    # Parse RRULE components
    parts = dict(
        part.split("=") for part in rrule.split(";") if "=" in part
    )

    freq = parts.get("FREQ", "DAILY")
    interval = int(parts.get("INTERVAL", 1))
    count = int(parts.get("COUNT", 0)) if "COUNT" in parts else None
    until_str = parts.get("UNTIL")
    until = _parse_datetime(until_str) if until_str else range_end
    byday = parts.get("BYDAY", "").split(",") if "BYDAY" in parts else None

    # Frequency mapping
    freq_delta = {
        "DAILY": timedelta(days=1),
        "WEEKLY": timedelta(weeks=1),
        "MONTHLY": timedelta(days=30),  # Approximation
        "YEARLY": timedelta(days=365),  # Approximation
    }

    delta = freq_delta.get(freq, timedelta(days=1)) * interval

    current = dtstart
    instance_count = 0
    max_instances = count if count else 1000  # Safety limit

    while current < until and current < range_end:
        if instance_count >= max_instances:
            break

        # Check BYDAY constraint for WEEKLY frequency
        include = True
        if byday and freq == "WEEKLY":
            day_map = {0: "MO", 1: "TU", 2: "WE", 3: "TH", 4: "FR", 5: "SA", 6: "SU"}
            current_day = day_map.get(current.weekday())
            include = current_day in byday

        if include and current + duration > range_start:
            instances.append((current, current + duration))
            instance_count += 1

        current += delta

    return instances

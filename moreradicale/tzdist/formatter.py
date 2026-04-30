"""
iCalendar VTIMEZONE formatter for TZDIST service.

Converts timezone transition data to RFC 5545 VTIMEZONE components.
"""

from datetime import datetime, timezone
from typing import List, Tuple



def format_offset(seconds: int) -> str:
    """
    Format UTC offset in iCalendar format (+/-HHMM or +/-HHMMSS).

    Args:
        seconds: UTC offset in seconds

    Returns:
        Formatted offset string like "+0100" or "-0500"
    """
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if secs > 0:
        return f"{sign}{hours:02d}{minutes:02d}{secs:02d}"
    return f"{sign}{hours:02d}{minutes:02d}"


def format_datetime_utc(dt: datetime) -> str:
    """Format datetime in UTC for iCalendar."""
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def format_datetime_local(dt: datetime) -> str:
    """Format datetime in local time for iCalendar (no Z suffix)."""
    return dt.strftime("%Y%m%dT%H%M%S")


def transitions_to_vtimezone(
    tzid: str,
    transitions: List[Tuple[datetime, str, int, int]],
    start_year: int,
    end_year: int
) -> str:
    """
    Convert timezone transitions to iCalendar VTIMEZONE component.

    Args:
        tzid: Timezone identifier (e.g., "America/New_York")
        transitions: List of (datetime, name, utc_offset, dst_offset) tuples
        start_year: Start year for the data
        end_year: End year for the data

    Returns:
        Complete VCALENDAR with VTIMEZONE component as iCalendar string
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Radicale//TZDIST Service//EN",
        "BEGIN:VTIMEZONE",
        f"TZID:{tzid}",
    ]

    # Add X-LIC-LOCATION for compatibility with some clients
    lines.append(f"X-LIC-LOCATION:{tzid}")

    if not transitions:
        # No transitions - create a simple STANDARD component
        lines.extend([
            "BEGIN:STANDARD",
            f"DTSTART:{start_year}0101T000000",
            "TZOFFSETFROM:+0000",
            "TZOFFSETTO:+0000",
            "TZNAME:UTC",
            "END:STANDARD",
        ])
    elif len(transitions) == 1:
        # Single offset (no DST)
        _, name, utc_offset, _ = transitions[0]
        offset_str = format_offset(utc_offset)
        lines.extend([
            "BEGIN:STANDARD",
            f"DTSTART:{start_year}0101T000000",
            f"TZOFFSETFROM:{offset_str}",
            f"TZOFFSETTO:{offset_str}",
            f"TZNAME:{name}",
            "END:STANDARD",
        ])
    else:
        # Multiple transitions - group into STANDARD and DAYLIGHT
        standard_transitions = []
        daylight_transitions = []

        for i, (dt, name, utc_offset, dst_offset) in enumerate(transitions):
            # Determine previous offset for TZOFFSETFROM
            if i > 0:
                prev_offset = transitions[i - 1][2]
            else:
                prev_offset = utc_offset

            transition_data = (dt, name, utc_offset, dst_offset, prev_offset)

            if dst_offset > 0:
                daylight_transitions.append(transition_data)
            else:
                standard_transitions.append(transition_data)

        # Generate STANDARD components
        for dt, name, utc_offset, dst_offset, prev_offset in standard_transitions:
            lines.extend([
                "BEGIN:STANDARD",
                f"DTSTART:{format_datetime_local(dt)}",
                f"TZOFFSETFROM:{format_offset(prev_offset)}",
                f"TZOFFSETTO:{format_offset(utc_offset)}",
                f"TZNAME:{name}",
                "END:STANDARD",
            ])

        # Generate DAYLIGHT components
        for dt, name, utc_offset, dst_offset, prev_offset in daylight_transitions:
            lines.extend([
                "BEGIN:DAYLIGHT",
                f"DTSTART:{format_datetime_local(dt)}",
                f"TZOFFSETFROM:{format_offset(prev_offset)}",
                f"TZOFFSETTO:{format_offset(utc_offset)}",
                f"TZNAME:{name}",
                "END:DAYLIGHT",
            ])

    lines.extend([
        "END:VTIMEZONE",
        "END:VCALENDAR",
    ])

    # Join with CRLF as per RFC 5545
    return "\r\n".join(lines) + "\r\n"


def generate_rrule_vtimezone(
    tzid: str,
    std_name: str,
    std_offset: int,
    dst_name: str,
    dst_offset: int,
    std_month: int,
    std_week: int,
    std_day: int,
    std_hour: int,
    dst_month: int,
    dst_week: int,
    dst_day: int,
    dst_hour: int,
) -> str:
    """
    Generate VTIMEZONE with RRULE for recurring DST transitions.

    This creates a more compact representation using RRULE instead of
    listing every transition explicitly.

    Args:
        tzid: Timezone identifier
        std_name: Standard time name (e.g., "EST")
        std_offset: Standard time UTC offset in seconds
        dst_name: Daylight time name (e.g., "EDT")
        dst_offset: Daylight time UTC offset in seconds
        std_month: Month when DST ends (1-12)
        std_week: Week of month (-1 for last)
        std_day: Day of week (0=SU, 1=MO, ..., 6=SA)
        std_hour: Hour of transition
        dst_month: Month when DST starts
        dst_week: Week of month
        dst_day: Day of week
        dst_hour: Hour of transition

    Returns:
        Complete VCALENDAR with VTIMEZONE as iCalendar string
    """
    days = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]

    def week_str(week: int) -> str:
        if week == -1:
            return "-1"
        return str(week)

    std_rrule = f"FREQ=YEARLY;BYMONTH={std_month};BYDAY={week_str(std_week)}{days[std_day]}"
    dst_rrule = f"FREQ=YEARLY;BYMONTH={dst_month};BYDAY={week_str(dst_week)}{days[dst_day]}"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Radicale//TZDIST Service//EN",
        "BEGIN:VTIMEZONE",
        f"TZID:{tzid}",
        f"X-LIC-LOCATION:{tzid}",
        "BEGIN:STANDARD",
        f"DTSTART:19700101T{std_hour:02d}0000",
        f"TZOFFSETFROM:{format_offset(dst_offset)}",
        f"TZOFFSETTO:{format_offset(std_offset)}",
        f"TZNAME:{std_name}",
        f"RRULE:{std_rrule}",
        "END:STANDARD",
        "BEGIN:DAYLIGHT",
        f"DTSTART:19700101T{dst_hour:02d}0000",
        f"TZOFFSETFROM:{format_offset(std_offset)}",
        f"TZOFFSETTO:{format_offset(dst_offset)}",
        f"TZNAME:{dst_name}",
        f"RRULE:{dst_rrule}",
        "END:DAYLIGHT",
        "END:VTIMEZONE",
        "END:VCALENDAR",
    ]

    return "\r\n".join(lines) + "\r\n"

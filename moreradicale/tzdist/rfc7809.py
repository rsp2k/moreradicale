"""
RFC 7809: CalDAV - Time Zones by Reference

This module implements timezone-by-reference support, allowing clients
and servers to exchange calendar data without full VTIMEZONE components.

When a client sends CalDAV-Timezones: F, the server omits standard
VTIMEZONE components that are available from the TZDIST service.
"""

import re
from typing import Set

from moreradicale.log import logger

# Standard IANA timezone identifiers pattern
# Matches timezones like "America/New_York", "Europe/London", "UTC", etc.
IANA_TZID_PATTERN = re.compile(
    r'^(?:'
    r'Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|'
    r'Europe|Indian|Pacific|Etc'
    r')/[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)?$|'
    r'^UTC$|^GMT$'
)


def is_standard_timezone(tzid: str) -> bool:
    """
    Check if a timezone ID is a standard IANA timezone.

    Args:
        tzid: Timezone identifier string

    Returns:
        True if this is a standard IANA timezone
    """
    if not tzid:
        return False

    # Remove any TZID prefix (some clients add "/" prefix)
    tzid = tzid.lstrip("/")

    # Check against IANA pattern
    if IANA_TZID_PATTERN.match(tzid):
        return True

    # Also check using Python's zoneinfo
    try:
        from zoneinfo import available_timezones
        return tzid in available_timezones()
    except ImportError:
        pass

    return False


def get_calendar_timezones(ical_data: str) -> Set[str]:
    """
    Extract all VTIMEZONE TZIDs from iCalendar data.

    Args:
        ical_data: iCalendar data string

    Returns:
        Set of TZID strings found in VTIMEZONE components
    """
    timezones = set()

    # Simple regex to find VTIMEZONE TZID values
    # More robust than full parsing for this use case
    vtimezone_pattern = re.compile(
        r'BEGIN:VTIMEZONE.*?TZID:([^\r\n]+).*?END:VTIMEZONE',
        re.DOTALL
    )

    for match in vtimezone_pattern.finditer(ical_data):
        tzid = match.group(1).strip()
        timezones.add(tzid)

    return timezones


def strip_standard_timezones(ical_data: str) -> str:
    """
    Remove standard IANA VTIMEZONE components from iCalendar data.

    Non-standard or custom timezones are preserved.

    Args:
        ical_data: iCalendar data string

    Returns:
        iCalendar data with standard VTIMEZONEs removed
    """
    if not ical_data:
        return ical_data

    # Find all VTIMEZONE blocks
    vtimezone_pattern = re.compile(
        r'BEGIN:VTIMEZONE\r?\n(.*?)END:VTIMEZONE\r?\n',
        re.DOTALL
    )

    def should_keep_vtimezone(match) -> str:
        """Return empty string for standard timezones, keep non-standard."""
        content = match.group(1)

        # Extract TZID from this VTIMEZONE
        tzid_match = re.search(r'TZID:([^\r\n]+)', content)
        if tzid_match:
            tzid = tzid_match.group(1).strip()
            if is_standard_timezone(tzid):
                logger.debug("Stripping standard VTIMEZONE: %s", tzid)
                return ""  # Remove this VTIMEZONE

        # Keep non-standard timezones
        return match.group(0)

    result = vtimezone_pattern.sub(should_keep_vtimezone, ical_data)
    return result


def should_include_timezones(environ: dict, configuration) -> bool:
    """
    Determine if VTIMEZONE components should be included in response.

    Checks the CalDAV-Timezones header (RFC 7809).

    Args:
        environ: WSGI environ dict
        configuration: Radicale configuration

    Returns:
        True if VTIMEZONEs should be included, False to strip them
    """
    # If TZDIST is not enabled, always include timezones
    if not configuration.get("tzdist", "enabled"):
        return True

    # Check CalDAV-Timezones header
    caldav_timezones = environ.get("HTTP_CALDAV_TIMEZONES", "").upper()

    if caldav_timezones == "F":
        # Client explicitly requests no timezones
        return False
    elif caldav_timezones == "T":
        # Client explicitly requests timezones
        return True

    # Default: include timezones for backward compatibility
    return True


def filter_calendar_response(ical_data: str, environ: dict,
                             configuration) -> str:
    """
    Filter calendar response based on CalDAV-Timezones header.

    Args:
        ical_data: iCalendar data string
        environ: WSGI environ dict
        configuration: Radicale configuration

    Returns:
        Filtered iCalendar data
    """
    if should_include_timezones(environ, configuration):
        return ical_data

    return strip_standard_timezones(ical_data)

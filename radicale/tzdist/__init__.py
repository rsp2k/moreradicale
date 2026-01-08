"""
RFC 7808 Time Zone Data Distribution Service (TZDIST) for Radicale.

This module implements the TZDIST protocol allowing calendar clients to
obtain timezone data directly from the CalDAV server via a well-defined API.

Endpoints:
    /.well-known/timezone - Service root (capabilities)
    /.well-known/timezone?action=list - List all timezones
    /.well-known/timezone?action=get&tzid=<id> - Get specific timezone
    /.well-known/timezone?action=find&pattern=<pattern> - Search timezones

References:
    RFC 7808: Time Zone Data Distribution Service
    RFC 7809: CalDAV: Time Zones by Reference
"""

# Protocol version as per RFC 7808
TZDIST_VERSION = 1

# Supported output formats
SUPPORTED_FORMATS = [
    "text/calendar",  # iCalendar format (default)
]

# Provider types
PROVIDER_ZONEINFO = "zoneinfo"
PROVIDER_PYTZ = "pytz"

# Cache key prefix
CACHE_PREFIX = "tzdist:"

# Well-known path
WELL_KNOWN_PATH = "/.well-known/timezone"

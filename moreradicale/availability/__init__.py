"""
RFC 7953 Calendar Availability.

Provides VAVAILABILITY component support for expressing
when a calendar user is typically available or busy.
"""

from moreradicale.availability.component import (
    VAvailability,
    Available,
    BusyType,
    parse_availability,
    serialize_availability,
)
from moreradicale.availability.processor import AvailabilityProcessor

__all__ = [
    "VAvailability",
    "Available",
    "BusyType",
    "parse_availability",
    "serialize_availability",
    "AvailabilityProcessor",
]

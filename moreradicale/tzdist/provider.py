"""
Timezone data providers for TZDIST service.

Supports both Python's built-in zoneinfo (3.9+) and pytz as fallback.
"""

import fnmatch
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo, available_timezones

from moreradicale.log import logger


class BaseTimezoneProvider(ABC):
    """Abstract base class for timezone providers."""

    @abstractmethod
    def list_timezones(self) -> List[str]:
        """Return all available timezone identifiers."""
        pass

    @abstractmethod
    def get_timezone(self, tzid: str) -> Optional[object]:
        """Get timezone object by identifier."""
        pass

    @abstractmethod
    def find_timezones(self, pattern: str) -> List[str]:
        """Find timezones matching a glob pattern."""
        pass

    @abstractmethod
    def get_transitions(
        self, tzid: str, start_year: int, end_year: int
    ) -> List[Tuple[datetime, str, int, int]]:
        """
        Get DST transitions for a timezone.

        Returns list of (datetime, name, utc_offset_seconds, dst_offset_seconds)
        """
        pass


class ZoneinfoProvider(BaseTimezoneProvider):
    """
    Timezone provider using Python's built-in zoneinfo module (Python 3.9+).

    Uses the IANA Time Zone Database shipped with Python or the system.
    """

    def __init__(self):
        self._cache: Dict[str, ZoneInfo] = {}
        self._available: Set[str] = available_timezones()
        logger.debug("ZoneinfoProvider initialized with %d timezones",
                     len(self._available))

    def list_timezones(self) -> List[str]:
        """Return all available timezone identifiers, sorted."""
        return sorted(self._available)

    def get_timezone(self, tzid: str) -> Optional[ZoneInfo]:
        """Get ZoneInfo object by identifier."""
        if tzid not in self._available:
            return None

        if tzid not in self._cache:
            try:
                self._cache[tzid] = ZoneInfo(tzid)
            except Exception as e:
                logger.warning("Failed to load timezone %s: %s", tzid, e)
                return None

        return self._cache[tzid]

    def find_timezones(self, pattern: str) -> List[str]:
        """
        Find timezones matching a glob pattern.

        Supports * and ? wildcards.
        Example: "America/*" matches all American timezones.
        """
        return sorted([
            tzid for tzid in self._available
            if fnmatch.fnmatch(tzid, pattern) or
            fnmatch.fnmatch(tzid.lower(), pattern.lower())
        ])

    def get_transitions(
        self, tzid: str, start_year: int, end_year: int
    ) -> List[Tuple[datetime, str, int, int]]:
        """
        Get DST transitions for a timezone between start_year and end_year.

        This samples dates throughout each year to detect offset changes,
        which indicate DST transitions.
        """
        tz = self.get_timezone(tzid)
        if not tz:
            return []

        transitions = []
        prev_offset = None

        # Sample every day to detect transitions
        current = datetime(start_year, 1, 1, tzinfo=tz)
        end = datetime(end_year + 1, 1, 1, tzinfo=tz)

        while current < end:
            offset = current.utcoffset()
            name = current.tzname()

            if prev_offset is not None and offset != prev_offset:
                # Found a transition - binary search to find exact time
                exact_dt = self._find_exact_transition(
                    tz, current - timedelta(days=1), current
                )
                if exact_dt:
                    utc_offset = int(offset.total_seconds())
                    # Calculate DST offset (difference from standard time)
                    dst_offset = self._get_dst_offset(tz, exact_dt)
                    transitions.append((exact_dt, name or "", utc_offset, dst_offset))

            prev_offset = offset
            current += timedelta(days=1)

        # If no transitions found, add the standard offset
        if not transitions:
            dt = datetime(start_year, 6, 15, 12, 0, tzinfo=tz)
            offset = dt.utcoffset()
            # Handle the case where offset might be zero (like UTC)
            offset_seconds = int(offset.total_seconds()) if offset else 0
            transitions.append((
                datetime(start_year, 1, 1, 0, 0, tzinfo=timezone.utc),
                dt.tzname() or tzid.split("/")[-1],
                offset_seconds,
                0
            ))

        return transitions

    def _find_exact_transition(
        self, tz: ZoneInfo, start: datetime, end: datetime
    ) -> Optional[datetime]:
        """Binary search to find exact transition time."""
        if (end - start) < timedelta(minutes=1):
            return end

        start_offset = start.utcoffset()
        mid = start + (end - start) / 2
        mid = mid.replace(tzinfo=tz)
        mid_offset = mid.utcoffset()

        if mid_offset == start_offset:
            return self._find_exact_transition(tz, mid, end)
        else:
            return self._find_exact_transition(tz, start, mid)

    def _get_dst_offset(self, tz: ZoneInfo, dt: datetime) -> int:
        """Calculate DST offset in seconds."""
        dst = dt.dst()
        if dst:
            return int(dst.total_seconds())
        return 0


def get_provider(provider_type: str = "zoneinfo") -> BaseTimezoneProvider:
    """
    Factory function to get the appropriate timezone provider.

    Args:
        provider_type: Either "zoneinfo" or "pytz"

    Returns:
        BaseTimezoneProvider implementation
    """
    if provider_type == "zoneinfo":
        return ZoneinfoProvider()
    elif provider_type == "pytz":
        # pytz fallback - not implemented as zoneinfo is standard
        raise NotImplementedError("pytz provider not yet implemented")
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")

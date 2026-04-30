"""
VAVAILABILITY processor for free-busy calculations.

RFC 7953 Section 5 defines how VAVAILABILITY affects
free-busy query results.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from moreradicale.log import logger
from moreradicale.availability.component import (
    VAvailability,
    BusyType,
    parse_availability,
    expand_available_instances,
)


class FreeBusyPeriod:
    """
    Represents a free/busy time period.

    Attributes:
        start: Period start time
        end: Period end time
        fb_type: Free/busy type (FREE, BUSY, BUSY-UNAVAILABLE, BUSY-TENTATIVE)
    """
    FREE = "FREE"
    BUSY = "BUSY"
    BUSY_UNAVAILABLE = "BUSY-UNAVAILABLE"
    BUSY_TENTATIVE = "BUSY-TENTATIVE"

    def __init__(self, start: datetime, end: datetime, fb_type: str = "FREE"):
        self.start = start
        self.end = end
        self.fb_type = fb_type

    def __repr__(self):
        return f"FreeBusyPeriod({self.start}, {self.end}, {self.fb_type})"


class AvailabilityProcessor:
    """
    Processes VAVAILABILITY components for free-busy calculations.

    Per RFC 7953 Section 5, the procedure is:
    1. Start with entire range as FREE
    2. Apply VAVAILABILITY by priority (lowest to highest)
    3. Mark time outside AVAILABLE periods as busy
    4. Merge with actual event busy time
    """

    def __init__(self, storage, configuration):
        """
        Initialize the availability processor.

        Args:
            storage: Radicale storage instance
            configuration: Radicale configuration
        """
        self._storage = storage
        self._configuration = configuration

    def calculate_freebusy_with_availability(
        self,
        range_start: datetime,
        range_end: datetime,
        availabilities: List[VAvailability],
        event_busy_periods: Optional[List[Tuple[datetime, datetime]]] = None
    ) -> List[FreeBusyPeriod]:
        """
        Calculate free-busy with VAVAILABILITY components.

        Args:
            range_start: Start of query range
            range_end: End of query range
            availabilities: List of VAVAILABILITY components to apply
            event_busy_periods: Optional list of busy periods from events

        Returns:
            List of FreeBusyPeriod objects representing the result
        """
        if not availabilities:
            # No availability data - return events as-is
            periods = [FreeBusyPeriod(range_start, range_end, FreeBusyPeriod.FREE)]
            if event_busy_periods:
                for start, end in event_busy_periods:
                    periods = self._mark_busy(
                        periods, start, end, FreeBusyPeriod.BUSY
                    )
            return periods

        # Sort by priority (lowest first, so higher priority overwrites)
        sorted_avails = sorted(availabilities, key=lambda a: a.priority)

        # Step 1: Start with everything as FREE
        periods = [FreeBusyPeriod(range_start, range_end, FreeBusyPeriod.FREE)]

        # Step 2-3: Apply each VAVAILABILITY by priority
        for avail in sorted_avails:
            periods = self._apply_availability(periods, avail,
                                               range_start, range_end)

        # Step 4: Overlay actual event busy time
        if event_busy_periods:
            for start, end in event_busy_periods:
                periods = self._mark_busy(periods, start, end, FreeBusyPeriod.BUSY)

        return self._merge_periods(periods)

    def _apply_availability(
        self,
        periods: List[FreeBusyPeriod],
        avail: VAvailability,
        range_start: datetime,
        range_end: datetime
    ) -> List[FreeBusyPeriod]:
        """
        Apply a single VAVAILABILITY to the free-busy periods.

        The VAVAILABILITY's time range is marked as busy according
        to its BUSYTYPE, then AVAILABLE periods within it are
        marked as FREE.
        """
        # Determine the VAVAILABILITY's effective range
        avail_start = avail.dtstart or range_start
        avail_end = avail.end_time or range_end

        # Clip to query range
        effective_start = max(avail_start, range_start)
        effective_end = min(avail_end, range_end)

        if effective_start >= effective_end:
            return periods  # No overlap with query range

        # Map BUSYTYPE to free-busy type
        fb_type_map = {
            BusyType.BUSY: FreeBusyPeriod.BUSY,
            BusyType.BUSY_UNAVAILABLE: FreeBusyPeriod.BUSY_UNAVAILABLE,
            BusyType.BUSY_TENTATIVE: FreeBusyPeriod.BUSY_TENTATIVE,
        }
        busy_type = fb_type_map.get(avail.busytype, FreeBusyPeriod.BUSY_UNAVAILABLE)

        # Mark the entire VAVAILABILITY range as busy
        periods = self._mark_busy(periods, effective_start, effective_end, busy_type)

        # Expand and mark AVAILABLE periods as FREE
        for available in avail.available:
            instances = expand_available_instances(
                available, effective_start, effective_end
            )
            for inst_start, inst_end in instances:
                periods = self._mark_free(periods, inst_start, inst_end)

        return periods

    def _mark_busy(
        self,
        periods: List[FreeBusyPeriod],
        start: datetime,
        end: datetime,
        fb_type: str
    ) -> List[FreeBusyPeriod]:
        """Mark a time range as busy."""
        result = []

        for period in periods:
            if period.end <= start or period.start >= end:
                # No overlap
                result.append(period)
            elif period.start >= start and period.end <= end:
                # Period fully contained - replace type
                result.append(FreeBusyPeriod(period.start, period.end, fb_type))
            elif period.start < start and period.end > end:
                # Period contains the busy range
                result.append(FreeBusyPeriod(period.start, start, period.fb_type))
                result.append(FreeBusyPeriod(start, end, fb_type))
                result.append(FreeBusyPeriod(end, period.end, period.fb_type))
            elif period.start < start:
                # Busy range overlaps end of period
                result.append(FreeBusyPeriod(period.start, start, period.fb_type))
                result.append(FreeBusyPeriod(start, period.end, fb_type))
            else:
                # Busy range overlaps start of period
                result.append(FreeBusyPeriod(period.start, end, fb_type))
                result.append(FreeBusyPeriod(end, period.end, period.fb_type))

        return result

    def _mark_free(
        self,
        periods: List[FreeBusyPeriod],
        start: datetime,
        end: datetime
    ) -> List[FreeBusyPeriod]:
        """Mark a time range as FREE."""
        return self._mark_busy(periods, start, end, FreeBusyPeriod.FREE)

    def _merge_periods(
        self,
        periods: List[FreeBusyPeriod]
    ) -> List[FreeBusyPeriod]:
        """Merge adjacent periods with the same type."""
        if not periods:
            return []

        # Sort by start time
        sorted_periods = sorted(periods, key=lambda p: p.start)
        merged = [sorted_periods[0]]

        for period in sorted_periods[1:]:
            last = merged[-1]
            if period.fb_type == last.fb_type and period.start <= last.end:
                # Extend the last period
                merged[-1] = FreeBusyPeriod(
                    last.start, max(last.end, period.end), last.fb_type
                )
            else:
                merged.append(period)

        return merged

    def get_user_availability(
        self,
        user: str,
        calendar_path: Optional[str] = None
    ) -> List[VAvailability]:
        """
        Get all VAVAILABILITY components for a user.

        Args:
            user: Username to get availability for
            calendar_path: Optional specific calendar path

        Returns:
            List of VAvailability objects
        """
        availabilities = []

        try:
            # Look for VAVAILABILITY in the user's calendars
            base_path = f"/{user}/" if not calendar_path else calendar_path
            items = list(self._storage.discover(base_path, depth="infinity"))

            for item in items:
                if hasattr(item, "serialize"):
                    content = item.serialize()
                    if "BEGIN:VAVAILABILITY" in content:
                        avail = parse_availability(content)
                        if avail:
                            availabilities.append(avail)

        except Exception as e:
            logger.warning("Error fetching availability for %s: %s", user, e)

        return availabilities

    def to_freebusy_ical(
        self,
        periods: List[FreeBusyPeriod],
        uid: str,
        organizer: Optional[str] = None,
        attendee: Optional[str] = None
    ) -> str:
        """
        Convert free-busy periods to iCalendar VFREEBUSY.

        Args:
            periods: List of FreeBusyPeriod objects
            uid: UID for the VFREEBUSY component
            organizer: Optional organizer address
            attendee: Optional attendee address

        Returns:
            iCalendar formatted VFREEBUSY string
        """
        now = datetime.now(timezone.utc)
        dtstamp = now.strftime("%Y%m%dT%H%M%SZ")

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Radicale//RFC 7953//EN",
            "METHOD:REPLY",
            "BEGIN:VFREEBUSY",
            f"DTSTAMP:{dtstamp}",
            f"UID:{uid}",
        ]

        if organizer:
            lines.append(f"ORGANIZER:{organizer}")
        if attendee:
            lines.append(f"ATTENDEE:{attendee}")

        # Add free-busy periods
        for period in periods:
            if period.fb_type != FreeBusyPeriod.FREE:
                start_str = period.start.strftime("%Y%m%dT%H%M%SZ")
                end_str = period.end.strftime("%Y%m%dT%H%M%SZ")
                fb_param = ""
                if period.fb_type != FreeBusyPeriod.BUSY:
                    fb_param = f";FBTYPE={period.fb_type}"
                lines.append(f"FREEBUSY{fb_param}:{start_str}/{end_str}")

        lines.append("END:VFREEBUSY")
        lines.append("END:VCALENDAR")

        return "\r\n".join(lines)


def get_inbox_calendar_availability(
    storage,
    user: str
) -> Optional[str]:
    """
    Get the calendar-availability property from user's inbox.

    RFC 7953 Section 6.1 defines this CalDAV property.

    Args:
        storage: Radicale storage instance
        user: Username

    Returns:
        iCalendar VAVAILABILITY data or None
    """
    try:
        inbox_path = f"/{user}/inbox/"
        items = list(storage.discover(inbox_path, depth="0"))
        if items:
            inbox = items[0]
            props = getattr(inbox, "props", {})
            return props.get("{urn:ietf:params:xml:ns:caldav}calendar-availability")
    except Exception as e:
        logger.debug("Error getting calendar-availability: %s", e)

    return None


def set_inbox_calendar_availability(
    storage,
    user: str,
    availability_data: str
) -> bool:
    """
    Set the calendar-availability property on user's inbox.

    Args:
        storage: Radicale storage instance
        user: Username
        availability_data: iCalendar VAVAILABILITY data

    Returns:
        True if successful
    """
    try:
        inbox_path = f"/{user}/inbox/"
        items = list(storage.discover(inbox_path, depth="0"))
        if items:
            inbox = items[0]
            props = getattr(inbox, "props", {})
            props["{urn:ietf:params:xml:ns:caldav}calendar-availability"] = availability_data
            inbox.set_meta(props)
            return True
    except Exception as e:
        logger.warning("Error setting calendar-availability: %s", e)

    return False

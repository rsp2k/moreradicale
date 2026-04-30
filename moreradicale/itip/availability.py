# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025 Ryan Malloy and contributors
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
RFC 7953 Calendar Availability (VAVAILABILITY) implementation.

This module provides support for the VAVAILABILITY component which allows
users to publish their general availability patterns (e.g., "available
Mon-Fri 9am-5pm") that enhance free/busy queries.

Key concepts:
- VAVAILABILITY: Container component defining an availability period
- AVAILABLE: Subcomponent marking specific available time slots
- BUSYTYPE: What to report for times outside AVAILABLE slots
- PRIORITY: For overlapping VAVAILABILITY components (1=highest, 9=lowest)

RFC 7953 Free/Busy Algorithm:
1. Mark entire query period as FREE
2. Apply VAVAILABILITY by priority (highest first)
3. Mark times outside AVAILABLE as BUSY-UNAVAILABLE (or BUSYTYPE value)
4. Expand AVAILABLE recurrences to mark those slots as FREE
5. Overlay actual VEVENT/VFREEBUSY busy times

See: https://datatracker.ietf.org/doc/html/rfc7953
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date, timezone
from typing import List, Optional, Tuple
from enum import Enum

try:
    import vobject
    from vobject.icalendar import utc as vobj_utc
except ImportError:
    vobject = None
    vobj_utc = None

try:
    from dateutil import rrule as dateutil_rrule
    from dateutil.tz import UTC as dateutil_utc
except ImportError:
    dateutil_rrule = None
    dateutil_utc = None

logger = logging.getLogger(__name__)


class BusyType(Enum):
    """RFC 7953 BUSYTYPE values."""
    FREE = "FREE"
    BUSY = "BUSY"
    BUSY_UNAVAILABLE = "BUSY-UNAVAILABLE"
    BUSY_TENTATIVE = "BUSY-TENTATIVE"


@dataclass
class TimeSlot:
    """Represents a time slot with its busy/free status."""
    start: datetime
    end: datetime
    status: BusyType = BusyType.FREE

    def overlaps(self, other: 'TimeSlot') -> bool:
        """Check if this slot overlaps with another."""
        return self.start < other.end and other.start < self.end

    def contains(self, point: datetime) -> bool:
        """Check if a point in time falls within this slot."""
        return self.start <= point < self.end


@dataclass
class AvailablePeriod:
    """
    Represents an AVAILABLE subcomponent from VAVAILABILITY.

    AVAILABLE defines when a user IS available within the
    VAVAILABILITY time range.
    """
    uid: str
    dtstart: datetime
    dtend: Optional[datetime] = None
    duration: Optional[timedelta] = None
    rrule: Optional[str] = None
    rdate: List[datetime] = field(default_factory=list)
    exdate: List[datetime] = field(default_factory=list)
    summary: str = ""
    location: str = ""

    def get_end(self) -> datetime:
        """Get the end time, calculating from duration if needed."""
        if self.dtend:
            return self.dtend
        if self.duration:
            return self.dtstart + self.duration
        # Default to 1 hour if no end/duration specified
        return self.dtstart + timedelta(hours=1)

    def get_occurrences(self, range_start: datetime, range_end: datetime) -> List[Tuple[datetime, datetime]]:
        """
        Get all occurrences of this AVAILABLE within a time range.

        Handles both single slots and recurring availability (RRULE).

        Args:
            range_start: Start of query range
            range_end: End of query range

        Returns:
            List of (start, end) tuples for each occurrence
        """
        occurrences = []
        slot_duration = self.get_end() - self.dtstart

        # Ensure timezone-aware comparison
        range_start = _ensure_utc(range_start)
        range_end = _ensure_utc(range_end)
        base_start = _ensure_utc(self.dtstart)

        if self.rrule and dateutil_rrule:
            try:
                # Parse RRULE
                rule = dateutil_rrule.rrulestr(
                    f"RRULE:{self.rrule}",
                    dtstart=base_start
                )

                # Get occurrences within range (limit to prevent runaway)
                count = 0
                max_occurrences = 1000

                for occ_start in rule:
                    if count >= max_occurrences:
                        break

                    occ_start = _ensure_utc(occ_start)
                    occ_end = occ_start + slot_duration

                    # Check if occurrence is in range
                    if occ_end <= range_start:
                        continue
                    if occ_start >= range_end:
                        break

                    # Check EXDATE exclusions
                    if any(_same_datetime(occ_start, ex) for ex in self.exdate):
                        continue

                    occurrences.append((occ_start, occ_end))
                    count += 1

            except Exception as e:
                logger.warning(f"Error expanding AVAILABLE RRULE: {e}")
                # Fall back to single occurrence
                if base_start < range_end and self.get_end() > range_start:
                    occurrences.append((base_start, _ensure_utc(self.get_end())))
        else:
            # Single occurrence
            end = _ensure_utc(self.get_end())
            if base_start < range_end and end > range_start:
                occurrences.append((base_start, end))

        # Add RDATE occurrences
        for rdt in self.rdate:
            rdt = _ensure_utc(rdt)
            rdt_end = rdt + slot_duration
            if rdt < range_end and rdt_end > range_start:
                if not any(_same_datetime(rdt, ex) for ex in self.exdate):
                    occurrences.append((rdt, rdt_end))

        return sorted(occurrences, key=lambda x: x[0])


@dataclass
class VAvailability:
    """
    Represents a VAVAILABILITY component.

    VAVAILABILITY defines a period during which availability information
    is provided. Outside of AVAILABLE subcomponents, the user is considered
    BUSY-UNAVAILABLE (or the specified BUSYTYPE).
    """
    uid: str
    dtstamp: datetime
    dtstart: Optional[datetime] = None  # If None, unbounded start
    dtend: Optional[datetime] = None    # If None, unbounded end
    duration: Optional[timedelta] = None
    priority: int = 0  # 0=lowest, 1=highest, 9=low
    busytype: BusyType = BusyType.BUSY_UNAVAILABLE
    summary: str = ""
    location: str = ""
    available: List[AvailablePeriod] = field(default_factory=list)

    def get_effective_end(self) -> Optional[datetime]:
        """Get the effective end time."""
        if self.dtend:
            return self.dtend
        if self.duration and self.dtstart:
            return self.dtstart + self.duration
        return None

    def is_active_at(self, dt: datetime) -> bool:
        """Check if this VAVAILABILITY is active at a given time."""
        dt = _ensure_utc(dt)

        if self.dtstart:
            start = _ensure_utc(self.dtstart)
            if dt < start:
                return False

        end = self.get_effective_end()
        if end:
            end = _ensure_utc(end)
            if dt >= end:
                return False

        return True

    def get_available_slots(self, range_start: datetime, range_end: datetime) -> List[Tuple[datetime, datetime]]:
        """
        Get all AVAILABLE slots within a time range.

        Args:
            range_start: Start of query range
            range_end: End of query range

        Returns:
            Sorted list of (start, end) tuples when user is available
        """
        all_slots = []

        for available in self.available:
            slots = available.get_occurrences(range_start, range_end)
            all_slots.extend(slots)

        # Sort and merge overlapping slots
        return _merge_overlapping_slots(all_slots)


class AvailabilityProcessor:
    """
    Processes VAVAILABILITY components for free/busy queries.

    Implements the RFC 7953 algorithm for combining availability
    patterns with actual event busy times.
    """

    def __init__(self, storage, configuration):
        """
        Initialize the availability processor.

        Args:
            storage: Radicale storage backend
            configuration: Radicale configuration
        """
        self.storage = storage
        self.configuration = configuration

    def get_user_availability(self, principal_path: str) -> List[VAvailability]:
        """
        Get all VAVAILABILITY components for a user.

        Scans the user's calendars for VAVAILABILITY components.

        Args:
            principal_path: User's principal path (e.g., /alice/)

        Returns:
            List of VAvailability objects sorted by priority (highest first)
        """
        availabilities = []

        try:
            discovered = list(self.storage.discover(principal_path, depth="1"))

            for collection in discovered:
                # Skip non-calendar collections
                if not hasattr(collection, 'tag') or collection.tag != 'VCALENDAR':
                    continue

                # Skip schedule-inbox/outbox
                if 'schedule-' in collection.path.lower():
                    continue

                try:
                    hrefs = list(collection._list())

                    for href in hrefs:
                        item = collection._get(href)
                        if not item:
                            continue

                        vcal = item.vobject_item

                        for subcomp in vcal.getChildren():
                            if subcomp.name == 'VAVAILABILITY':
                                vavail = self._parse_vavailability(subcomp)
                                if vavail:
                                    availabilities.append(vavail)

                except Exception as e:
                    logger.warning(f"Error reading calendar {collection.path}: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Error discovering collections for {principal_path}: {e}")

        # Sort by priority (1=highest priority comes first, 0=lowest)
        # RFC 7953: priority 1 is highest, 9 is low, 0 is undefined/lowest
        availabilities.sort(key=lambda v: (v.priority if v.priority > 0 else 10))

        return availabilities

    def _parse_vavailability(self, vavail_comp) -> Optional[VAvailability]:
        """
        Parse a VAVAILABILITY vobject component.

        Args:
            vavail_comp: vobject VAVAILABILITY component

        Returns:
            VAvailability object or None if invalid
        """
        try:
            # Required properties
            uid = getattr(vavail_comp, 'uid', None)
            if not uid:
                logger.warning("VAVAILABILITY missing required UID")
                return None

            dtstamp = getattr(vavail_comp, 'dtstamp', None)
            if not dtstamp:
                logger.warning("VAVAILABILITY missing required DTSTAMP")
                return None

            # Optional properties
            dtstart = getattr(vavail_comp, 'dtstart', None)
            dtend = getattr(vavail_comp, 'dtend', None)
            duration = getattr(vavail_comp, 'duration', None)
            priority_prop = getattr(vavail_comp, 'priority', None)
            busytype_prop = getattr(vavail_comp, 'busytype', None)
            summary = getattr(vavail_comp, 'summary', None)
            location = getattr(vavail_comp, 'location', None)

            # Parse priority (default 0)
            priority = 0
            if priority_prop:
                try:
                    priority = int(priority_prop.value)
                except (ValueError, TypeError):
                    pass

            # Parse BUSYTYPE (default BUSY-UNAVAILABLE)
            busytype = BusyType.BUSY_UNAVAILABLE
            if busytype_prop:
                bt_str = str(busytype_prop.value).upper().replace('_', '-')
                try:
                    busytype = BusyType(bt_str)
                except ValueError:
                    logger.debug(f"Unknown BUSYTYPE: {bt_str}, using BUSY-UNAVAILABLE")

            # Parse AVAILABLE subcomponents
            available_list = []
            for child in vavail_comp.getChildren():
                if child.name == 'AVAILABLE':
                    avail = self._parse_available(child)
                    if avail:
                        available_list.append(avail)

            return VAvailability(
                uid=uid.value,
                dtstamp=dtstamp.value,
                dtstart=dtstart.value if dtstart else None,
                dtend=dtend.value if dtend else None,
                duration=duration.value if duration else None,
                priority=priority,
                busytype=busytype,
                summary=summary.value if summary else "",
                location=location.value if location else "",
                available=available_list
            )

        except Exception as e:
            logger.warning(f"Error parsing VAVAILABILITY: {e}")
            return None

    def _parse_available(self, avail_comp) -> Optional[AvailablePeriod]:
        """
        Parse an AVAILABLE vobject component.

        Args:
            avail_comp: vobject AVAILABLE component

        Returns:
            AvailablePeriod object or None if invalid
        """
        try:
            # Required properties
            uid = getattr(avail_comp, 'uid', None)
            dtstart = getattr(avail_comp, 'dtstart', None)

            if not dtstart:
                logger.warning("AVAILABLE missing required DTSTART")
                return None

            # Optional properties
            dtend = getattr(avail_comp, 'dtend', None)
            duration = getattr(avail_comp, 'duration', None)
            rrule = getattr(avail_comp, 'rrule', None)
            summary = getattr(avail_comp, 'summary', None)
            location = getattr(avail_comp, 'location', None)

            # Parse RDATE
            rdate_list = []
            if hasattr(avail_comp, 'rdate'):
                rdates = avail_comp.rdate
                if not isinstance(rdates, list):
                    rdates = [rdates]
                for rd in rdates:
                    if hasattr(rd, 'value'):
                        if isinstance(rd.value, list):
                            rdate_list.extend(rd.value)
                        else:
                            rdate_list.append(rd.value)

            # Parse EXDATE
            exdate_list = []
            if hasattr(avail_comp, 'exdate'):
                exdates = avail_comp.exdate
                if not isinstance(exdates, list):
                    exdates = [exdates]
                for ex in exdates:
                    if hasattr(ex, 'value'):
                        if isinstance(ex.value, list):
                            exdate_list.extend(ex.value)
                        else:
                            exdate_list.append(ex.value)

            return AvailablePeriod(
                uid=uid.value if uid else f"available-{id(avail_comp)}",
                dtstart=dtstart.value,
                dtend=dtend.value if dtend else None,
                duration=duration.value if duration else None,
                rrule=rrule.value if rrule else None,
                rdate=rdate_list,
                exdate=exdate_list,
                summary=summary.value if summary else "",
                location=location.value if location else ""
            )

        except Exception as e:
            logger.warning(f"Error parsing AVAILABLE: {e}")
            return None

    def calculate_freebusy_with_availability(
        self,
        principal_path: str,
        range_start: datetime,
        range_end: datetime,
        event_busy_periods: List[Tuple[datetime, datetime, str]]
    ) -> List[Tuple[datetime, datetime, str]]:
        """
        Calculate free/busy times considering VAVAILABILITY.

        Implements the RFC 7953 algorithm:
        1. Get VAVAILABILITY components sorted by priority
        2. For each time point in the range:
           - Check highest-priority active VAVAILABILITY
           - If inside AVAILABLE slot: FREE (unless event blocks it)
           - If outside AVAILABLE: BUSY-UNAVAILABLE (or component's BUSYTYPE)
        3. Overlay actual event busy times

        Args:
            principal_path: User's principal path
            range_start: Query start time
            range_end: Query end time
            event_busy_periods: List of (start, end, fbtype) from actual events

        Returns:
            Combined list of (start, end, fbtype) busy periods
        """
        range_start = _ensure_utc(range_start)
        range_end = _ensure_utc(range_end)

        # Get user's VAVAILABILITY components
        availabilities = self.get_user_availability(principal_path)

        if not availabilities:
            # No VAVAILABILITY defined - just return event busy times
            logger.debug(f"No VAVAILABILITY for {principal_path}, using events only")
            return event_busy_periods

        logger.debug(f"Found {len(availabilities)} VAVAILABILITY components for {principal_path}")

        # Build timeline of unavailable periods from VAVAILABILITY
        unavailable_periods = []

        for vavail in availabilities:
            # Check if this VAVAILABILITY is active in our range
            vavail_start = _ensure_utc(vavail.dtstart) if vavail.dtstart else range_start
            vavail_end = _ensure_utc(vavail.get_effective_end()) if vavail.get_effective_end() else range_end

            # Clip to query range
            effective_start = max(vavail_start, range_start)
            effective_end = min(vavail_end, range_end)

            if effective_start >= effective_end:
                continue

            # Get available slots within this VAVAILABILITY
            available_slots = vavail.get_available_slots(effective_start, effective_end)

            # The gaps between AVAILABLE slots are BUSY-UNAVAILABLE
            busytype_str = vavail.busytype.value

            if not available_slots:
                # No AVAILABLE defined - entire period is unavailable
                unavailable_periods.append((effective_start, effective_end, busytype_str))
            else:
                # Find gaps between available slots
                cursor = effective_start

                for slot_start, slot_end in available_slots:
                    if cursor < slot_start:
                        # Gap before this slot
                        unavailable_periods.append((cursor, slot_start, busytype_str))
                    cursor = max(cursor, slot_end)

                # Gap after last slot
                if cursor < effective_end:
                    unavailable_periods.append((cursor, effective_end, busytype_str))

        # Combine unavailable periods with event busy times
        all_busy = unavailable_periods + list(event_busy_periods)

        # Sort by start time
        all_busy.sort(key=lambda x: x[0])

        # Merge overlapping periods (keeping highest priority busy type)
        # BUSY > BUSY-UNAVAILABLE > BUSY-TENTATIVE
        return _merge_busy_periods(all_busy)


def _ensure_utc(dt) -> datetime:
    """Ensure a datetime is timezone-aware (UTC)."""
    if dt is None:
        return None

    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, datetime.min.time())

    if hasattr(dt, 'tzinfo'):
        if dt.tzinfo is None:
            if vobj_utc:
                return dt.replace(tzinfo=vobj_utc)
            elif dateutil_utc:
                return dt.replace(tzinfo=dateutil_utc)

    return dt


def _same_datetime(dt1, dt2) -> bool:
    """Check if two datetimes represent the same point in time."""
    dt1 = _ensure_utc(dt1)
    dt2 = _ensure_utc(dt2)

    # Handle date vs datetime comparison
    if isinstance(dt1, date) and not isinstance(dt1, datetime):
        dt1 = datetime.combine(dt1, datetime.min.time())
    if isinstance(dt2, date) and not isinstance(dt2, datetime):
        dt2 = datetime.combine(dt2, datetime.min.time())

    return dt1 == dt2


def _merge_overlapping_slots(slots: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    """Merge overlapping time slots into contiguous periods."""
    if not slots:
        return []

    sorted_slots = sorted(slots, key=lambda x: x[0])
    merged = [sorted_slots[0]]

    for start, end in sorted_slots[1:]:
        last_start, last_end = merged[-1]

        if start <= last_end:
            # Overlapping or adjacent - merge
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def _merge_busy_periods(periods: List[Tuple[datetime, datetime, str]]) -> List[Tuple[datetime, datetime, str]]:
    """
    Merge overlapping busy periods, keeping the highest priority busy type.

    Priority order: BUSY > BUSY-UNAVAILABLE > BUSY-TENTATIVE
    """
    if not periods:
        return []

    # Define priority (lower number = higher priority)
    priority_map = {
        'BUSY': 1,
        'BUSY-UNAVAILABLE': 2,
        'BUSY-TENTATIVE': 3,
    }

    sorted_periods = sorted(periods, key=lambda x: x[0])
    merged = []

    for start, end, fbtype in sorted_periods:
        if not merged:
            merged.append([start, end, fbtype])
            continue

        last = merged[-1]

        if start <= last[1]:
            # Overlapping - merge and use higher priority type
            last[1] = max(last[1], end)
            if priority_map.get(fbtype, 10) < priority_map.get(last[2], 10):
                last[2] = fbtype
        else:
            merged.append([start, end, fbtype])

    return [(s, e, t) for s, e, t in merged]


def create_vavailability_ics(
    uid: str,
    summary: str,
    available_slots: List[dict],
    dtstart: Optional[datetime] = None,
    dtend: Optional[datetime] = None,
    priority: int = 0,
    busytype: str = "BUSY-UNAVAILABLE",
    location: str = ""
) -> str:
    """
    Create a VAVAILABILITY iCalendar component.

    This is a helper function for creating VAVAILABILITY data
    programmatically.

    Args:
        uid: Unique identifier for the component
        summary: Description of this availability
        available_slots: List of dicts with 'dtstart', 'dtend', optional 'rrule'
        dtstart: When this availability pattern starts (None = unbounded)
        dtend: When this availability pattern ends (None = unbounded)
        priority: Priority level (1=highest, 9=low, 0=undefined)
        busytype: Busy type for times outside AVAILABLE slots
        location: Location for this availability

    Returns:
        iCalendar text with VAVAILABILITY component

    Example:
        >>> slots = [
        ...     {'dtstart': datetime(2025, 1, 1, 9, 0),
        ...      'dtend': datetime(2025, 1, 1, 17, 0),
        ...      'rrule': 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR'}
        ... ]
        >>> ics = create_vavailability_ics(
        ...     uid='work-hours-1',
        ...     summary='Work Hours',
        ...     available_slots=slots
        ... )
    """
    from datetime import datetime

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Radicale//RFC7953//EN",
        "BEGIN:VAVAILABILITY",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    ]

    if dtstart:
        lines.append(f"DTSTART:{dtstart.strftime('%Y%m%dT%H%M%SZ')}")
    if dtend:
        lines.append(f"DTEND:{dtend.strftime('%Y%m%dT%H%M%SZ')}")
    if summary:
        lines.append(f"SUMMARY:{summary}")
    if location:
        lines.append(f"LOCATION:{location}")
    if priority > 0:
        lines.append(f"PRIORITY:{priority}")
    if busytype and busytype != "BUSY-UNAVAILABLE":
        lines.append(f"BUSYTYPE:{busytype}")

    # Add AVAILABLE subcomponents
    for i, slot in enumerate(available_slots):
        lines.append("BEGIN:AVAILABLE")
        lines.append(f"UID:{uid}-available-{i+1}")
        lines.append(f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")

        slot_start = slot.get('dtstart')
        slot_end = slot.get('dtend')
        slot_rrule = slot.get('rrule')
        slot_summary = slot.get('summary', '')

        if slot_start:
            lines.append(f"DTSTART:{slot_start.strftime('%Y%m%dT%H%M%SZ')}")
        if slot_end:
            lines.append(f"DTEND:{slot_end.strftime('%Y%m%dT%H%M%SZ')}")
        if slot_rrule:
            lines.append(f"RRULE:{slot_rrule}")
        if slot_summary:
            lines.append(f"SUMMARY:{slot_summary}")

        lines.append("END:AVAILABLE")

    lines.extend([
        "END:VAVAILABILITY",
        "END:VCALENDAR",
    ])

    return "\r\n".join(lines)

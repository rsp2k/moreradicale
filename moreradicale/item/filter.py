# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2008 Nicolas Kandel
# Copyright © 2008 Pascal Halter
# Copyright © 2008-2015 Guillaume Ayoub
# Copyright © 2017-2021 Unrud <unrud@outlook.com>
# Copyright © 2023-2024 Ray <ray@react0r.com>
# Copyright © 2024-2025 Peter Bieringer <pb@bieringer.de>
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


import math
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from itertools import chain
from typing import (Callable, Iterable, Iterator, List, Optional, Sequence,
                    Tuple, Union)

import vobject
from dateutil import rrule as dateutil_rrule
from dateutil import tz as dateutil_tz

from moreradicale import item, xmlutils
from moreradicale.log import logger
from moreradicale.utils import format_ut

DAY: timedelta = timedelta(days=1)
SECOND: timedelta = timedelta(seconds=1)
DATETIME_MIN: datetime = datetime.min.replace(tzinfo=timezone.utc)
DATETIME_MAX: datetime = datetime.max.replace(tzinfo=timezone.utc)
TIMESTAMP_MIN: int = math.floor(DATETIME_MIN.timestamp())
TIMESTAMP_MAX: int = math.ceil(DATETIME_MAX.timestamp())

if sys.version_info < (3, 10):
    TRIGGER = Union[datetime, None]
else:
    TRIGGER = datetime | None


# Module-level cache for parsed calendar timezones
_calendar_timezone_cache: dict = {}

# Module-level default timezone for floating time interpretation
# Used during filter operations when a collection has C:calendar-timezone set
_default_floating_timezone: Optional[datetime.tzinfo] = None


def set_default_floating_timezone(tzinfo: Optional[datetime.tzinfo]) -> None:
    """Set the default timezone for interpreting floating times.

    RFC 4791 §5.3.2: Floating times in calendar components should be
    interpreted using the collection's calendar-timezone property.

    Call this before filtering to set the timezone, and call with None
    to clear it after filtering.
    """
    global _default_floating_timezone
    _default_floating_timezone = tzinfo


def get_default_floating_timezone() -> datetime.tzinfo:
    """Get the default timezone for interpreting floating times.

    Returns the collection's calendar-timezone if set, otherwise UTC.
    """
    return _default_floating_timezone or vobject.icalendar.utc


def parse_calendar_timezone(calendar_timezone_prop: Optional[str]) -> Optional[datetime.tzinfo]:
    """Parse a calendar-timezone property value and return a tzinfo object.

    RFC 4791 §5.3.2: The calendar-timezone property value is an iCalendar
    object containing exactly one VTIMEZONE component.

    Args:
        calendar_timezone_prop: The text value of the C:calendar-timezone property

    Returns:
        A tzinfo object representing the timezone, or None if parsing fails
    """
    if not calendar_timezone_prop:
        return None

    # Check cache first
    if calendar_timezone_prop in _calendar_timezone_cache:
        return _calendar_timezone_cache[calendar_timezone_prop]

    try:
        cal = vobject.readOne(calendar_timezone_prop)
        if hasattr(cal, 'vtimezone'):
            vtimezone = cal.vtimezone
            tzid = getattr(vtimezone, 'tzid', None)
            if tzid:
                # Try to use dateutil to get the timezone
                tzinfo = dateutil_tz.gettz(tzid.value)
                if tzinfo:
                    _calendar_timezone_cache[calendar_timezone_prop] = tzinfo
                    logger.debug("Parsed calendar-timezone: %s", tzid.value)
                    return tzinfo
                # Fall back to vobject's timezone handling
                logger.debug("Using vobject timezone for: %s", tzid.value)
                # vobject creates timezone objects when parsing VTIMEZONE
                _calendar_timezone_cache[calendar_timezone_prop] = vtimezone
                return None  # vobject timezones don't implement tzinfo interface well
    except Exception as e:
        logger.warning("Failed to parse calendar-timezone: %s", e)

    _calendar_timezone_cache[calendar_timezone_prop] = None
    return None


def date_to_datetime(d: date, tzinfo=None) -> datetime:
    """Transform any date to a datetime with timezone.

    If ``d`` is a datetime without timezone (floating time), use the
    default floating timezone per RFC 4791 §5.3.2. If no timezone is
    explicitly provided and no collection calendar-timezone is set,
    defaults to UTC.

    If ``d`` is already a datetime with timezone, return as is.

    Args:
        d: A date or datetime object
        tzinfo: Optional explicit timezone to use for floating times

    Returns:
        A timezone-aware datetime
    """
    if not isinstance(d, datetime):
        d = datetime.combine(d, datetime.min.time())
    if not d.tzinfo:
        # Use provided tzinfo, or the default floating timezone (UTC if not set)
        effective_tz = tzinfo if tzinfo is not None else get_default_floating_timezone()
        d = d.replace(tzinfo=effective_tz)
    return d


def parse_time_range(time_filter: ET.Element) -> Tuple[datetime, datetime]:
    start_text = time_filter.get("start")
    end_text = time_filter.get("end")
    if start_text:
        start = datetime.strptime(
            start_text, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc)
    else:
        start = DATETIME_MIN
    if end_text:
        end = datetime.strptime(
            end_text, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc)
    else:
        end = DATETIME_MAX
    return start, end


def time_range_timestamps(time_filter: ET.Element) -> Tuple[int, int]:
    start, end = parse_time_range(time_filter)
    return (math.floor(start.timestamp()), math.ceil(end.timestamp()))


def comp_match(item: "item.Item", filter_: ET.Element, level: int = 0) -> bool:
    """Check whether the ``item`` matches the comp ``filter_``.

    If ``level`` is ``0``, the filter is applied on the
    item's collection. Otherwise, it's applied on the item.

    See rfc4791-9.7.1.

    """

    # HACK: the filters are tested separately against all components

    name = filter_.get("name", "").upper()
    logger.debug("TRACE/ITEM/FILTER/comp_match: name=%s level=%d", name, level)

    if level == 0:
        tag = item.name
    elif level == 1:
        tag = item.component_name
    elif level == 2:
        tag = item.component_name
    else:
        logger.warning(
            "Filters with %d levels of comp-filter are not supported", level)
        return True
    if not tag:
        return False

    # At level 2 (nested components like VALARM), check for component existence
    # in the parent components rather than comparing name == tag
    if level == 2:
        # Get parent components (e.g., vevent_list for VALARM in VEVENT)
        parent_components = list(getattr(item.vobject_item, "%s_list" % tag.lower(), []))
        if len(filter_) == 0:
            # Point #1 of rfc4791-9.7.1 - check if nested component exists
            for parent in parent_components:
                # Check for the nested component (e.g., valarm in vevent)
                nested_list = getattr(parent, "%s_list" % name.lower(), [])
                if nested_list:
                    return True
            return False
        if len(filter_) == 1:
            if filter_[0].tag == xmlutils.make_clark("C:is-not-defined"):
                # Point #2 of rfc4791-9.7.1 - check if nested component does NOT exist
                for parent in parent_components:
                    nested_list = getattr(parent, "%s_list" % name.lower(), [])
                    if nested_list:
                        return False
                return True

    if len(filter_) == 0:
        # Point #1 of rfc4791-9.7.1
        return name == tag
    if len(filter_) == 1:
        if filter_[0].tag == xmlutils.make_clark("C:is-not-defined"):
            # Point #2 of rfc4791-9.7.1
            return name != tag
    if (level < 2) and (name != tag):
        return False
    if ((level == 0 and name != "VCALENDAR") or
            (level == 1 and name not in ("VTODO", "VEVENT", "VJOURNAL", "VFREEBUSY", "VAVAILABILITY")) or
            (level == 2 and name not in ("VALARM", "AVAILABLE"))):
        logger.warning("Filtering %s is not supported", name)
        return True
    # Point #3 and #4 of rfc4791-9.7.1
    trigger = None
    if level == 0:
        components = [item.vobject_item]
    elif level == 1:
        components = list(getattr(item.vobject_item, "%s_list" % tag.lower()))
    elif level == 2:
        components = list(getattr(item.vobject_item, "%s_list" % tag.lower()))
        for comp in components:
            subcomp = getattr(comp, name.lower(), None)
            if not subcomp:
                return False
            if hasattr(subcomp, "trigger"):
                # rfc4791-7.8.5:
                trigger = subcomp.trigger.value
    for child in filter_:
        if child.tag == xmlutils.make_clark("C:prop-filter"):
            logger.debug("TRACE/ITEM/FILTER/comp_match: prop-filter level=%d", level)
            if not any(prop_match(comp, child, "C")
                       for comp in components):
                return False
        elif child.tag == xmlutils.make_clark("C:time-range"):
            logger.debug("TRACE/ITEM/FILTER/comp_match: time-range level=%d tag=%s", level, tag)
            if (level == 0) and (name == "VCALENDAR"):
                for name_try in ("VTODO", "VEVENT", "VJOURNAL", "VFREEBUSY"):
                    try:
                        if time_range_match(item.vobject_item, filter_[0], name_try, trigger):
                            return True
                    except Exception:
                        continue
                return False
            if not time_range_match(item.vobject_item, filter_[0], tag, trigger):
                return False
        elif child.tag == xmlutils.make_clark("C:comp-filter"):
            logger.debug("TRACE/ITEM/FILTER/comp_match: comp-filter level=%d", level)
            if not comp_match(item, child, level=level + 1):
                return False
        else:
            raise ValueError("Unexpected %r in comp-filter" % child.tag)
    return True


def prop_match(vobject_item: vobject.base.Component,
               filter_: ET.Element, ns: str) -> bool:
    """Check whether the ``item`` matches the prop ``filter_``.

    See rfc4791-9.7.2 and rfc6352-10.5.1.

    """
    name = filter_.get("name", "").lower()
    if len(filter_) == 0:
        # Point #1 of rfc4791-9.7.2
        return name in vobject_item.contents
    if len(filter_) == 1:
        if filter_[0].tag == xmlutils.make_clark("%s:is-not-defined" % ns):
            # Point #2 of rfc4791-9.7.2
            return name not in vobject_item.contents
    if name not in vobject_item.contents:
        return False
    # Point #3 and #4 of rfc4791-9.7.2
    for child in filter_:
        if ns == "C" and child.tag == xmlutils.make_clark("C:time-range"):
            if not time_range_match(vobject_item, child, name, None):
                return False
        elif child.tag == xmlutils.make_clark("%s:text-match" % ns):
            if not text_match(vobject_item, child, name, ns):
                return False
        elif child.tag == xmlutils.make_clark("%s:param-filter" % ns):
            if not param_filter_match(vobject_item, child, name, ns):
                return False
        else:
            raise ValueError("Unexpected %r in prop-filter" % child.tag)
    return True


def time_range_match(vobject_item: vobject.base.Component,
                     filter_: ET.Element, child_name: str, trigger: TRIGGER) -> bool:
    """Check whether the component/property ``child_name`` of
       ``vobject_item`` matches the time-range ``filter_``."""
    # supporting since 3.5.4 now optional trigger (either absolute or relative offset)

    if not filter_.get("start") and not filter_.get("end"):
        return False

    start, end = parse_time_range(filter_)
    matched = False

    def range_fn(range_start: datetime, range_end: datetime,
                 is_recurrence: bool) -> bool:
        nonlocal matched
        if trigger:
            # if trigger is given, only check range_start
            if isinstance(trigger, timedelta):
                # trigger is a offset, apply to range_start
                if start < range_start + trigger and range_start + trigger < end:
                    matched = True
                    return True
                else:
                    return False
            elif isinstance(trigger, datetime):
                # trigger is absolute, use instead of range_start
                if start < trigger and trigger < end:
                    matched = True
                    return True
                else:
                    return False
            else:
                logger.warning("item/filter/time_range_match/range_fn: unsupported data format of provided trigger=%r", trigger)
                return True
        if start < range_end and range_start < end:
            matched = True
            return True
        if end < range_start and not is_recurrence:
            return True
        return False

    def infinity_fn(start: datetime) -> bool:
        return False

    logger.debug("TRACE/ITEM/FILTER/time_range_match: start=(%s) end=(%s) child_name=%s", start, end, child_name)
    visit_time_ranges(vobject_item, child_name, range_fn, infinity_fn)
    return matched


def time_range_fill(vobject_item: vobject.base.Component,
                    filter_: ET.Element, child_name: str, n: int = 1
                    ) -> List[Tuple[datetime, datetime]]:
    """Create a list of ``n`` occurances from the component/property ``child_name``
       of ``vobject_item``."""
    if not filter_.get("start") and not filter_.get("end"):
        return []

    start, end = parse_time_range(filter_)
    ranges: List[Tuple[datetime, datetime]] = []

    def range_fn(range_start: datetime, range_end: datetime,
                 is_recurrence: bool) -> bool:
        nonlocal ranges
        if start < range_end and range_start < end:
            ranges.append((range_start, range_end))
            if n > 0 and len(ranges) >= n:
                return True
        if end < range_start and not is_recurrence:
            return True
        return False

    def infinity_fn(range_start: datetime) -> bool:
        return False

    visit_time_ranges(vobject_item, child_name, range_fn, infinity_fn)
    return ranges


def visit_time_ranges(vobject_item: vobject.base.Component, child_name: str,
                      range_fn: Callable[[datetime, datetime, bool], bool],
                      infinity_fn: Callable[[datetime], bool]) -> None:
    """Visit all time ranges in the component/property ``child_name`` of
    `vobject_item`` with visitors ``range_fn`` and ``infinity_fn``.

    ``range_fn`` gets called for every time_range with ``start`` and ``end``
    datetimes and ``is_recurrence`` as arguments. If the function returns True,
    the operation is cancelled.

    ``infinity_fn`` gets called when an infinite recurrence rule is detected
    with ``start`` datetime as argument. If the function returns True, the
    operation is cancelled.

    See rfc4791-9.9.

    """

    # RFC 5545 §3.8.4.4: RECURRENCE-ID with RANGE parameter
    # - Default (no RANGE): Override affects only this single instance
    # - RANGE=THISANDFUTURE: Override affects this instance and all future instances
    # - RANGE=THISANDPRIOR: Deprecated, must not be generated

    logger.debug("TRACE/ITEM/FILTER/visit_time_ranges: child_name=%s", child_name)

    def getrruleset(child: vobject.base.Component, ignore: Sequence[date],
                    thisandfuture_cutoff: Optional[date] = None
                    ) -> Tuple[Iterable[date], bool]:
        """Get filtered recurrence dates, excluding overridden instances.

        Args:
            child: The component with RRULE
            ignore: Specific dates to skip (single-instance overrides)
            thisandfuture_cutoff: If set, skip all dates >= this date
                                  (for RANGE=THISANDFUTURE support)
        """
        infinite = False
        for rrule in child.contents.get("rrule", []):
            if (";UNTIL=" not in rrule.value.upper() and
                    ";COUNT=" not in rrule.value.upper()):
                infinite = True
                break
        if infinite:
            for dtstart in child.getrruleset(addRDate=True):
                if dtstart in ignore:
                    continue
                # RFC 5545 §3.8.4.4: RANGE=THISANDFUTURE cuts off at that date
                if thisandfuture_cutoff is not None and dtstart >= thisandfuture_cutoff:
                    continue
                if infinity_fn(date_to_datetime(dtstart)):
                    return (), True
                break

        def should_include(dtstart: date) -> bool:
            if dtstart in ignore:
                return False
            # RFC 5545 §3.8.4.4: RANGE=THISANDFUTURE cuts off at that date
            if thisandfuture_cutoff is not None and dtstart >= thisandfuture_cutoff:
                return False
            return True

        return filter(should_include, child.getrruleset(addRDate=True)), False

    def get_children(components: Iterable[vobject.base.Component]) -> Iterator[
            Tuple[vobject.base.Component, bool, List[date], Optional[date]]]:
        """Separate main component from recurrence overrides.

        Returns tuples of (component, is_recurrence, ignore_dates, thisandfuture_cutoff)
        where thisandfuture_cutoff is set if any override has RANGE=THISANDFUTURE.
        """
        main = None
        rec_main = None
        recurrences: List[date] = []
        thisandfuture_cutoff: Optional[date] = None

        for comp in components:
            if hasattr(comp, "recurrence_id") and comp.recurrence_id.value:
                recurrence_date = comp.recurrence_id.value
                # Check for RANGE parameter (RFC 5545 §3.8.4.4)
                range_param = comp.recurrence_id.params.get("RANGE", [])
                if range_param and range_param[0].upper() == "THISANDFUTURE":
                    # RANGE=THISANDFUTURE: This and all future instances are overridden
                    logger.debug("RECURRENCE-ID with RANGE=THISANDFUTURE: %s", recurrence_date)
                    if thisandfuture_cutoff is None or recurrence_date < thisandfuture_cutoff:
                        thisandfuture_cutoff = recurrence_date
                else:
                    # Default: Only this single instance is overridden
                    recurrences.append(recurrence_date)

                if comp.rruleset:
                    if comp.rruleset._len is None:
                        logger.warning("Ignore empty RRULESET in item at RECURRENCE-ID with value '%s' and UID '%s'", comp.recurrence_id.value, comp.uid.value)
                    else:
                        # Prevent possible infinite loop
                        raise ValueError("Overwritten recurrence with RRULESET")
                rec_main = comp
                yield comp, True, [], None
            else:
                if main is not None:
                    raise ValueError("Multiple main components. Got comp: {}".format(comp))
                main = comp
        if main is None and len(recurrences) == 1:
            main = rec_main
        if main is None:
            raise ValueError("Main component missing")
        yield main, False, recurrences, thisandfuture_cutoff

    # Comments give the lines in the tables of the specification
    if child_name == "VEVENT":
        for child, is_recurrence, recurrences, thisandfuture_cutoff in get_children(
                vobject_item.vevent_list):
            # TODO: check if there's a timezone
            try:
                dtstart = child.dtstart.value
            except AttributeError:
                raise AttributeError("missing DTSTART")

            if child.rruleset:
                dtstarts, infinity = getrruleset(child, recurrences, thisandfuture_cutoff)
                if infinity:
                    return
            else:
                dtstarts = (dtstart,)

            dtend = getattr(child, "dtend", None)
            if dtend is not None:
                dtend = dtend.value

                # Ensure that both datetime.datetime objects have a timezone or
                # both do not have one before doing calculations. This is required
                # as the library does not support performing mathematical operations
                # on timezone-aware and timezone-naive objects. See #1847
                if hasattr(dtstart, 'tzinfo') and hasattr(dtend, 'tzinfo'):
                    if dtstart.tzinfo is None and dtend.tzinfo is not None:
                        dtstart_orig = dtstart
                        dtstart = date_to_datetime(dtstart, dtend.astimezone().tzinfo)
                        logger.debug("TRACE/ITEM/FILTER/get_children: overtake missing tzinfo on dtstart from dtend: '%s' -> '%s'", dtstart_orig, dtstart)
                    elif dtstart.tzinfo is not None and dtend.tzinfo is None:
                        dtend_orig = dtend
                        dtend = date_to_datetime(dtend, dtstart.astimezone().tzinfo)
                        logger.debug("TRACE/ITEM/FILTER/get_children: overtake missing tzinfo on dtend from dtstart: '%s' -> '%s'", dtend_orig, dtend)

                original_duration = (dtend - dtstart).total_seconds()
                dtend = date_to_datetime(dtend)

            duration = getattr(child, "duration", None)
            if duration is not None:
                original_duration = duration = duration.value

            for dtstart in dtstarts:
                dtstart_is_datetime = isinstance(dtstart, datetime)
                dtstart = date_to_datetime(dtstart)

                if dtend is not None:
                    # Line 1
                    dtend = dtstart + timedelta(seconds=original_duration)
                    if range_fn(dtstart, dtend, is_recurrence):
                        return
                elif duration is not None:
                    if original_duration is None:
                        original_duration = duration.seconds
                    if duration.seconds > 0:
                        # Line 2
                        if range_fn(dtstart, dtstart + duration,
                                    is_recurrence):
                            return
                    else:
                        # Line 3
                        if range_fn(dtstart, dtstart + SECOND, is_recurrence):
                            return
                elif dtstart_is_datetime:
                    # Line 4
                    if range_fn(dtstart, dtstart + SECOND, is_recurrence):
                        return
                else:
                    # Line 5
                    if range_fn(dtstart, dtstart + DAY, is_recurrence):
                        return

    elif child_name == "VTODO":
        for child, is_recurrence, recurrences, thisandfuture_cutoff in get_children(
                vobject_item.vtodo_list):
            dtstart = getattr(child, "dtstart", None)
            duration = getattr(child, "duration", None)
            due = getattr(child, "due", None)
            completed = getattr(child, "completed", None)
            created = getattr(child, "created", None)

            if dtstart is not None:
                dtstart = date_to_datetime(dtstart.value)
            if duration is not None:
                duration = duration.value
            if due is not None:
                due = date_to_datetime(due.value)
                if dtstart is not None:
                    original_duration = (due - dtstart).total_seconds()
            if completed is not None:
                completed = date_to_datetime(completed.value)
                if created is not None:
                    created = date_to_datetime(created.value)
                    original_duration = (completed - created).total_seconds()
            elif created is not None:
                created = date_to_datetime(created.value)

            if child.rruleset:
                reference_dates, infinity = getrruleset(child, recurrences, thisandfuture_cutoff)
                if infinity:
                    return
            else:
                if dtstart is not None:
                    reference_dates = (dtstart,)
                elif due is not None:
                    reference_dates = (due,)
                elif completed is not None:
                    reference_dates = (completed,)
                elif created is not None:
                    reference_dates = (created,)
                else:
                    # Line 8
                    if range_fn(DATETIME_MIN, DATETIME_MAX, is_recurrence):
                        return
                    reference_dates = ()

            for reference_date in reference_dates:
                reference_date = date_to_datetime(reference_date)

                if dtstart is not None and duration is not None:
                    # Line 1
                    if range_fn(reference_date,
                                reference_date + duration + SECOND,
                                is_recurrence):
                        return
                    if range_fn(reference_date + duration - SECOND,
                                reference_date + duration + SECOND,
                                is_recurrence):
                        return
                elif dtstart is not None and due is not None:
                    # Line 2
                    due = reference_date + timedelta(seconds=original_duration)
                    if (range_fn(reference_date, due, is_recurrence) or
                            range_fn(reference_date,
                                     reference_date + SECOND, is_recurrence) or
                            range_fn(due - SECOND, due, is_recurrence) or
                            range_fn(due - SECOND, reference_date + SECOND,
                                     is_recurrence)):
                        return
                elif dtstart is not None:
                    if range_fn(reference_date, reference_date + SECOND,
                                is_recurrence):
                        return
                elif due is not None:
                    # Line 4
                    if range_fn(reference_date - SECOND, reference_date,
                                is_recurrence):
                        return
                elif completed is not None and created is not None:
                    # Line 5
                    completed = reference_date + timedelta(
                        seconds=original_duration)
                    if (range_fn(reference_date - SECOND,
                                 reference_date + SECOND,
                                 is_recurrence) or
                            range_fn(completed - SECOND, completed + SECOND,
                                     is_recurrence) or
                            range_fn(reference_date - SECOND,
                                     reference_date + SECOND, is_recurrence) or
                            range_fn(completed - SECOND, completed + SECOND,
                                     is_recurrence)):
                        return
                elif completed is not None:
                    # Line 6
                    if range_fn(reference_date - SECOND,
                                reference_date + SECOND, is_recurrence):
                        return
                elif created is not None:
                    # Line 7
                    if range_fn(reference_date, DATETIME_MAX, is_recurrence):
                        return

    elif child_name == "VJOURNAL":
        for child, is_recurrence, recurrences, thisandfuture_cutoff in get_children(
                vobject_item.vjournal_list):
            dtstart = getattr(child, "dtstart", None)

            if dtstart is not None:
                dtstart = dtstart.value
                if child.rruleset:
                    dtstarts, infinity = getrruleset(child, recurrences, thisandfuture_cutoff)
                    if infinity:
                        return
                else:
                    dtstarts = (dtstart,)

                for dtstart in dtstarts:
                    dtstart_is_datetime = isinstance(dtstart, datetime)
                    dtstart = date_to_datetime(dtstart)

                    if dtstart_is_datetime:
                        # Line 1
                        if range_fn(dtstart, dtstart + SECOND, is_recurrence):
                            return
                    else:
                        # Line 2
                        if range_fn(dtstart, dtstart + DAY, is_recurrence):
                            return

    elif child_name == "VAVAILABILITY":
        # RFC 7953: VAVAILABILITY component for calendar availability
        # RFC 7953 §3.1: VAVAILABILITY MAY contain RRULE, RDATE, EXDATE properties
        # Get VAVAILABILITY components - use getattr since vobject may not have vavailability_list
        vavail_list = getattr(vobject_item, 'vavailability_list', [])
        for child in vavail_list:
            dtstart = getattr(child, "dtstart", None)
            dtend = getattr(child, "dtend", None)
            duration = getattr(child, "duration", None)

            if dtstart is not None:
                dtstart_value = dtstart.value
                start = date_to_datetime(dtstart_value)

                # Calculate duration for recurrence expansion
                if dtend is not None:
                    end_value = date_to_datetime(dtend.value)
                    avail_duration = end_value - start
                elif duration is not None:
                    avail_duration = duration.value
                else:
                    # No end - availability extends indefinitely
                    avail_duration = None

                # RFC 7953 §3.1: Support RRULE for recurring availability
                # vobject doesn't implement getrruleset for VAVAILABILITY components,
                # so we need to parse RRULE manually using dateutil
                has_rrule = 'rrule' in child.contents
                if has_rrule:
                    rrule_str = child.rrule.value
                    # Check if RRULE is infinite (no COUNT or UNTIL)
                    is_infinite = (';UNTIL=' not in rrule_str.upper() and
                                   ';COUNT=' not in rrule_str.upper())

                    # Parse RRULE with dateutil
                    # Make dtstart timezone-naive for dateutil compatibility
                    dtstart_naive = dtstart_value
                    if isinstance(dtstart_naive, datetime) and dtstart_naive.tzinfo:
                        dtstart_naive = dtstart_naive.replace(tzinfo=None)
                    rule = dateutil_rrule.rrulestr(rrule_str, dtstart=dtstart_naive)
                    dtstarts: Iterable[date] = rule

                    if is_infinite:
                        # Infinite recurrence - call infinity_fn to set time range
                        # (used by find_time_range for prefiltering)
                        # After calling infinity_fn, continue to iterate occurrences
                        if infinity_fn(start):
                            return
                else:
                    dtstarts = (dtstart_value,)

                for recur_start in dtstarts:
                    recur_start = date_to_datetime(recur_start)
                    if avail_duration:
                        recur_end = recur_start + avail_duration
                    else:
                        recur_end = DATETIME_MAX

                    if range_fn(recur_start, recur_end, has_rrule):
                        return
            else:
                # No DTSTART means availability applies indefinitely
                start = DATETIME_MIN
                if dtend is not None:
                    end = date_to_datetime(dtend.value)
                else:
                    end = DATETIME_MAX
                if range_fn(start, end, False):
                    return

    elif child_name == "VFREEBUSY":
        # RFC 5545 §3.6.4: VFREEBUSY component for free/busy time
        # RFC 4791 §9.9: Time-range matching for VFREEBUSY
        vfb_list = getattr(vobject_item, 'vfreebusy_list', [])
        for child in vfb_list:
            # First check DTSTART/DTEND if present
            dtstart = getattr(child, "dtstart", None)
            dtend = getattr(child, "dtend", None)

            if dtstart is not None and dtend is not None:
                # Use DTSTART/DTEND as the time range
                start = date_to_datetime(dtstart.value)
                end = date_to_datetime(dtend.value)
                if range_fn(start, end, False):
                    return
            else:
                # Check individual FREEBUSY periods
                # FREEBUSY property contains period values (start/end or start/duration)
                freebusy_list = child.contents.get("freebusy", [])
                for fb_prop in freebusy_list:
                    # Each FREEBUSY value can be a list of periods
                    periods = fb_prop.value if isinstance(fb_prop.value, list) else [fb_prop.value]
                    for period in periods:
                        if hasattr(period, 'start') and hasattr(period, 'end'):
                            # Period with start and end
                            start = date_to_datetime(period.start)
                            end = date_to_datetime(period.end)
                        elif hasattr(period, '__iter__') and len(period) == 2:
                            # Tuple of (start, end) or (start, duration)
                            start = date_to_datetime(period[0])
                            if isinstance(period[1], timedelta):
                                end = start + period[1]
                            else:
                                end = date_to_datetime(period[1])
                        else:
                            continue
                        if range_fn(start, end, False):
                            return

    else:
        # Match a property
        logger.debug("TRACE/ITEM/FILTER/get_children: child_name=%s property match", child_name)
        child = getattr(vobject_item, child_name.lower())
        if isinstance(child.value, date):
            child_is_datetime = isinstance(child.value, datetime)
            child = date_to_datetime(child.value)
            if child_is_datetime:
                range_fn(child, child + SECOND, False)
            else:
                range_fn(child, child + DAY, False)


def _apply_collation(text: str, collation: str) -> str:
    """Apply collation transformation to text for comparison.

    RFC 4790 defines collation identifiers for text comparison:
    - i;ascii-casemap: ASCII case-insensitive comparison (default)
    - i;octet: Byte-by-byte comparison (case-sensitive)
    - i;unicode-casemap: Unicode case-insensitive comparison

    """
    if collation == "i;octet":
        # Case-sensitive byte comparison - no transformation
        return text
    elif collation == "i;unicode-casemap":
        # Unicode case-insensitive: use casefold() for proper Unicode handling
        # casefold() handles special cases like German ß -> ss
        return text.casefold()
    else:
        # Default: i;ascii-casemap - simple lowercase for ASCII
        # Also handles unknown collations gracefully
        return text.lower()


def text_match(vobject_item: vobject.base.Component,
               filter_: ET.Element, child_name: str, ns: str,
               attrib_name: Optional[str] = None) -> bool:
    """Check whether the ``item`` matches the text-match ``filter_``.

    See RFC 4791 §9.7.5 (CalDAV) and RFC 6352 §10.5.4 (CardDAV).

    Supports collations per RFC 4790:
    - i;ascii-casemap (default): ASCII case-insensitive
    - i;octet: Case-sensitive byte comparison
    - i;unicode-casemap: Unicode case-insensitive (recommended)

    """
    # Get collation attribute (default: i;ascii-casemap per RFC 4791 §7.5)
    collation = filter_.get("collation", "i;ascii-casemap")

    # Apply collation to filter text
    raw_text = next(filter_.itertext())
    text = _apply_collation(raw_text, collation)

    # Get match-type (supported by both CalDAV and CardDAV)
    # RFC 4791 §9.7.5 and RFC 6352 §10.5.4 both define match-type
    match_type = filter_.get("match-type", "contains")

    def match(value: str) -> bool:
        # Apply same collation to value being compared
        value = _apply_collation(value, collation)
        if match_type == "equals":
            return value == text
        if match_type == "contains":
            return text in value
        if match_type == "starts-with":
            return value.startswith(text)
        if match_type == "ends-with":
            return value.endswith(text)
        raise ValueError("Unexpected text-match match-type: %r" % match_type)

    children = getattr(vobject_item, "%s_list" % child_name, [])
    if attrib_name is not None:
        condition = any(
            match(attrib) for child in children
            for attrib in child.params.get(attrib_name, []))
    else:
        res = []
        for child in children:
            # Some filters such as CATEGORIES provide a list in child.value
            if type(child.value) is list:
                for value in child.value:
                    res.append(match(value))
            else:
                res.append(match(child.value))
        condition = any(res)
    if filter_.get("negate-condition") == "yes":
        return not condition
    return condition


def param_filter_match(vobject_item: vobject.base.Component,
                       filter_: ET.Element, parent_name: str, ns: str) -> bool:
    """Check whether the ``item`` matches the param-filter ``filter_``.

    See rfc4791-9.7.3.

    """
    name = filter_.get("name", "").upper()
    children = getattr(vobject_item, "%s_list" % parent_name, [])
    condition = any(name in child.params for child in children)
    if len(filter_) > 0:
        if filter_[0].tag == xmlutils.make_clark("%s:text-match" % ns):
            return condition and text_match(
                vobject_item, filter_[0], parent_name, ns, name)
        if filter_[0].tag == xmlutils.make_clark("%s:is-not-defined" % ns):
            return not condition
    return condition


def simplify_prefilters(filters: Iterable[ET.Element], collection_tag: str
                        ) -> Tuple[Optional[str], int, int, bool]:
    """Creates a simplified condition from ``filters``.

    Returns a tuple (``tag``, ``start``, ``end``, ``simple``) where ``tag`` is
    a string or None (match all) and ``start`` and ``end`` are POSIX
    timestamps (as int). ``simple`` is a bool that indicates that ``filters``
    and the simplified condition are identical.

    When multiple filters target different component types (e.g., VEVENT and
    VFREEBUSY), returns tag=None to disable component type filtering, ensuring
    all items are considered.

    """
    flat_filters = list(chain.from_iterable(filters))
    simple = len(flat_filters) <= 1
    logger.debug("TRACE/ITEM/FILTER/simplify_prefilters: collection_tag=%s", collection_tag)

    # First pass: collect all unique component tags and time ranges
    # This ensures we don't miss items when multiple component types are queried
    found_tags: List[str] = []
    found_ranges: List[Tuple[int, int]] = []

    for col_filter in flat_filters:
        if collection_tag != "VCALENDAR":
            simple = False
            break
        if (col_filter.tag != xmlutils.make_clark("C:comp-filter") or
                col_filter.get("name", "").upper() != "VCALENDAR"):
            simple = False
            continue
        simple &= len(col_filter) <= 1
        for comp_filter in col_filter:
            logger.debug("TRACE/ITEM/FILTER/simplify_prefilters: filter.tag=%s simple=%s", comp_filter.tag, simple)
            if comp_filter.tag == xmlutils.make_clark("C:time-range") and simple is True:
                # time-filter found on level 0
                start, end = time_range_timestamps(comp_filter)
                logger.debug("TRACE/ITEM/FILTER/simplify_prefilters: found time-filter on level 0 start=%r(%d) end=%r(%d) simple=%s", format_ut(start), start, format_ut(end), end, simple)
                found_ranges.append((start, end))
                continue
            if comp_filter.tag != xmlutils.make_clark("C:comp-filter"):
                logger.debug("TRACE/ITEM/FILTER/simplify_prefilters: no comp-filter on level 0")
                simple = False
                continue
            tag = comp_filter.get("name", "").upper()
            if comp_filter.find(
                    xmlutils.make_clark("C:is-not-defined")) is not None:
                simple = False
                continue
            if tag not in found_tags:
                found_tags.append(tag)
            simple &= len(comp_filter) <= 1
            for time_filter in comp_filter:
                if tag not in ("VTODO", "VEVENT", "VJOURNAL", "VFREEBUSY", "VAVAILABILITY"):
                    simple = False
                    break
                if time_filter.tag != xmlutils.make_clark("C:time-range"):
                    simple = False
                    continue
                start, end = time_range_timestamps(time_filter)
                logger.debug("TRACE/ITEM/FILTER/simplify_prefilters: found time-filter on level 1 tag=%s start=%d end=%d simple=%s", tag, start, end, simple)
                found_ranges.append((start, end))

    # Determine combined time range (union of all ranges)
    if found_ranges:
        combined_start = min(r[0] for r in found_ranges)
        combined_end = max(r[1] for r in found_ranges)
    else:
        combined_start = TIMESTAMP_MIN
        combined_end = TIMESTAMP_MAX

    # Determine result tag
    # If multiple different component types are being queried, return None
    # to disable tag-based filtering and let all items through
    if len(found_tags) == 0:
        # No specific component filter, e.g., time-range on VCALENDAR level
        result_tag = None
    elif len(found_tags) == 1:
        # Single component type
        result_tag = found_tags[0]
    else:
        # Multiple different component types (e.g., VEVENT + VFREEBUSY)
        # Return None to disable tag filtering, ensuring all types are considered
        logger.debug("TRACE/ITEM/FILTER/simplify_prefilters: multiple component types %s, disabling tag filter", found_tags)
        result_tag = None
        simple = False

    logger.debug("TRACE/ITEM/FILTER/simplify_prefilters: result tag=%s start=%d end=%d simple=%s", result_tag, combined_start, combined_end, simple)
    return result_tag, combined_start, combined_end, simple

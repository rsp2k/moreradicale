# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025-2025 RFC 6638 Scheduling Implementation
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
iTIP message validation per RFC 5546.

This module validates iTIP messages to ensure they conform to
RFC 5546 (iCalendar Transport-Independent Interoperability Protocol).
"""

from typing import Optional

import vobject

from moreradicale.itip.models import ITIPMethod, ITIPMessage, ITIPAttendee, AttendeePartStat


class ITIPValidationError(ValueError):
    """Raised when an iTIP message fails validation."""
    pass


def validate_itip_message(vcal: vobject.base.Component) -> None:
    """Validate iTIP message structure per RFC 5546 Section 3.

    Args:
        vcal: vobject VCALENDAR component

    Raises:
        ITIPValidationError: If validation fails
    """
    # RFC 5546 Section 3.1: METHOD property required at VCALENDAR level
    if not hasattr(vcal, 'method'):
        raise ITIPValidationError("iTIP message missing METHOD property")

    try:
        method = ITIPMethod(vcal.method.value.upper())
    except ValueError:
        raise ITIPValidationError(
            f"Unsupported iTIP method: {vcal.method.value}")

    # Check for VFREEBUSY first - it has different validation rules
    if hasattr(vcal, 'vfreebusy'):
        vfreebusy = vcal.vfreebusy
        # VFREEBUSY requires DTSTAMP
        if not hasattr(vfreebusy, 'dtstamp'):
            raise ITIPValidationError("VFREEBUSY missing required DTSTAMP property")
        # VFREEBUSY REQUEST requires DTSTART, DTEND, ORGANIZER, ATTENDEE
        if method == ITIPMethod.REQUEST:
            if not hasattr(vfreebusy, 'dtstart') or not hasattr(vfreebusy, 'dtend'):
                raise ITIPValidationError(
                    "VFREEBUSY REQUEST requires DTSTART and DTEND properties")
            if not hasattr(vfreebusy, 'organizer'):
                raise ITIPValidationError(
                    "VFREEBUSY REQUEST requires ORGANIZER property")
            if not hasattr(vfreebusy, 'attendee'):
                raise ITIPValidationError(
                    "VFREEBUSY REQUEST requires at least one ATTENDEE property")
        return  # VFREEBUSY validation complete

    # Get the schedulable component (VEVENT, VTODO, or VJOURNAL)
    component = _get_schedulable_component(vcal)
    if not component:
        raise ITIPValidationError(
            "No schedulable component (VEVENT, VTODO, VJOURNAL) found")

    # RFC 5546 Section 3.2: UID and DTSTAMP required
    if not hasattr(component, 'uid'):
        raise ITIPValidationError("Missing required UID property")

    if not hasattr(component, 'dtstamp'):
        raise ITIPValidationError("Missing required DTSTAMP property")

    # Method-specific validation
    _validate_method_specific(method, component)


def _get_schedulable_component(vcal: vobject.base.Component
                               ) -> Optional[vobject.base.Component]:
    """Extract the schedulable component from VCALENDAR.

    Args:
        vcal: vobject VCALENDAR component

    Returns:
        First VEVENT, VTODO, or VJOURNAL component, or None
    """
    for comp_type in ('vevent', 'vtodo', 'vjournal'):
        if hasattr(vcal, comp_type):
            return getattr(vcal, comp_type)
    return None


def _validate_method_specific(method: ITIPMethod,
                              component: vobject.base.Component) -> None:
    """Perform method-specific validation.

    Args:
        method: iTIP method
        component: Schedulable component (VEVENT, etc.)

    Raises:
        ITIPValidationError: If validation fails
    """
    # RFC 5546 Section 3.2: ORGANIZER required for most methods
    if method in (ITIPMethod.REQUEST, ITIPMethod.CANCEL,
                  ITIPMethod.ADD, ITIPMethod.DECLINECOUNTER):
        if not hasattr(component, 'organizer'):
            raise ITIPValidationError(
                f"{method.value} requires ORGANIZER property")

    # RFC 5546 Section 3.2.1: REQUEST must have at least one ATTENDEE
    if method == ITIPMethod.REQUEST:
        if not hasattr(component, 'attendee'):
            raise ITIPValidationError(
                "REQUEST requires at least one ATTENDEE property")

    # RFC 5546 Section 3.2.2: REPLY validation
    if method == ITIPMethod.REPLY:
        if not hasattr(component, 'attendee'):
            raise ITIPValidationError(
                "REPLY requires ATTENDEE property with updated PARTSTAT")

        # REPLY should have exactly one attendee (the replier)
        attendee_list = component.attendee_list if hasattr(
            component, 'attendee_list') else [component.attendee]
        if len(attendee_list) > 1:
            raise ITIPValidationError(
                "REPLY should contain only the responding attendee")

    # RFC 5546 Section 3.2.4: COUNTER validation
    if method == ITIPMethod.COUNTER:
        if not hasattr(component, 'organizer'):
            raise ITIPValidationError("COUNTER requires ORGANIZER property")
        if not hasattr(component, 'attendee'):
            raise ITIPValidationError("COUNTER requires ATTENDEE property")

    # RFC 5546 Section 3.2.6: REFRESH validation
    if method == ITIPMethod.REFRESH:
        if not hasattr(component, 'attendee'):
            raise ITIPValidationError("REFRESH requires ATTENDEE property")
        # REFRESH should only have UID and minimal properties
        # ORGANIZER tells us who to request from
        if not hasattr(component, 'organizer'):
            raise ITIPValidationError("REFRESH requires ORGANIZER property")


def parse_itip_message(vcal: vobject.base.Component) -> ITIPMessage:
    """Parse vobject into ITIPMessage data model.

    Args:
        vcal: vobject VCALENDAR component

    Returns:
        Parsed ITIPMessage

    Raises:
        ITIPValidationError: If parsing fails
    """
    validate_itip_message(vcal)

    method = ITIPMethod(vcal.method.value.upper())
    component = _get_schedulable_component(vcal)

    # Extract core properties
    uid = component.uid.value
    sequence = getattr(component, 'sequence', None)
    sequence = int(sequence.value) if sequence else 0

    organizer = ""
    if hasattr(component, 'organizer'):
        organizer = component.organizer.value
        if organizer.startswith('mailto:'):
            organizer = organizer[7:]  # Strip mailto: prefix

    # Determine component type
    component_type = "VEVENT"
    for comp_type in ('vevent', 'vtodo', 'vjournal'):
        if hasattr(vcal, comp_type):
            component_type = comp_type.upper()
            break

    # Extract attendees
    attendees = []
    if hasattr(component, 'attendee_list'):
        attendee_list = component.attendee_list
    elif hasattr(component, 'attendee'):
        attendee_list = [component.attendee]
    else:
        attendee_list = []

    for att in attendee_list:
        email = att.value
        if email.startswith('mailto:'):
            email = email[7:]

        # Extract PARTSTAT parameter
        partstat_str = att.params.get('PARTSTAT', ['NEEDS-ACTION'])[0]
        try:
            partstat = AttendeePartStat(partstat_str.upper())
        except ValueError:
            partstat = AttendeePartStat.NEEDS_ACTION

        # Extract CN (common name)
        cn = att.params.get('CN', [None])[0]

        attendees.append(ITIPAttendee(
            email=email,
            partstat=partstat,
            cn=cn
        ))

    # Extract optional properties (common)
    summary = getattr(component, 'summary', None)
    summary = summary.value if summary else None

    dtstart = getattr(component, 'dtstart', None)
    dtstart = str(dtstart.value) if dtstart else None

    dtend = getattr(component, 'dtend', None)
    dtend = str(dtend.value) if dtend else None

    recurrence_id = getattr(component, 'recurrence_id', None)
    recurrence_id = str(recurrence_id.value) if recurrence_id else None

    # VTODO-specific properties
    due = None
    completed = None
    percent_complete = None

    if component_type == "VTODO":
        # DUE is like DTEND for tasks
        due_prop = getattr(component, 'due', None)
        due = str(due_prop.value) if due_prop else None

        # COMPLETED timestamp
        completed_prop = getattr(component, 'completed', None)
        completed = str(completed_prop.value) if completed_prop else None

        # PERCENT-COMPLETE (0-100)
        pct_prop = getattr(component, 'percent_complete', None)
        if pct_prop:
            try:
                percent_complete = int(pct_prop.value)
            except (ValueError, TypeError):
                percent_complete = None

    return ITIPMessage(
        method=method,
        uid=uid,
        sequence=sequence,
        organizer=organizer,
        attendees=attendees,
        component_type=component_type,
        icalendar_text=vcal.serialize(),
        summary=summary,
        dtstart=dtstart,
        dtend=dtend,
        recurrence_id=recurrence_id,
        due=due,
        completed=completed,
        percent_complete=percent_complete
    )


def needs_scheduling(vcal_text: str) -> bool:
    """
    Determine if a calendar object needs scheduling processing.

    An object needs scheduling if it has both ORGANIZER and ATTENDEE properties.

    Args:
        vcal_text: iCalendar text

    Returns:
        True if object needs scheduling
    """
    try:
        vcal = vobject.readOne(vcal_text)

        # Get schedulable component
        component = None
        for comp_type in ('vevent', 'vtodo', 'vjournal'):
            if hasattr(vcal, comp_type):
                component = getattr(vcal, comp_type)
                break

        if not component:
            return False

        # Needs scheduling if it has both organizer and attendees
        has_organizer = hasattr(component, 'organizer')
        has_attendees = hasattr(component, 'attendee')

        return has_organizer and has_attendees

    except Exception:
        return False

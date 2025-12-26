"""
iTIP message validation (RFC 5546 Section 3).
"""

import vobject
from radicale.itip.models import ITIPMethod


def validate_itip_message(vcal: vobject.base.Component) -> None:
    """
    Validate iTIP message structure per RFC 5546 Section 3.
    
    Args:
        vcal: vObject VCALENDAR component
        
    Raises:
        ValueError: If message is invalid
    """
    # METHOD must exist at VCALENDAR level for iTIP
    if not hasattr(vcal, 'method'):
        raise ValueError("iTIP message missing METHOD property")
    
    try:
        method = ITIPMethod(vcal.method.value.upper())
    except ValueError:
        raise ValueError(f"Invalid iTIP METHOD: {vcal.method.value}")
    
    # Get schedulable component (VEVENT, VTODO, VJOURNAL)
    component = None
    for comp_type in ('vevent', 'vtodo', 'vjournal'):
        if hasattr(vcal, comp_type):
            component = getattr(vcal, comp_type)
            break
    
    if not component:
        raise ValueError("No schedulable component (VEVENT/VTODO/VJOURNAL) found")
    
    # METHOD-specific validation per RFC 5546 Section 3.2
    if method in (ITIPMethod.REQUEST, ITIPMethod.CANCEL):
        if not hasattr(component, 'organizer'):
            raise ValueError(f"{method.value} requires ORGANIZER property")
    
    if method == ITIPMethod.REQUEST:
        if not hasattr(component, 'attendee'):
            raise ValueError("REQUEST requires at least one ATTENDEE")
    
    if method == ITIPMethod.REPLY:
        if not hasattr(component, 'attendee'):
            raise ValueError("REPLY requires ATTENDEE property")
    
    # UID and DTSTAMP required for all methods
    if not hasattr(component, 'uid'):
        raise ValueError("Missing required UID property")
    
    if not hasattr(component, 'dtstamp'):
        raise ValueError("Missing required DTSTAMP property")


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

"""
iTIP message processor for implicit scheduling.
"""

import logging
import vobject
from typing import List, Optional
from radicale.itip.models import ITIPMethod, ITIPAttendee, ITIPMessage, AttendeePartStat
from radicale.itip.router import extract_email, route_attendee, get_inbox_path
from radicale.itip.validator import needs_scheduling
from radicale import item as radicale_item


logger = logging.getLogger(__name__)


class ITIPProcessor:
    """Processes iTIP messages for implicit scheduling."""
    
    def __init__(self, storage):
        """
        Initialize processor.
        
        Args:
            storage: Radicale storage backend
        """
        self.storage = storage
    
    def process_put(self, vcal_text: str, user: str, path: str) -> None:
        """
        Process a PUT request that may need scheduling.
        
        This is called when an event/todo/journal is created or updated.
        If it has attendees and an organizer, we generate and deliver iTIP messages.
        
        Args:
            vcal_text: iCalendar text being PUT
            user: User making the PUT request
            path: Path where item is being PUT
        """
        # Check if this needs scheduling
        if not needs_scheduling(vcal_text):
            logger.debug("Item doesn't need scheduling (no ORGANIZER+ATTENDEE)")
            return
        
        try:
            # Parse the calendar object
            vcal = vobject.readOne(vcal_text)
            
            # Get the component
            component = None
            comp_type_name = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    comp_type_name = comp_type.upper()
                    break
            
            if not component:
                logger.warning("No schedulable component found")
                return
            
            # Extract organizer
            if not hasattr(component, 'organizer'):
                logger.debug("No organizer, skipping scheduling")
                return
            
            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)
            
            if not organizer_email:
                logger.warning(f"Invalid organizer email: {organizer_uri}")
                return
            
            # Extract attendees
            if not hasattr(component, 'attendee'):
                logger.debug("No attendees, skipping scheduling")
                return
            
            attendees = component.attendee_list if hasattr(component, 'attendee_list') else [component.attendee]
            
            # Parse each attendee
            itip_attendees = []
            for att in attendees:
                att_email = extract_email(att.value)
                if not att_email:
                    continue
                
                # Get participation status
                partstat_str = att.params.get('PARTSTAT', ['NEEDS-ACTION'])[0]
                try:
                    partstat = AttendeePartStat(partstat_str)
                except ValueError:
                    partstat = AttendeePartStat.NEEDS_ACTION
                
                # Get common name
                cn = att.params.get('CN', [None])[0]
                
                # Get role
                role = att.params.get('ROLE', ['REQ-PARTICIPANT'])[0]
                
                # Get calendar user type
                cutype = att.params.get('CUTYPE', ['INDIVIDUAL'])[0]
                
                itip_attendee = ITIPAttendee(
                    email=att_email,
                    partstat=partstat,
                    cn=cn,
                    role=role,
                    cutype=cutype
                )
                
                # Check if internal
                is_internal, principal_path = route_attendee(att_email, self.storage)
                itip_attendee.is_internal = is_internal
                itip_attendee.principal_path = principal_path
                
                itip_attendees.append(itip_attendee)
            
            if not itip_attendees:
                logger.debug("No valid attendees found")
                return
            
            # Get UID and SEQUENCE
            uid = component.uid.value
            sequence = component.sequence.value if hasattr(component, 'sequence') else 0
            
            # Create iTIP message for REQUEST
            # For implicit scheduling, we generate REQUEST when organizer creates/updates event
            itip_msg = ITIPMessage(
                method=ITIPMethod.REQUEST,
                uid=uid,
                sequence=sequence,
                organizer=organizer_email,
                attendees=itip_attendees,
                component_type=comp_type_name,
                icalendar_text=self._generate_itip_request(vcal, component)
            )
            
            # Deliver to internal attendees
            self._deliver_internal(itip_msg)
            
            logger.info(f"Processed iTIP scheduling for {uid}: {len([a for a in itip_attendees if a.is_internal])} internal, {len([a for a in itip_attendees if not a.is_internal])} external")
            
        except Exception as e:
            logger.error(f"Error processing iTIP scheduling: {e}", exc_info=True)
    
    def _generate_itip_request(self, vcal: vobject.base.Component, component: vobject.base.Component) -> str:
        """
        Generate iTIP REQUEST message from calendar component.
        
        Args:
            vcal: Parent VCALENDAR
            component: VEVENT/VTODO/VJOURNAL component
            
        Returns:
            iCalendar text with METHOD:REQUEST
        """
        # Clone the calendar
        itip_vcal = vobject.newFromBehavior('VCALENDAR')
        itip_vcal.add('version').value = '2.0'
        itip_vcal.add('prodid').value = '-//Radicale//NONSGML Radicale Server//EN'
        itip_vcal.add('method').value = 'REQUEST'
        
        # Clone the component
        comp_type = component.name.lower()
        itip_comp = itip_vcal.add(comp_type)
        
        # Copy all properties
        for prop in component.getChildren():
            if prop.name.lower() not in ('method',):  # Skip METHOD at component level
                itip_comp.add(prop.name).value = prop.value
                # Copy parameters
                if hasattr(prop, 'params'):
                    for param_name, param_values in prop.params.items():
                        itip_comp.contents[prop.name.lower()][-1].params[param_name] = param_values
        
        return itip_vcal.serialize()
    
    def _deliver_internal(self, itip_msg: ITIPMessage) -> None:
        """
        Deliver iTIP message to internal attendees' schedule-inbox.
        
        Args:
            itip_msg: iTIP message to deliver
        """
        for attendee in itip_msg.attendees:
            if not attendee.is_internal or not attendee.principal_path:
                continue
            
            try:
                inbox_path = get_inbox_path(attendee.principal_path)

                # Generate filename: UID-SEQUENCE.ics
                filename = f"{itip_msg.uid}-{itip_msg.sequence}.ics"
                item_path = f"{inbox_path}{filename}"

                # Discover inbox collection (no lock needed - we're already in PUT handler's lock)
                discovered = list(self.storage.discover(inbox_path, depth="0"))
                if not discovered:
                    logger.warning(f"Schedule-inbox not found: {inbox_path}")
                    continue

                inbox = discovered[0]

                # Create item from iTIP message
                itip_vobject = vobject.readOne(itip_msg.icalendar_text)
                itip_item = radicale_item.Item(collection_path=inbox.path, vobject_item=itip_vobject)
                itip_item.prepare()

                # Upload iTIP message
                inbox.upload(filename, itip_item)

                logger.info(f"Delivered iTIP REQUEST to {attendee.email} inbox: {item_path}")

            except Exception as e:
                logger.error(f"Failed to deliver to {attendee.email}: {e}", exc_info=True)

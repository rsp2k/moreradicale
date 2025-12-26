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

    def process_delete(self, item, user: str) -> None:
        """
        Process deletion of an event that may need iTIP CANCEL.

        When an organizer deletes an event with attendees, we generate and deliver
        CANCEL messages to all attendees' inboxes.

        Args:
            item: Radicale Item being deleted
            user: User deleting the item
        """
        try:
            # Get the iCalendar data
            vcal_text = item.serialize()

            # Check if this needs scheduling
            if not needs_scheduling(vcal_text):
                logger.debug("Deleted item doesn't need scheduling (no ORGANIZER+ATTENDEE)")
                return

            # Parse the calendar object
            vcal = item.vobject_item

            # Get the component
            component = None
            comp_type_name = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    comp_type_name = comp_type.upper()
                    break

            if not component:
                logger.warning("No schedulable component found in deleted item")
                return

            # Extract organizer
            if not hasattr(component, 'organizer'):
                logger.debug("No organizer, skipping CANCEL")
                return

            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)

            if not organizer_email:
                logger.warning(f"Invalid organizer email: {organizer_uri}")
                return

            # Extract attendees
            if not hasattr(component, 'attendee'):
                logger.debug("No attendees, skipping CANCEL")
                return

            attendees = component.attendee_list if hasattr(component, 'attendee_list') else [component.attendee]

            # Parse each attendee
            itip_attendees = []
            for att in attendees:
                att_email = extract_email(att.value)
                if not att_email:
                    continue

                # Get participation status (from before deletion)
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
                logger.debug("No valid attendees found for CANCEL")
                return

            # Get UID and SEQUENCE
            uid = component.uid.value
            sequence = component.sequence.value if hasattr(component, 'sequence') else 0

            # Create iTIP CANCEL message
            itip_msg = ITIPMessage(
                method=ITIPMethod.CANCEL,
                uid=uid,
                sequence=sequence,
                organizer=organizer_email,
                attendees=itip_attendees,
                component_type=comp_type_name,
                icalendar_text=self._generate_itip_cancel(vcal, component)
            )

            # Deliver CANCEL to internal attendees
            self._deliver_internal(itip_msg)

            logger.info(f"Processed iTIP CANCEL for {uid}: {len([a for a in itip_attendees if a.is_internal])} internal, {len([a for a in itip_attendees if not a.is_internal])} external")

        except Exception as e:
            logger.error(f"Error processing iTIP CANCEL: {e}", exc_info=True)

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

    def _generate_itip_cancel(self, vcal: vobject.base.Component, component: vobject.base.Component) -> str:
        """
        Generate iTIP CANCEL message from calendar component.

        Args:
            vcal: Parent VCALENDAR
            component: VEVENT/VTODO/VJOURNAL component

        Returns:
            iCalendar text with METHOD:CANCEL
        """
        # Clone the calendar
        itip_vcal = vobject.newFromBehavior('VCALENDAR')
        itip_vcal.add('version').value = '2.0'
        itip_vcal.add('prodid').value = '-//Radicale//NONSGML Radicale Server//EN'
        itip_vcal.add('method').value = 'CANCEL'

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

                # Generate filename: UID-SEQUENCE-METHOD.ics
                # Include method to avoid CANCEL overwriting REQUEST
                method_str = itip_msg.method.value.lower()
                filename = f"{itip_msg.uid}-{itip_msg.sequence}-{method_str}.ics"
                item_path = f"{inbox_path}{filename}"

                # Discover inbox collection (no lock needed - we're already in DELETE/PUT handler's lock)
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

                logger.info(f"Delivered iTIP {itip_msg.method.value} to {attendee.email} inbox: {item_path}")

            except Exception as e:
                logger.error(f"Failed to deliver to {attendee.email}: {e}", exc_info=True)

    def process_outbox_post(self, user: str, ical_text: str, base_prefix: str):
        """
        Process iTIP REPLY message POSTed to schedule-outbox.

        This handles attendees responding to meeting invitations (ACCEPT/DECLINE/TENTATIVE).
        The REPLY is processed and the organizer's event is updated with the new PARTSTAT.

        Args:
            user: User posting the REPLY (attendee)
            ical_text: iTIP REPLY message
            base_prefix: Base URL prefix for responses

        Returns:
            HTTP response with schedule-response XML
        """
        from radicale import httputils, xmlutils
        import xml.etree.ElementTree as ET
        from radicale.itip.validator import validate_itip_message

        try:
            # Parse and validate iTIP message
            vcal = vobject.readOne(ical_text)
            validate_itip_message(vcal)

            # Verify it's a REPLY
            if not hasattr(vcal, 'method') or vcal.method.value != 'REPLY':
                logger.warning(f"Non-REPLY method posted to schedule-outbox: {vcal.method.value if hasattr(vcal, 'method') else 'NO METHOD'}")
                return httputils.BAD_REQUEST

            # Get the component (VEVENT/VTODO/VJOURNAL)
            component = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    break

            if not component:
                logger.warning("No schedulable component in REPLY")
                return httputils.BAD_REQUEST

            # Extract UID and ORGANIZER
            if not hasattr(component, 'uid'):
                logger.warning("REPLY missing UID")
                return httputils.BAD_REQUEST

            uid = component.uid.value

            if not hasattr(component, 'organizer'):
                logger.warning("REPLY missing ORGANIZER")
                return httputils.BAD_REQUEST

            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)

            if not organizer_email:
                logger.warning(f"Invalid ORGANIZER email: {organizer_uri}")
                return httputils.BAD_REQUEST

            # Extract ATTENDEE (should be the user posting the REPLY)
            if not hasattr(component, 'attendee'):
                logger.warning("REPLY missing ATTENDEE")
                return httputils.BAD_REQUEST

            attendee = component.attendee
            attendee_email = extract_email(attendee.value)
            new_partstat = attendee.params.get('PARTSTAT', ['NEEDS-ACTION'])[0]

            logger.info(f"Processing REPLY from {attendee_email} for {uid}: PARTSTAT={new_partstat}")

            # Route organizer (must be internal)
            is_internal, organizer_principal = route_attendee(organizer_email, self.storage)

            if not is_internal:
                logger.warning(f"REPLY for external organizer {organizer_email} - not supported yet")
                return self._build_schedule_response_error(
                    base_prefix, "External organizers not supported")

            # Find organizer's event (already in POST handler's lock context)
            event_found, event_path, event_collection = self._find_organizer_event(
                organizer_principal, uid)

            if not event_found:
                logger.warning(f"Organizer event not found for UID {uid}")
                return self._build_schedule_response_error(
                    base_prefix, "Event not found")

            # Update the PARTSTAT for this attendee
            updated = self._update_attendee_partstat(
                event_path, event_collection, attendee_email, new_partstat)

            if not updated:
                logger.warning(f"Failed to update PARTSTAT for {attendee_email} in {event_path}")
                return self._build_schedule_response_error(
                    base_prefix, "Failed to update event")

            logger.info(f"Updated PARTSTAT for {attendee_email} to {new_partstat} in {event_path}")

            # Return success schedule-response
            return self._build_schedule_response_success(base_prefix, attendee_email)

        except Exception as e:
            logger.error(f"Error processing REPLY: {e}", exc_info=True)
            return httputils.INTERNAL_SERVER_ERROR

    def _find_organizer_event(self, organizer_principal: str, uid: str):
        """
        Find the organizer's calendar event by UID.

        Args:
            organizer_principal: Organizer's principal path (e.g., /alice/)
            uid: Event UID

        Returns:
            Tuple of (found, item_path, collection)
        """
        # Discover all calendar collections under organizer's principal
        discovered = list(self.storage.discover(organizer_principal, depth="1"))
        logger.debug(f"Searching for UID {uid} in {organizer_principal}, found {len(discovered)} items")

        for item in discovered:
            logger.debug(f"Checking item: {item.path}, tag={getattr(item, 'tag', 'NO TAG')}")

            # Skip non-calendar collections
            if not hasattr(item, 'tag') or item.tag != 'VCALENDAR':
                continue

            # Search this calendar for the UID
            hrefs = list(item._list())
            logger.debug(f"Calendar {item.path} has {len(hrefs)} items")

            for href in hrefs:
                try:
                    calendar_item = item._get(href)
                    if calendar_item:
                        comp = calendar_item.vobject_item
                        # Check if this is the event we're looking for
                        for subcomp in comp.getChildren():
                            if subcomp.name in ('VEVENT', 'VTODO', 'VJOURNAL'):
                                item_uid = subcomp.uid.value if hasattr(subcomp, 'uid') else 'NO UID'
                                logger.debug(f"Found {subcomp.name} with UID={item_uid}")
                                if hasattr(subcomp, 'uid') and subcomp.uid.value == uid:
                                    # Ensure proper path separator
                                    path_sep = '' if item.path.endswith('/') else '/'
                                    item_path = f"{item.path}{path_sep}{href}"
                                    logger.info(f"Found organizer event: {item_path}")
                                    return True, item_path, item
                except Exception as e:
                    logger.warning(f"Error checking {item.path}{href}: {e}", exc_info=True)
                    continue

        logger.warning(f"UID {uid} not found in any calendar under {organizer_principal}")
        return False, None, None

    def _update_attendee_partstat(self, event_path: str, collection,
                                  attendee_email: str, new_partstat: str) -> bool:
        """
        Update the PARTSTAT of an attendee in an event.

        Args:
            event_path: Full path to event (e.g., /alice/calendar.ics/meeting.ics)
            collection: Calendar collection containing the event
            attendee_email: Email of attendee to update
            new_partstat: New participation status

        Returns:
            True if updated successfully
        """
        try:
            # Get the item
            href = event_path.split('/')[-1]
            item = collection._get(href)

            if not item:
                return False

            # Find and update the attendee
            vcal = item.vobject_item
            updated = False

            for subcomp in vcal.getChildren():
                if subcomp.name not in ('VEVENT', 'VTODO', 'VJOURNAL'):
                    continue

                # Find the attendee
                if hasattr(subcomp, 'attendee_list'):
                    attendees = subcomp.attendee_list
                else:
                    attendees = [subcomp.attendee] if hasattr(subcomp, 'attendee') else []

                for att in attendees:
                    att_email = extract_email(att.value)
                    if att_email and att_email.lower() == attendee_email.lower():
                        # Update PARTSTAT
                        att.params['PARTSTAT'] = [new_partstat]
                        updated = True
                        logger.debug(f"Updated PARTSTAT for {attendee_email} to {new_partstat}")
                        break

                if updated:
                    # Increment SEQUENCE
                    if hasattr(subcomp, 'sequence'):
                        subcomp.sequence.value = str(int(subcomp.sequence.value) + 1)
                    else:
                        subcomp.add('sequence').value = '1'
                    break

            if not updated:
                logger.warning(f"Attendee {attendee_email} not found in event")
                return False

            # Save the updated event
            updated_item = radicale_item.Item(
                collection_path=collection.path,
                vobject_item=vcal)
            updated_item.prepare()
            collection.upload(href, updated_item)

            return True

        except Exception as e:
            logger.error(f"Error updating PARTSTAT: {e}", exc_info=True)
            return False

    def _build_schedule_response_success(self, base_prefix: str, attendee_email: str):
        """Build successful RFC 6638 schedule-response."""
        from radicale import xmlutils
        from http import client
        import xml.etree.ElementTree as ET

        # Build schedule-response XML
        response = ET.Element(xmlutils.make_clark("C:schedule-response"))
        response_elem = ET.SubElement(response, xmlutils.make_clark("C:response"))

        # Recipient
        recipient = ET.SubElement(response_elem, xmlutils.make_clark("C:recipient"))
        href = ET.SubElement(recipient, xmlutils.make_clark("D:href"))
        href.text = f"mailto:{attendee_email}"

        # Request status (2.0 = Success)
        request_status = ET.SubElement(response_elem,
                                       xmlutils.make_clark("C:request-status"))
        request_status.text = "2.0;Success"

        # Response description
        response_desc = ET.SubElement(response_elem,
                                      xmlutils.make_clark("C:response-description"))
        response_desc.text = "REPLY processed successfully"

        headers = (
            ("Content-Type", "application/xml; charset=utf-8"),
        )

        return client.OK, headers, ET.tostring(response, encoding="utf-8"), None

    def _build_schedule_response_error(self, base_prefix: str, error_msg: str):
        """Build error RFC 6638 schedule-response."""
        from radicale import xmlutils
        from http import client
        import xml.etree.ElementTree as ET

        response = ET.Element(xmlutils.make_clark("C:schedule-response"))
        response_elem = ET.SubElement(response, xmlutils.make_clark("C:response"))

        # Request status (5.3 = No authority)
        request_status = ET.SubElement(response_elem,
                                       xmlutils.make_clark("C:request-status"))
        request_status.text = "5.3;No authority"

        # Error description
        response_desc = ET.SubElement(response_elem,
                                      xmlutils.make_clark("C:response-description"))
        response_desc.text = error_msg

        headers = (
            ("Content-Type", "application/xml; charset=utf-8"),
        )

        return client.OK, headers, ET.tostring(response, encoding="utf-8"), None

"""
iTIP message processor for implicit scheduling.
"""

import logging
import re
import vobject
from datetime import datetime
from typing import List, Optional
from radicale.itip.models import ITIPMethod, ITIPAttendee, ITIPMessage, AttendeePartStat
from radicale.itip.router import extract_email, route_attendee, get_inbox_path
from radicale.itip.validator import needs_scheduling
from radicale import item as radicale_item
from radicale import email_utils


logger = logging.getLogger(__name__)


class ITIPProcessor:
    """Processes iTIP messages for implicit scheduling."""

    def __init__(self, storage, configuration=None):
        """
        Initialize processor.

        Args:
            storage: Radicale storage backend
            configuration: Radicale configuration (optional, for email delivery)
        """
        self.storage = storage
        self.configuration = configuration
        self.email_config = None

        # Load email configuration if enabled
        if configuration:
            scheduling_enabled = configuration.get("scheduling", "enabled")
            email_enabled = configuration.get("scheduling", "email_enabled")

            if scheduling_enabled and email_enabled:
                try:
                    self.email_config = email_utils.load_email_config_from_radicale_config(configuration)
                    logger.info(f"Email delivery enabled for external attendees: {self.email_config}")
                except Exception as e:
                    logger.error(f"Failed to load email configuration: {e}")
                    logger.warning("Email delivery for external attendees will be disabled")
    
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

            # Deliver to external attendees via email
            try:
                self._deliver_external(itip_msg)
            except Exception as e:
                logger.error(f"External delivery failed: {e}", exc_info=True)

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

            # Deliver CANCEL to external attendees via email
            try:
                self._deliver_external(itip_msg)
            except Exception as e:
                logger.error(f"External CANCEL delivery failed: {e}", exc_info=True)

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

    def _deliver_external(self, itip_msg: ITIPMessage) -> None:
        """
        Deliver iTIP message to external attendees via RFC 6047 email.

        Sends calendar invitations, updates, cancellations, and counter-proposals
        to attendees who are not on this Radicale server.

        Args:
            itip_msg: iTIP message to deliver

        Note:
            Email failures are logged but do not raise exceptions to prevent
            blocking event creation when email delivery fails.
        """
        if not self.email_config:
            # Email not configured - skip external delivery
            return

        # Filter for external attendees only
        external_attendees = [a for a in itip_msg.attendees if not a.is_internal]

        if not external_attendees:
            logger.debug("No external attendees - skipping email delivery")
            return

        logger.info(f"Delivering iTIP {itip_msg.method.value} to {len(external_attendees)} external attendee(s)")

        for attendee in external_attendees:
            try:
                # Build email components
                subject = self._build_email_subject(itip_msg, attendee)
                body = self._build_email_body(itip_msg, attendee)
                from_email = self._get_from_email(itip_msg)

                # Send via SMTP
                success = email_utils.send_itip_email(
                    email_config=self.email_config,
                    from_email=from_email,
                    to_email=attendee.email,
                    subject=subject,
                    body_text=body,
                    icalendar_text=itip_msg.icalendar_text,
                    method=itip_msg.method.value
                )

                if success:
                    logger.info(f"Sent iTIP {itip_msg.method.value} email to {attendee.email}")
                else:
                    logger.warning(f"Failed to send iTIP {itip_msg.method.value} email to {attendee.email}")

            except Exception as e:
                # Log but don't block event creation
                logger.error(f"Email delivery to {attendee.email} failed: {e}", exc_info=True)

    def _build_email_subject(self, itip_msg: ITIPMessage, attendee: ITIPAttendee) -> str:
        """
        Build email subject line for iTIP message.

        Args:
            itip_msg: iTIP message
            attendee: Target attendee

        Returns:
            Email subject with optional prefix
        """
        # Extract event title
        summary = self._extract_field(itip_msg.icalendar_text, "SUMMARY") or "Calendar Event"

        # Get subject prefix from config
        prefix = ""
        if self.configuration:
            prefix = self.configuration.get("scheduling", "email_subject_prefix") or ""

        # Build subject based on method
        method = itip_msg.method.value

        if method == "REQUEST":
            subject = f"Invitation: {summary}"
        elif method == "CANCEL":
            subject = f"Cancelled: {summary}"
        elif method == "COUNTER":
            subject = f"Counter-proposal: {summary}"
        elif method == "DECLINECOUNTER":
            subject = f"Counter-proposal declined: {summary}"
        elif method == "REFRESH":
            subject = f"Refresh request: {summary}"
        else:
            subject = f"{method}: {summary}"

        return f"{prefix}{subject}"

    def _build_email_body(self, itip_msg: ITIPMessage, attendee: ITIPAttendee) -> str:
        """
        Build email body text from template with variable substitution.

        Renders the configured template for the iTIP method, replacing
        template variables with actual event data.

        Args:
            itip_msg: iTIP message
            attendee: Target attendee

        Returns:
            Rendered email body text
        """
        if not self.configuration:
            return "Please see attached calendar invitation."

        # Get template for this method
        method = itip_msg.method.value.lower()
        template_key = f"{method}_template"
        template = self.configuration.get("scheduling", template_key)

        if not template:
            # Fallback to generic message
            return f"Please see attached {method.upper()} message."

        # Extract event fields
        summary = self._extract_field(itip_msg.icalendar_text, "SUMMARY") or "No Title"
        location = self._extract_field(itip_msg.icalendar_text, "LOCATION") or "No Location"
        description = self._extract_field(itip_msg.icalendar_text, "DESCRIPTION") or ""
        dtstart = self._extract_field(itip_msg.icalendar_text, "DTSTART")
        dtend = self._extract_field(itip_msg.icalendar_text, "DTEND")
        organizer_raw = self._extract_field(itip_msg.icalendar_text, "ORGANIZER") or ""

        # Extract organizer name/email
        organizer_email = extract_email(organizer_raw) or "Unknown"
        organizer_cn_match = re.search(r'CN=([^:;]+)', organizer_raw)
        organizer_name = organizer_cn_match.group(1).strip('"') if organizer_cn_match else organizer_email

        # Extract attendee name
        attendee_name = attendee.cn or attendee.email

        # Format datetimes
        start_time = self._format_datetime(dtstart)
        end_time = self._format_datetime(dtend)

        # Build context for template substitution
        context = {
            "$event_title": summary,
            "$event_start_time": start_time,
            "$event_end_time": end_time,
            "$event_location": location,
            "$event_description": description,
            "$organizer_name": organizer_name,
            "$attendee_name": attendee_name
        }

        # Perform variable substitution
        body = template
        for key, value in context.items():
            body = body.replace(key, value)

        return body

    def _get_from_email(self, itip_msg: ITIPMessage) -> str:
        """
        Determine from address for email.

        Can send from organizer address (requires SPF/DKIM) or
        use configured from_email address.

        Args:
            itip_msg: iTIP message

        Returns:
            From email address
        """
        if not self.configuration:
            return self.email_config.from_email

        # Check if we should send from organizer
        smtp_from_organizer = self.configuration.get("scheduling", "smtp_from_organizer")

        if smtp_from_organizer:
            # Extract organizer email from iTIP message
            organizer_raw = self._extract_field(itip_msg.icalendar_text, "ORGANIZER")
            if organizer_raw:
                organizer_email = extract_email(organizer_raw)
                if organizer_email:
                    logger.debug(f"Sending from organizer address: {organizer_email}")
                    return organizer_email

        # Fallback to configured from_email
        return self.email_config.from_email

    def _extract_field(self, ical_text: str, field_name: str) -> Optional[str]:
        """
        Extract field value from iCalendar text.

        Handles RFC 5545 line folding (CRLF + space/tab continuation).

        Args:
            ical_text: iCalendar text
            field_name: Field name (e.g., "SUMMARY", "LOCATION")

        Returns:
            Field value or None if not found
        """
        # Unfold lines (RFC 5545: continuation lines start with space or tab)
        unfolded = ical_text.replace('\r\n ', '').replace('\r\n\t', '')
        unfolded = unfolded.replace('\n ', '').replace('\n\t', '')

        # Search for field (case-insensitive)
        pattern = rf'^{field_name}[:;](.*)$'
        match = re.search(pattern, unfolded, re.MULTILINE | re.IGNORECASE)

        if match:
            value = match.group(1)
            # Remove parameters (e.g., "SUMMARY;LANGUAGE=en:Title" -> "Title")
            if ':' in value:
                value = value.split(':', 1)[1]
            return value.strip()

        return None

    def _format_datetime(self, dt_value: Optional[str]) -> str:
        """
        Format iCalendar datetime to human-readable string.

        Args:
            dt_value: iCalendar datetime (e.g., "20250115T140000Z")

        Returns:
            Formatted string (e.g., "January 15, 2025 at 2:00 PM")
        """
        if not dt_value:
            return "No Time Specified"

        try:
            # Remove VALUE=DATE parameter if present
            if ':' in dt_value:
                dt_value = dt_value.split(':', 1)[1]

            # Parse different formats
            if 'T' in dt_value:
                # DateTime format: 20250115T140000Z or 20250115T140000
                dt_str = dt_value.replace('Z', '')
                if len(dt_str) == 15:  # YYYYMMDDTHHmmss
                    dt = datetime.strptime(dt_str, '%Y%m%dT%H%M%S')
                    return dt.strftime('%B %d, %Y at %I:%M %p')
            else:
                # Date-only format: 20250115
                if len(dt_value) == 8:  # YYYYMMDD
                    dt = datetime.strptime(dt_value, '%Y%m%d')
                    return dt.strftime('%B %d, %Y')

            # Fallback: return as-is
            return dt_value

        except Exception as e:
            logger.debug(f"Failed to parse datetime '{dt_value}': {e}")
            return dt_value

    def process_outbox_post(self, user: str, ical_text: str, base_prefix: str):
        """
        Process iTIP message POSTed to schedule-outbox.

        Handles:
        - REPLY: Attendees responding to invitations (ACCEPT/DECLINE/TENTATIVE)
        - REFRESH: Attendees requesting latest event version from organizer
        - COUNTER: Attendees proposing changes to event (time, location, etc.)
        - DECLINECOUNTER: Organizers declining counter-proposals

        Args:
            user: User posting the message (attendee or organizer)
            ical_text: iTIP message
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

            # Check METHOD
            if not hasattr(vcal, 'method'):
                logger.warning("iTIP message missing METHOD")
                return httputils.BAD_REQUEST

            method = vcal.method.value.upper()

            # Route to appropriate handler
            if method == 'REPLY':
                return self._process_reply(vcal, user, base_prefix)
            elif method == 'REFRESH':
                return self._process_refresh(vcal, user, base_prefix)
            elif method == 'COUNTER':
                return self._process_counter(vcal, user, base_prefix)
            elif method == 'DECLINECOUNTER':
                return self._process_declinecounter(vcal, user, base_prefix)
            else:
                logger.warning(f"Unsupported METHOD posted to schedule-outbox: {method}")
                return httputils.BAD_REQUEST

        except Exception as e:
            logger.error(f"Error processing iTIP message: {e}", exc_info=True)
            return self._build_schedule_response_error(base_prefix, str(e))

    def _process_reply(self, vcal: vobject.base.Component, user: str, base_prefix: str):
        """
        Process iTIP REPLY message.

        Updates organizer's event with attendee's response.

        Args:
            vcal: Parsed VCALENDAR with METHOD:REPLY
            user: User posting the REPLY (attendee)
            base_prefix: Base URL prefix for responses

        Returns:
            HTTP response with schedule-response XML
        """
        from radicale import httputils

        try:

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

    def _process_refresh(self, vcal: vobject.base.Component, user: str, base_prefix: str):
        """
        Process iTIP REFRESH message.

        When an attendee requests the latest version of an event, we find the
        organizer's current event and send a fresh REQUEST to the attendee's inbox.

        Args:
            vcal: Parsed VCALENDAR with METHOD:REFRESH
            user: User posting the REFRESH (attendee)
            base_prefix: Base URL prefix for responses

        Returns:
            HTTP response with schedule-response XML
        """
        from radicale import httputils

        try:
            # Get the component (VEVENT/VTODO/VJOURNAL)
            component = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    break

            if not component:
                logger.warning("No schedulable component in REFRESH")
                return httputils.BAD_REQUEST

            # Extract UID and ORGANIZER
            if not hasattr(component, 'uid'):
                logger.warning("REFRESH missing UID")
                return httputils.BAD_REQUEST

            uid = component.uid.value

            if not hasattr(component, 'organizer'):
                logger.warning("REFRESH missing ORGANIZER")
                return httputils.BAD_REQUEST

            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)

            if not organizer_email:
                logger.warning(f"Invalid ORGANIZER email: {organizer_uri}")
                return httputils.BAD_REQUEST

            # Extract ATTENDEE (should be the user requesting refresh)
            attendee_email = f"{user}@localhost"  # Simplified - assumes internal domain

            logger.info(f"Processing REFRESH from {attendee_email} for {uid}")

            # Route organizer (must be internal)
            is_internal, organizer_principal = route_attendee(organizer_email, self.storage)

            if not is_internal:
                logger.warning(f"REFRESH for external organizer {organizer_email} - not supported")
                return self._build_schedule_response_error(
                    base_prefix, "External organizers not supported")

            # Find organizer's event
            event_found, event_path, event_collection = self._find_organizer_event(
                organizer_principal, uid)

            if not event_found:
                logger.warning(f"Organizer event not found for UID {uid}")
                return self._build_schedule_response_error(
                    base_prefix, "Event not found")

            # Get the current event
            item_href = event_path.split('/')[-1]
            current_item = event_collection._get(item_href)

            if not current_item:
                logger.warning(f"Could not retrieve event {item_href}")
                return self._build_schedule_response_error(
                    base_prefix, "Event not accessible")

            # Generate fresh REQUEST for this attendee
            current_vcal = current_item.vobject_item
            current_component = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(current_vcal, comp_type):
                    current_component = getattr(current_vcal, comp_type)
                    break

            if not current_component:
                logger.warning(f"Could not parse event component")
                return self._build_schedule_response_error(
                    base_prefix, "Invalid event data")

            # Create iTIP REQUEST message
            request_ical = self._generate_itip_request(current_vcal, current_component)

            # Deliver to requesting attendee's inbox
            is_internal_attendee, attendee_principal = route_attendee(attendee_email, self.storage)

            if not is_internal_attendee:
                logger.warning(f"REFRESH from external attendee {attendee_email} - not supported")
                return self._build_schedule_response_error(
                    base_prefix, "External attendees not supported")

            # Deliver to attendee's inbox
            inbox_path = get_inbox_path(attendee_principal)
            discovered = list(self.storage.discover(inbox_path, depth="0"))

            if not discovered:
                logger.warning(f"Schedule-inbox not found: {inbox_path}")
                return self._build_schedule_response_error(
                    base_prefix, "Inbox not found")

            inbox = discovered[0]

            # Create item
            request_vobject = vobject.readOne(request_ical)
            request_item = radicale_item.Item(collection_path=inbox.path, vobject_item=request_vobject)
            request_item.prepare()

            # Generate filename with timestamp to avoid overwriting
            import time
            timestamp = int(time.time())
            filename = f"{uid}-refresh-{timestamp}.ics"

            # Upload to inbox
            inbox.upload(filename, request_item)

            logger.info(f"Delivered fresh REQUEST to {attendee_email} in response to REFRESH")

            # Return success schedule-response
            return self._build_schedule_response_success(base_prefix, attendee_email)

        except Exception as e:
            logger.error(f"Error processing REFRESH: {e}", exc_info=True)
            return httputils.INTERNAL_SERVER_ERROR

    def _process_counter(self, vcal: vobject.base.Component, user: str, base_prefix: str):
        """
        Process iTIP COUNTER message.

        When an attendee proposes a change (different time, location, etc.),
        we deliver the COUNTER proposal to the organizer's schedule-inbox for review.

        Args:
            vcal: Parsed VCALENDAR with METHOD:COUNTER
            user: User posting the COUNTER (attendee)
            base_prefix: Base URL prefix for responses

        Returns:
            HTTP response with schedule-response XML
        """
        from radicale import httputils

        try:
            # Get the component (VEVENT/VTODO/VJOURNAL)
            component = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    break

            if not component:
                logger.warning("No schedulable component in COUNTER")
                return httputils.BAD_REQUEST

            # Extract UID and ORGANIZER
            if not hasattr(component, 'uid'):
                logger.warning("COUNTER missing UID")
                return httputils.BAD_REQUEST

            uid = component.uid.value

            if not hasattr(component, 'organizer'):
                logger.warning("COUNTER missing ORGANIZER")
                return httputils.BAD_REQUEST

            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)

            if not organizer_email:
                logger.warning(f"Invalid ORGANIZER email: {organizer_uri}")
                return httputils.BAD_REQUEST

            # Extract ATTENDEE (should be the user proposing the counter)
            if not hasattr(component, 'attendee'):
                logger.warning("COUNTER missing ATTENDEE")
                return httputils.BAD_REQUEST

            attendee = component.attendee
            attendee_email = extract_email(attendee.value)

            logger.info(f"Processing COUNTER from {attendee_email} for {uid}")

            # Route organizer
            is_internal, organizer_principal = route_attendee(organizer_email, self.storage)

            if not is_internal:
                # External organizer - send COUNTER via email
                logger.info(f"COUNTER for external organizer {organizer_email} - sending via email")

                if self.email_config:
                    try:
                        # Build iTIP COUNTER message for email
                        itip_attendee = ITIPAttendee(
                            email=organizer_email,
                            is_internal=False,
                            principal_path=None,
                            cn=None
                        )

                        itip_msg = ITIPMessage(
                            method=ITIPMethod.COUNTER,
                            uid=uid,
                            sequence=0,  # COUNTER doesn't increment sequence
                            organizer=organizer_email,
                            attendees=[itip_attendee],
                            component_type=component.name.upper(),
                            icalendar_text=vcal.serialize()
                        )

                        # Send via email
                        self._deliver_external(itip_msg)

                        # Return success schedule-response
                        return self._build_schedule_response_success(base_prefix, organizer_email)

                    except Exception as e:
                        logger.error(f"Failed to send COUNTER email to {organizer_email}: {e}", exc_info=True)
                        return self._build_schedule_response_error(
                            base_prefix, "Email delivery failed")
                else:
                    logger.warning("Email not configured - cannot send COUNTER to external organizer")
                    return self._build_schedule_response_error(
                        base_prefix, "Email not configured")

            # Deliver COUNTER to organizer's schedule-inbox
            inbox_path = get_inbox_path(organizer_principal)
            discovered = list(self.storage.discover(inbox_path, depth="0"))

            if not discovered:
                logger.warning(f"Schedule-inbox not found: {inbox_path}")
                return self._build_schedule_response_error(
                    base_prefix, "Organizer inbox not found")

            inbox = discovered[0]

            # Create item from COUNTER message
            counter_item = radicale_item.Item(collection_path=inbox.path, vobject_item=vcal)
            counter_item.prepare()

            # Generate filename with timestamp to prevent overwrites
            import time
            timestamp = int(time.time())
            filename = f"{uid}-counter-{timestamp}.ics"

            # Upload to organizer's inbox
            inbox.upload(filename, counter_item)

            logger.info(f"Delivered COUNTER from {attendee_email} to {organizer_email} inbox")

            # Return success schedule-response
            return self._build_schedule_response_success(base_prefix, organizer_email)

        except Exception as e:
            logger.error(f"Error processing COUNTER: {e}", exc_info=True)
            return httputils.INTERNAL_SERVER_ERROR

    def _process_declinecounter(self, vcal: vobject.base.Component, user: str, base_prefix: str):
        """
        Process iTIP DECLINECOUNTER message.

        When an organizer declines an attendee's counter-proposal, we deliver
        the DECLINECOUNTER message to the attendee's schedule-inbox.

        Args:
            vcal: Parsed VCALENDAR with METHOD:DECLINECOUNTER
            user: User posting the DECLINECOUNTER (organizer)
            base_prefix: Base URL prefix for responses

        Returns:
            HTTP response with schedule-response XML
        """
        from radicale import httputils

        try:
            # Get the component (VEVENT/VTODO/VJOURNAL)
            component = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    break

            if not component:
                logger.warning("No schedulable component in DECLINECOUNTER")
                return httputils.BAD_REQUEST

            # Extract UID
            if not hasattr(component, 'uid'):
                logger.warning("DECLINECOUNTER missing UID")
                return httputils.BAD_REQUEST

            uid = component.uid.value

            # Extract ORGANIZER (should be the user posting the DECLINECOUNTER)
            if not hasattr(component, 'organizer'):
                logger.warning("DECLINECOUNTER missing ORGANIZER")
                return httputils.BAD_REQUEST

            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)

            # Extract ATTENDEE(s) who proposed the counter
            if not hasattr(component, 'attendee'):
                logger.warning("DECLINECOUNTER missing ATTENDEE")
                return httputils.BAD_REQUEST

            attendees = component.attendee_list if hasattr(component, 'attendee_list') else [component.attendee]

            logger.info(f"Processing DECLINECOUNTER from {organizer_email} for {uid}")

            # Deliver to each attendee's inbox
            delivered_count = 0
            external_attendees = []

            for attendee in attendees:
                attendee_email = extract_email(attendee.value)
                if not attendee_email:
                    continue

                # Route attendee
                is_internal, attendee_principal = route_attendee(attendee_email, self.storage)

                if not is_internal:
                    # External attendee - collect for email delivery
                    logger.info(f"DECLINECOUNTER for external attendee {attendee_email} - will send via email")
                    external_attendees.append(ITIPAttendee(
                        email=attendee_email,
                        is_internal=False,
                        principal_path=None,
                        cn=attendee.params.get('CN', [None])[0] if hasattr(attendee, 'params') else None
                    ))
                    continue

                # Deliver DECLINECOUNTER to attendee's schedule-inbox
                inbox_path = get_inbox_path(attendee_principal)
                discovered = list(self.storage.discover(inbox_path, depth="0"))

                if not discovered:
                    logger.warning(f"Schedule-inbox not found: {inbox_path}")
                    continue

                inbox = discovered[0]

                # Create item from DECLINECOUNTER message
                declinecounter_item = radicale_item.Item(collection_path=inbox.path, vobject_item=vcal)
                declinecounter_item.prepare()

                # Generate filename with timestamp
                import time
                timestamp = int(time.time())
                filename = f"{uid}-declinecounter-{timestamp}.ics"

                # Upload to attendee's inbox
                inbox.upload(filename, declinecounter_item)

                logger.info(f"Delivered DECLINECOUNTER to {attendee_email} inbox")
                delivered_count += 1

            # Send DECLINECOUNTER to external attendees via email
            if external_attendees and self.email_config:
                try:
                    itip_msg = ITIPMessage(
                        method=ITIPMethod.DECLINECOUNTER,
                        uid=uid,
                        sequence=0,  # DECLINECOUNTER doesn't increment sequence
                        organizer=organizer_email,
                        attendees=external_attendees,
                        component_type=component.name.upper(),
                        icalendar_text=vcal.serialize()
                    )

                    self._deliver_external(itip_msg)
                    delivered_count += len(external_attendees)

                except Exception as e:
                    logger.error(f"Failed to send DECLINECOUNTER emails: {e}", exc_info=True)

            if delivered_count == 0:
                logger.warning("DECLINECOUNTER not delivered to any attendees")
                return self._build_schedule_response_error(
                    base_prefix, "No attendees could receive DECLINECOUNTER")

            # Return success schedule-response
            return self._build_schedule_response_success(base_prefix, organizer_email)

        except Exception as e:
            logger.error(f"Error processing DECLINECOUNTER: {e}", exc_info=True)
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

"""
iTIP message processor for implicit scheduling.
"""

import logging
import re
import vobject
from datetime import datetime
from typing import List, Optional, Tuple
from moreradicale.itip.models import (
    ITIPMethod, ITIPAttendee, ITIPMessage, AttendeePartStat,
    ScheduleStatus, ScheduleAgent
)
from moreradicale.itip.router import extract_email, route_attendee, get_inbox_path
from moreradicale.itip.validator import needs_scheduling
from moreradicale import item as radicale_item
from moreradicale import email_utils


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
        # Group definitions for CUTYPE=GROUP expansion
        self.groups = {}

        # Initialize AutoScheduler for resource auto-accept
        self.auto_scheduler = None

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

            # Load group definitions for CUTYPE=GROUP expansion
            groups_file = configuration.get("scheduling", "groups_file")
            if groups_file:
                self.groups = self._load_groups(groups_file)

            # Initialize AutoScheduler for SCHEDULE-AGENT=SERVER
            if scheduling_enabled:
                try:
                    from moreradicale.itip.auto_scheduler import AutoScheduler
                    self.auto_scheduler = AutoScheduler(storage, configuration)
                    logger.info("AutoScheduler initialized for resource calendars")
                except Exception as e:
                    logger.warning(f"AutoScheduler initialization failed: {e}")

    def _load_groups(self, groups_file: str) -> dict:
        """
        Load group definitions from a JSON file.

        The JSON file should have the format:
        {
            "engineering@example.com": {
                "members": ["alice@example.com", "bob@example.com", "charlie@example.com"],
                "name": "Engineering Team"
            },
            "all-hands@example.com": {
                "members": ["engineering@example.com", "marketing@example.com"],
                "name": "All Hands"
            }
        }

        Groups can reference other groups (recursive expansion supported).

        Args:
            groups_file: Path to the JSON file

        Returns:
            Dictionary mapping group emails to group definitions
        """
        import json
        import os

        if not os.path.exists(groups_file):
            logger.warning(f"Groups file not found: {groups_file}")
            return {}

        try:
            with open(groups_file, 'r', encoding='utf-8') as f:
                groups = json.load(f)
                logger.info(f"Loaded {len(groups)} group definitions from {groups_file}")
                return groups
        except Exception as e:
            logger.error(f"Failed to load groups from {groups_file}: {e}")
            return {}

    def _expand_groups(self, component: vobject.base.Component) -> bool:
        """
        Expand CUTYPE=GROUP attendees into individual members.

        This modifies the component in-place, replacing GROUP attendees
        with individual member attendees.

        Args:
            component: VEVENT/VTODO component to modify

        Returns:
            True if any groups were expanded, False otherwise
        """
        if not self.groups:
            return False

        if not hasattr(component, 'attendee'):
            return False

        # Get existing attendees
        attendees_raw = component.contents.get('attendee', [])
        if not isinstance(attendees_raw, list):
            attendees_raw = [attendees_raw]

        expanded = False
        new_attendees = []
        existing_emails = set()

        for att in attendees_raw:
            att_email = extract_email(att.value)
            if not att_email:
                new_attendees.append(att)
                continue

            # Check if this is a GROUP with CUTYPE=GROUP
            cutype = 'INDIVIDUAL'
            if hasattr(att, 'params') and 'CUTYPE' in att.params:
                cutype = att.params['CUTYPE'][0].upper()

            if cutype == 'GROUP' and att_email.lower() in [g.lower() for g in self.groups]:
                # Expand this group
                group_key = next(g for g in self.groups if g.lower() == att_email.lower())
                group_def = self.groups[group_key]
                members = self._get_group_members(group_key, set())

                logger.info(f"Expanding group {att_email} into {len(members)} members")

                for member_email in members:
                    if member_email.lower() in existing_emails:
                        continue  # Skip duplicates

                    # Create new attendee for this member
                    member_att = component.add('attendee')
                    member_att.value = f"mailto:{member_email}"

                    # Copy relevant parameters from group, but change CUTYPE
                    if hasattr(att, 'params'):
                        for param, value in att.params.items():
                            if param.upper() != 'CUTYPE':
                                member_att.params[param] = value
                    member_att.params['CUTYPE'] = ['INDIVIDUAL']
                    member_att.params['PARTSTAT'] = ['NEEDS-ACTION']

                    # Track expanded attendee
                    new_attendees.append(member_att)
                    existing_emails.add(member_email.lower())
                    expanded = True
            else:
                # Keep non-group attendees as-is
                if att_email.lower() not in existing_emails:
                    new_attendees.append(att)
                    existing_emails.add(att_email.lower())

        if expanded:
            # Replace component's attendee list with expanded list
            component.contents['attendee'] = new_attendees
            logger.info(f"Group expansion complete: {len(new_attendees)} total attendees")

        return expanded

    def _get_group_members(self, group_email: str, visited: set) -> List[str]:
        """
        Recursively get all members of a group, handling nested groups.

        Args:
            group_email: The group email to expand
            visited: Set of already-visited groups (prevents infinite loops)

        Returns:
            List of member email addresses
        """
        if group_email.lower() in visited:
            logger.warning(f"Circular group reference detected: {group_email}")
            return []

        visited.add(group_email.lower())

        group_key = None
        for g in self.groups:
            if g.lower() == group_email.lower():
                group_key = g
                break

        if not group_key:
            return []

        group_def = self.groups[group_key]
        members = group_def.get('members', [])

        result = []
        for member in members:
            # Check if this member is also a group
            member_lower = member.lower()
            is_nested_group = any(g.lower() == member_lower for g in self.groups)

            if is_nested_group:
                # Recursively expand nested group
                nested_members = self._get_group_members(member, visited)
                result.extend(nested_members)
            else:
                result.append(member)

        return result

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

            # RFC 6638 Implicit Scheduling: Verify user is authorized as organizer
            # Only the organizer (or their delegate) should trigger implicit scheduling on PUT
            from moreradicale.itip.router import validate_organizer_permission
            if not validate_organizer_permission(organizer_email, user, self.configuration, self.storage):
                logger.debug(f"User {user} is not organizer {organizer_email}, skipping implicit scheduling")
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

                # RFC 6638: Get SCHEDULE-AGENT parameter
                schedule_agent = ScheduleAgent.SERVER  # Default
                if hasattr(att, 'params') and 'SCHEDULE-AGENT' in att.params:
                    agent_str = att.params['SCHEDULE-AGENT'][0].upper()
                    try:
                        schedule_agent = ScheduleAgent(agent_str)
                    except ValueError:
                        logger.warning(f"Unknown SCHEDULE-AGENT: {agent_str}, using SERVER")
                        schedule_agent = ScheduleAgent.SERVER

                itip_attendee = ITIPAttendee(
                    email=att_email,
                    partstat=partstat,
                    cn=cn,
                    role=role,
                    cutype=cutype,
                    schedule_agent=schedule_agent
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

            # Process resource auto-accept for ROOM/RESOURCE attendees
            # This checks for conflicts and auto-accepts if available
            self._process_resource_auto_accept(itip_msg, vcal, component)

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

            # RFC 6638 Implicit Scheduling: Verify user is authorized as organizer
            # Only the organizer (or their delegate) should trigger implicit CANCEL on DELETE
            from moreradicale.itip.router import validate_organizer_permission
            if not validate_organizer_permission(organizer_email, user, self.configuration, self.storage):
                logger.debug(f"User {user} is not organizer {organizer_email}, skipping implicit CANCEL")
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

                # RFC 6638: Get SCHEDULE-AGENT parameter
                schedule_agent = ScheduleAgent.SERVER  # Default
                if hasattr(att, 'params') and 'SCHEDULE-AGENT' in att.params:
                    agent_str = att.params['SCHEDULE-AGENT'][0].upper()
                    try:
                        schedule_agent = ScheduleAgent(agent_str)
                    except ValueError:
                        logger.warning(f"Unknown SCHEDULE-AGENT: {agent_str}, using SERVER")
                        schedule_agent = ScheduleAgent.SERVER

                itip_attendee = ITIPAttendee(
                    email=att_email,
                    partstat=partstat,
                    cn=cn,
                    role=role,
                    cutype=cutype,
                    schedule_agent=schedule_agent
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

        Updates attendee.schedule_status with RFC 6638 status codes:
        - 1.2 (DELIVERED): Successfully delivered to inbox
        - 3.7 (INVALID_USER): Inbox not found (invalid user)
        - 3.8 (NO_SCHEDULING): SCHEDULE-AGENT is CLIENT or NONE
        - 5.1 (DELIVERY_FAILED): Delivery error

        Args:
            itip_msg: iTIP message to deliver
        """
        for attendee in itip_msg.attendees:
            if not attendee.is_internal or not attendee.principal_path:
                continue

            # RFC 6638: Check SCHEDULE-AGENT - skip if CLIENT or NONE
            if attendee.schedule_agent != ScheduleAgent.SERVER:
                logger.debug(
                    f"Skipping delivery to {attendee.email}: "
                    f"SCHEDULE-AGENT={attendee.schedule_agent.value}"
                )
                # RFC 6638 3.8: No scheduling privileges (client handles it)
                attendee.schedule_status = ScheduleStatus.NO_SCHEDULING
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
                    # RFC 6638 3.7: Invalid calendar user
                    attendee.schedule_status = ScheduleStatus.INVALID_USER
                    continue

                inbox = discovered[0]

                # Create item from iTIP message
                itip_vobject = vobject.readOne(itip_msg.icalendar_text)
                itip_item = radicale_item.Item(collection_path=inbox.path, vobject_item=itip_vobject)
                itip_item.prepare()

                # Upload iTIP message
                inbox.upload(filename, itip_item)

                # RFC 6638 1.2: Delivered to calendar user
                attendee.schedule_status = ScheduleStatus.DELIVERED
                logger.info(f"Delivered iTIP {itip_msg.method.value} to {attendee.email} inbox: {item_path}")

            except Exception as e:
                # RFC 6638 5.1: Could not be delivered
                attendee.schedule_status = ScheduleStatus.DELIVERY_FAILED
                logger.error(f"Failed to deliver to {attendee.email}: {e}", exc_info=True)

    def _deliver_external(self, itip_msg: ITIPMessage) -> None:
        """
        Deliver iTIP message to external attendees via RFC 6047 email.

        Sends calendar invitations, updates, cancellations, and counter-proposals
        to attendees who are not on this Radicale server.

        Updates attendee.schedule_status with RFC 6638 status codes:
        - 1.1 (PENDING): Email not configured, cannot track delivery
        - 1.2 (DELIVERED): Email sent to SMTP server
        - 3.8 (NO_SCHEDULING): SCHEDULE-AGENT is CLIENT or NONE
        - 5.1 (DELIVERY_FAILED): SMTP delivery failed

        Args:
            itip_msg: iTIP message to deliver

        Note:
            Email failures are logged but do not raise exceptions to prevent
            blocking event creation when email delivery fails.
        """
        # Filter for external attendees only (respecting SCHEDULE-AGENT)
        external_attendees = [a for a in itip_msg.attendees if not a.is_internal]

        if not external_attendees:
            logger.debug("No external attendees - skipping email delivery")
            return

        # Check SCHEDULE-AGENT for external attendees too
        for attendee in external_attendees:
            if attendee.schedule_agent != ScheduleAgent.SERVER:
                logger.debug(
                    f"Skipping email to {attendee.email}: "
                    f"SCHEDULE-AGENT={attendee.schedule_agent.value}"
                )
                attendee.schedule_status = ScheduleStatus.NO_SCHEDULING

        # Filter to only those with SERVER schedule-agent
        deliverable = [a for a in external_attendees
                       if a.schedule_agent == ScheduleAgent.SERVER]

        if not deliverable:
            logger.debug("No external attendees with SCHEDULE-AGENT=SERVER")
            return

        if not self.email_config:
            # Email not configured - mark as pending (unknown status)
            for attendee in deliverable:
                # RFC 6638 1.1: Pending - no way to deliver
                attendee.schedule_status = ScheduleStatus.PENDING
            logger.debug("Email not configured - external attendees marked as PENDING")
            return

        logger.info(f"Delivering iTIP {itip_msg.method.value} to {len(deliverable)} external attendee(s)")

        # Extract attachments from the event (shared across all recipients)
        attachments = []
        try:
            attachments = email_utils.extract_attachments_from_icalendar(itip_msg.icalendar_text)
            if attachments:
                logger.info(f"Extracted {len(attachments)} attachment(s) from event")
        except Exception as e:
            logger.warning(f"Failed to extract attachments: {e}")

        for attendee in deliverable:
            try:
                # Build email components
                subject = self._build_email_subject(itip_msg, attendee)
                body = self._build_email_body(itip_msg, attendee)
                from_email = self._get_from_email(itip_msg)

                # Send via SMTP with attachments
                success = email_utils.send_itip_email(
                    email_config=self.email_config,
                    from_email=from_email,
                    to_email=attendee.email,
                    subject=subject,
                    body_text=body,
                    icalendar_text=itip_msg.icalendar_text,
                    method=itip_msg.method.value,
                    attachments=attachments if attachments else None
                )

                if success:
                    # RFC 6638 1.2: Delivered (to SMTP server)
                    attendee.schedule_status = ScheduleStatus.DELIVERED
                    logger.info(f"Sent iTIP {itip_msg.method.value} email to {attendee.email}")
                else:
                    # RFC 6638 5.1: Delivery failed
                    attendee.schedule_status = ScheduleStatus.DELIVERY_FAILED
                    logger.warning(f"Failed to send iTIP {itip_msg.method.value} email to {attendee.email}")

            except Exception as e:
                # RFC 6638 5.1: Could not be delivered
                attendee.schedule_status = ScheduleStatus.DELIVERY_FAILED
                # Log but don't block event creation
                logger.error(f"Email delivery to {attendee.email} failed: {e}", exc_info=True)

    def _deliver_reply_to_external_organizer(
            self, vcal: vobject.base.Component, organizer_email: str,
            attendee_email: str, attendee_cn: str, partstat: str
    ) -> Tuple[bool, ScheduleStatus]:
        """
        Deliver iTIP REPLY to external organizer via RFC 6047 email.

        When an internal attendee responds to an invitation from an external
        organizer, we deliver the REPLY via email since we cannot directly
        update the organizer's calendar.

        Args:
            vcal: VCALENDAR with METHOD:REPLY
            organizer_email: External organizer's email
            attendee_email: Internal attendee's email (from address)
            attendee_cn: Attendee's display name
            partstat: PARTSTAT value (ACCEPTED, DECLINED, TENTATIVE, DELEGATED)

        Returns:
            Tuple of (success, schedule_status)
        """
        if not self.email_config:
            # Email not configured - mark as pending (unknown status)
            logger.debug(
                f"Email not configured - REPLY to external organizer "
                f"{organizer_email} marked as PENDING"
            )
            return False, ScheduleStatus.PENDING

        # Serialize the vCalendar to iCalendar text
        icalendar_text = vcal.serialize()

        # Build subject based on PARTSTAT
        summary = "Calendar Event"
        for comp_type in ('vevent', 'vtodo', 'vjournal'):
            if hasattr(vcal, comp_type):
                comp = getattr(vcal, comp_type)
                if hasattr(comp, 'summary'):
                    summary = comp.summary.value
                break

        # Map PARTSTAT to human-readable status
        status_map = {
            "ACCEPTED": "Accepted",
            "DECLINED": "Declined",
            "TENTATIVE": "Tentative",
            "DELEGATED": "Delegated",
        }
        status_text = status_map.get(partstat, partstat)

        # Get subject prefix from config
        prefix = ""
        if self.configuration:
            prefix = self.configuration.get("scheduling", "email_subject_prefix") or ""

        subject = f"{prefix}{status_text}: {summary}"

        # Build body text
        respondent = attendee_cn if attendee_cn else attendee_email
        body = (
            f"{respondent} has responded to your calendar invitation.\n\n"
            f"Response: {status_text}\n"
            f"Event: {summary}\n\n"
            "Please see the attached calendar reply."
        )

        # Determine from address (use attendee's email or configured sender)
        from_email = attendee_email
        if self.email_config.sender_email:
            from_email = self.email_config.sender_email

        try:
            success = email_utils.send_itip_email(
                email_config=self.email_config,
                from_email=from_email,
                to_email=organizer_email,
                subject=subject,
                body_text=body,
                icalendar_text=icalendar_text,
                method="REPLY"
            )

            if success:
                logger.info(
                    f"Sent REPLY ({partstat}) email to external organizer "
                    f"{organizer_email} from {attendee_email}"
                )
                return True, ScheduleStatus.DELIVERED
            else:
                logger.warning(
                    f"Failed to send REPLY email to external organizer {organizer_email}"
                )
                return False, ScheduleStatus.DELIVERY_FAILED

        except Exception as e:
            logger.error(
                f"Email delivery of REPLY to {organizer_email} failed: {e}",
                exc_info=True
            )
            return False, ScheduleStatus.DELIVERY_FAILED

    def _deliver_refresh_to_external_organizer(
            self, vcal: vobject.base.Component, organizer_email: str,
            attendee_email: str
    ) -> Tuple[bool, ScheduleStatus]:
        """
        Deliver iTIP REFRESH to external organizer via RFC 6047 email.

        When an internal attendee requests the current event state from an
        external organizer, we deliver the REFRESH via email.

        Args:
            vcal: VCALENDAR with METHOD:REFRESH
            organizer_email: External organizer's email
            attendee_email: Internal attendee's email

        Returns:
            Tuple of (success, schedule_status)
        """
        if not self.email_config:
            logger.debug(
                f"Email not configured - REFRESH to external organizer "
                f"{organizer_email} marked as PENDING"
            )
            return False, ScheduleStatus.PENDING

        # Serialize the vCalendar to iCalendar text
        icalendar_text = vcal.serialize()

        # Build subject
        summary = "Calendar Event"
        for comp_type in ('vevent', 'vtodo', 'vjournal'):
            if hasattr(vcal, comp_type):
                comp = getattr(vcal, comp_type)
                if hasattr(comp, 'summary'):
                    summary = comp.summary.value
                break

        # Get subject prefix from config
        prefix = ""
        if self.configuration:
            prefix = self.configuration.get("scheduling", "email_subject_prefix") or ""

        subject = f"{prefix}Refresh Request: {summary}"

        body = (
            f"{attendee_email} is requesting the current version of an event.\n\n"
            f"Event: {summary}\n\n"
            "Please resend the calendar invitation to update the attendee's calendar."
        )

        # Determine from address
        from_email = attendee_email
        if self.email_config.sender_email:
            from_email = self.email_config.sender_email

        try:
            success = email_utils.send_itip_email(
                email_config=self.email_config,
                from_email=from_email,
                to_email=organizer_email,
                subject=subject,
                body_text=body,
                icalendar_text=icalendar_text,
                method="REFRESH"
            )

            if success:
                logger.info(
                    f"Sent REFRESH email to external organizer "
                    f"{organizer_email} from {attendee_email}"
                )
                return True, ScheduleStatus.DELIVERED
            else:
                logger.warning(
                    f"Failed to send REFRESH email to external organizer {organizer_email}"
                )
                return False, ScheduleStatus.DELIVERY_FAILED

        except Exception as e:
            logger.error(
                f"Email delivery of REFRESH to {organizer_email} failed: {e}",
                exc_info=True
            )
            return False, ScheduleStatus.DELIVERY_FAILED

    def _process_resource_auto_accept(self, itip_msg: ITIPMessage,
                                      vcal: vobject.base.Component,
                                      component: vobject.base.Component) -> None:
        """
        Auto-accept/decline for resource attendees (CUTYPE=ROOM or CUTYPE=RESOURCE).

        This method now delegates to the AutoScheduler class which implements
        RFC 6638 SCHEDULE-AGENT=SERVER processing with configurable policies.

        Resources automatically accept meeting invitations based on:
        1. Auto-accept policy (always, if-free, manual, tentative-if-conflict)
        2. Scheduling conflicts in the resource's calendar
        3. VAVAILABILITY constraints (if defined)

        For each resource attendee:
        1. Check auto-accept policy
        2. Check for scheduling conflicts (if needed)
        3. Set PARTSTAT to ACCEPTED/DECLINED/TENTATIVE
        4. Add event to resource's calendar (if accepted)
        5. Update the organizer's copy with the resource's response

        Args:
            itip_msg: iTIP REQUEST message
            vcal: Full VCALENDAR object
            component: VEVENT/VTODO component
        """
        # Use new AutoScheduler if available
        if self.auto_scheduler:
            try:
                auto_scheduled = self.auto_scheduler.process_request(itip_msg, vcal, component)

                # Update organizer's copy for each auto-scheduled resource
                for attendee in auto_scheduled:
                    try:
                        is_internal, organizer_principal = route_attendee(itip_msg.organizer, self.storage)
                        if is_internal:
                            event_found, event_path, event_collection = self._find_organizer_event(
                                organizer_principal, itip_msg.uid)
                            if event_found:
                                self._update_attendee_partstat(
                                    event_path, event_collection, attendee.email, attendee.partstat.value
                                )
                    except Exception as e:
                        logger.error(f"Error updating organizer calendar for {attendee.email}: {e}", exc_info=True)

                return

            except Exception as e:
                logger.error(f"AutoScheduler failed, falling back to legacy implementation: {e}", exc_info=True)

        # Fallback to legacy implementation if AutoScheduler not available

        for attendee in itip_msg.attendees:
            # Only process ROOM and RESOURCE types
            if attendee.cutype not in ('ROOM', 'RESOURCE'):
                continue

            # Only internal resources can auto-accept
            if not attendee.is_internal or not attendee.principal_path:
                continue

            # Skip if SCHEDULE-AGENT is not SERVER
            if attendee.schedule_agent != ScheduleAgent.SERVER:
                logger.debug(f"Resource {attendee.email} skipped: SCHEDULE-AGENT={attendee.schedule_agent.value}")
                continue

            try:
                # Get event time range for conflict check
                dtstart = getattr(component, 'dtstart', None)
                dtend = getattr(component, 'dtend', None)

                if not dtstart:
                    logger.warning(f"Cannot auto-accept for {attendee.email}: missing DTSTART")
                    continue

                event_start = dtstart.value
                event_end = dtend.value if dtend else event_start

                # Check for conflicts in the resource's calendar
                has_conflict = self._check_resource_conflict(
                    attendee.principal_path, attendee.email,
                    event_start, event_end, itip_msg.uid
                )

                if has_conflict:
                    # Resource has a conflict - decline
                    attendee.partstat = AttendeePartStat.DECLINED
                    logger.info(f"Resource {attendee.email} DECLINED (conflict detected)")
                else:
                    # No conflict - auto-accept and add to resource's calendar
                    self._add_event_to_resource_calendar(
                        attendee.principal_path, vcal, component, attendee.email, itip_msg.uid
                    )
                    attendee.partstat = AttendeePartStat.ACCEPTED
                    logger.info(f"Resource {attendee.email} ACCEPTED (no conflict)")

                # Update organizer's copy with the resource's response
                is_internal, organizer_principal = route_attendee(itip_msg.organizer, self.storage)
                if is_internal:
                    event_found, event_path, event_collection = self._find_organizer_event(
                        organizer_principal, itip_msg.uid)
                    if event_found:
                        self._update_attendee_partstat(
                            event_path, event_collection, attendee.email, attendee.partstat.value
                        )

            except Exception as e:
                logger.error(f"Error processing resource auto-accept for {attendee.email}: {e}",
                             exc_info=True)

    def _check_resource_conflict(self, principal_path: str, resource_email: str,
                                 event_start, event_end, exclude_uid: str) -> bool:
        """
        Check if a resource has conflicting events in the specified time range.

        Args:
            principal_path: Resource's principal path
            resource_email: Resource's email address
            event_start: Event start time
            event_end: Event end time
            exclude_uid: UID to exclude (the event being scheduled)

        Returns:
            True if conflicts exist, False otherwise
        """

        try:
            # Discover all calendar collections under resource's principal
            discovered = list(self.storage.discover(principal_path, depth="1"))

            for collection in discovered:
                # Skip non-calendar collections
                if not hasattr(collection, 'tag') or collection.tag != 'VCALENDAR':
                    continue

                # Skip schedule-inbox
                if 'schedule-inbox' in collection.path.lower():
                    continue

                # Check all items in calendar
                try:
                    hrefs = list(collection._list())

                    for href in hrefs:
                        item = collection._get(href)
                        if not item:
                            continue

                        vcal = item.vobject_item

                        for subcomp in vcal.getChildren():
                            if subcomp.name != 'VEVENT':
                                continue

                            # Skip the event being scheduled (same UID)
                            if hasattr(subcomp, 'uid') and subcomp.uid.value == exclude_uid:
                                continue

                            # Skip cancelled events
                            status = getattr(subcomp, 'status', None)
                            if status and status.value.upper() == 'CANCELLED':
                                continue

                            # Skip transparent events
                            transp = getattr(subcomp, 'transp', None)
                            if transp and transp.value.upper() == 'TRANSPARENT':
                                continue

                            # Get event times
                            if not hasattr(subcomp, 'dtstart'):
                                continue

                            existing_start = subcomp.dtstart.value
                            existing_end = subcomp.dtend.value if hasattr(subcomp, 'dtend') else existing_start

                            # Check for overlap
                            if self._times_overlap(event_start, event_end, existing_start, existing_end):
                                logger.debug(
                                    f"Conflict found for {resource_email}: "
                                    f"existing event {getattr(subcomp, 'uid', 'unknown').value} "
                                    f"overlaps with new event"
                                )
                                return True

                except Exception as e:
                    logger.warning(f"Error reading calendar {collection.path}: {e}")
                    continue

            return False

        except Exception as e:
            logger.error(f"Error checking resource conflict: {e}", exc_info=True)
            # On error, assume conflict to be safe
            return True

    def _times_overlap(self, start1, end1, start2, end2) -> bool:
        """Check if two time ranges overlap."""
        from datetime import date, datetime

        # Normalize to comparable types
        def to_datetime(dt):
            if isinstance(dt, date) and not isinstance(dt, datetime):
                return datetime.combine(dt, datetime.min.time())
            return dt

        s1, e1 = to_datetime(start1), to_datetime(end1)
        s2, e2 = to_datetime(start2), to_datetime(end2)

        # Overlap if: start1 < end2 AND start2 < end1
        return s1 < e2 and s2 < e1

    def _add_event_to_resource_calendar(self, principal_path: str,
                                        vcal: vobject.base.Component,
                                        component: vobject.base.Component,
                                        resource_email: str,
                                        uid: str) -> bool:
        """
        Add an event to the resource's default calendar with PARTSTAT=ACCEPTED.

        Args:
            principal_path: Resource's principal path
            vcal: Full VCALENDAR object
            component: VEVENT/VTODO component
            resource_email: Resource's email
            uid: Event UID

        Returns:
            True if event was added successfully
        """
        try:
            # Find resource's default calendar
            discovered = list(self.storage.discover(principal_path, depth="1"))
            calendar_collection = None

            for collection in discovered:
                if hasattr(collection, 'tag') and collection.tag == 'VCALENDAR':
                    if 'schedule-inbox' not in collection.path.lower():
                        if 'schedule-outbox' not in collection.path.lower():
                            calendar_collection = collection
                            break

            if not calendar_collection:
                logger.warning(f"No calendar found for resource {resource_email}")
                return False

            # Clone the event and update the resource's PARTSTAT to ACCEPTED
            new_vcal = vobject.iCalendar()

            # Copy the component
            for child in vcal.getChildren():
                if child.name in ('VEVENT', 'VTODO', 'VJOURNAL'):
                    new_comp = new_vcal.add(child.name.lower())

                    # Copy all properties
                    for prop in child.getChildren():
                        new_prop = new_comp.add(prop.name.lower())
                        new_prop.value = prop.value
                        if hasattr(prop, 'params'):
                            for k, v in prop.params.items():
                                new_prop.params[k] = list(v) if isinstance(v, list) else [v]

                        # Update PARTSTAT for the resource attendee
                        if prop.name.upper() == 'ATTENDEE':
                            prop_email = extract_email(prop.value) or ''
                            if prop_email.lower() == resource_email.lower():
                                new_prop.params['PARTSTAT'] = ['ACCEPTED']

            # Create item and upload
            event_item = radicale_item.Item(
                collection_path=calendar_collection.path,
                vobject_item=new_vcal
            )
            event_item.prepare()

            filename = f"{uid}.ics"
            calendar_collection.upload(filename, event_item)

            logger.info(f"Added event {uid} to resource {resource_email}'s calendar")
            return True

        except Exception as e:
            logger.error(f"Error adding event to resource calendar: {e}", exc_info=True)
            return False

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
        from moreradicale import httputils
        from moreradicale.itip.validator import validate_itip_message

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
            elif method == 'REQUEST':
                # REQUEST can be either:
                # 1. VFREEBUSY REQUEST - free/busy query
                # 2. VEVENT/VTODO/VJOURNAL REQUEST - meeting invitation
                if hasattr(vcal, 'vfreebusy'):
                    return self._process_freebusy_request(vcal, user, base_prefix)
                else:
                    # VEVENT/VTODO/VJOURNAL REQUEST - deliver to attendee inboxes
                    return self._process_vevent_request(vcal, user, base_prefix, ical_text)
            elif method == 'ADD':
                # RFC 5546 §3.2.4: ADD method adds new instances to recurring events
                return self._process_add(vcal, user, base_prefix, ical_text)
            elif method == 'PUBLISH':
                # RFC 5546 §3.2.5: PUBLISH method for one-way calendar publication
                return self._process_publish(vcal, user, base_prefix, ical_text)
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
        from moreradicale import httputils

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

            # RFC 5546 Delegation: Extract DELEGATED-TO if present
            delegated_to = None
            delegated_to_list = attendee.params.get('DELEGATED-TO', [])
            if delegated_to_list:
                # Extract email from mailto: URI
                delegated_to = extract_email(delegated_to_list[0])
                if delegated_to:
                    logger.info(
                        f"Delegation detected: {attendee_email} delegating to {delegated_to}"
                    )

            # RFC 5546 Delegation: Extract DELEGATED-FROM if present
            # This indicates the replier is a delegate responding to a delegated invitation
            delegated_from = None
            delegated_from_list = attendee.params.get('DELEGATED-FROM', [])
            if delegated_from_list:
                delegated_from = extract_email(delegated_from_list[0])
                if delegated_from:
                    logger.info(
                        f"Delegate response: {attendee_email} responding "
                        f"(delegated from {delegated_from})"
                    )

            # Extract RECURRENCE-ID for recurring event support
            recurrence_id = None
            if hasattr(component, 'recurrence_id'):
                recurrence_id = component.recurrence_id.value
                logger.info(
                    f"Processing REPLY from {attendee_email} for {uid} "
                    f"(RECURRENCE-ID: {recurrence_id}): PARTSTAT={new_partstat}"
                )
            else:
                logger.info(f"Processing REPLY from {attendee_email} for {uid}: PARTSTAT={new_partstat}")

            # Route organizer (must be internal)
            is_internal, organizer_principal = route_attendee(organizer_email, self.storage)

            if not is_internal:
                # External organizer: deliver REPLY via email (RFC 6047)
                logger.info(
                    f"Delivering REPLY to external organizer {organizer_email} "
                    f"from {attendee_email} (PARTSTAT={new_partstat})"
                )

                # Extract attendee CN if available
                attendee_cn = ""
                cn_list = attendee.params.get('CN', [])
                if cn_list:
                    attendee_cn = cn_list[0].strip('"')

                # Deliver via email
                success, schedule_status = self._deliver_reply_to_external_organizer(
                    vcal, organizer_email, attendee_email, attendee_cn, new_partstat
                )

                if success:
                    return self._build_schedule_response_external(
                        base_prefix, organizer_email, schedule_status
                    )
                else:
                    # Even if delivery failed/pending, return the status
                    # (not an error - the REPLY was processed, just couldn't confirm delivery)
                    return self._build_schedule_response_external(
                        base_prefix, organizer_email, schedule_status
                    )

            # Find organizer's event (already in POST handler's lock context)
            event_found, event_path, event_collection = self._find_organizer_event(
                organizer_principal, uid)

            if not event_found:
                logger.warning(f"Organizer event not found for UID {uid}")
                return self._build_schedule_response_error(
                    base_prefix, "Event not found")

            # RFC 5546 §2.1.4: Check sequence ordering to reject stale messages
            incoming_sequence = 0
            if hasattr(component, 'sequence'):
                try:
                    incoming_sequence = int(component.sequence.value)
                except (ValueError, TypeError):
                    incoming_sequence = 0

            is_valid_sequence, stored_sequence = self._check_sequence_ordering(
                event_path, event_collection, incoming_sequence, recurrence_id
            )

            if not is_valid_sequence:
                logger.warning(
                    f"Rejecting REPLY from {attendee_email}: stale SEQUENCE "
                    f"{incoming_sequence} < stored {stored_sequence}"
                )
                return self._build_schedule_response_error(
                    base_prefix,
                    f"Stale message: SEQUENCE {incoming_sequence} < {stored_sequence}",
                    schedule_status="5.3"  # Invalid date/time (closest to stale sequence)
                )

            # RFC 5546 Delegation handling
            if new_partstat == "DELEGATED" and delegated_to:
                # Handle delegation workflow
                delegation_result = self._handle_delegation(
                    event_path, event_collection, attendee_email,
                    delegated_to, component, base_prefix,
                    recurrence_id=recurrence_id
                )
                if delegation_result:
                    return delegation_result
                # If delegation handling returned None, fall through to regular update

            # Update the PARTSTAT for this attendee
            updated = self._update_attendee_partstat(
                event_path, event_collection, attendee_email, new_partstat,
                recurrence_id=recurrence_id,
                delegated_to=delegated_to if new_partstat == "DELEGATED" else None
            )

            if not updated:
                logger.warning(f"Failed to update PARTSTAT for {attendee_email} in {event_path}")
                return self._build_schedule_response_error(
                    base_prefix, "Failed to update event")

            log_msg = f"Updated PARTSTAT for {attendee_email} to {new_partstat} in {event_path}"
            if recurrence_id:
                log_msg += f" (RECURRENCE-ID: {recurrence_id})"
            if delegated_to:
                log_msg += f" (DELEGATED-TO: {delegated_to})"
            if delegated_from:
                log_msg += f" (DELEGATED-FROM: {delegated_from})"
            logger.info(log_msg)

            # RFC 5546: When a delegate declines, notify the original delegator
            # The delegator needs to know so they can attend themselves or find another delegate
            if new_partstat == "DECLINED" and delegated_from:
                self._notify_delegator_of_decline(
                    event_path, event_collection, vcal, component,
                    delegate_email=attendee_email,
                    delegator_email=delegated_from,
                    organizer_email=organizer_email,
                    base_prefix=base_prefix,
                    recurrence_id=recurrence_id
                )

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
        from moreradicale import httputils

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
                # External organizer: deliver REFRESH via email (RFC 6047)
                logger.info(
                    f"Delivering REFRESH to external organizer {organizer_email} "
                    f"from {attendee_email}"
                )

                success, schedule_status = self._deliver_refresh_to_external_organizer(
                    vcal, organizer_email, attendee_email
                )

                return self._build_schedule_response_external(
                    base_prefix, organizer_email, schedule_status
                )

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
                logger.warning("Could not parse event component")
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
        from moreradicale import httputils

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
        from moreradicale import httputils

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

    def _process_publish(self, vcal: vobject.base.Component, user: str,
                         base_prefix: str, ical_text: str):
        """
        Process iTIP PUBLISH message for one-way calendar publication.

        RFC 5546 §3.2.5: PUBLISH is used by an "Organizer" to publish a
        calendar component to one or more "Calendar Users". There is no
        interactivity between the publisher and any other calendar user.
        This method type is only meaningful for components that specify
        the "Organizer", since the "Organizer" is the only party that
        should be able to send this message type.

        Key differences from REQUEST:
        - No ATTENDEE property required (one-way publication)
        - No scheduling/delivery to other users
        - Simply stores in organizer's calendar
        - Used for publishing public calendars, holidays, etc.

        Args:
            vcal: Parsed VCALENDAR with METHOD:PUBLISH
            user: User posting the PUBLISH (organizer)
            base_prefix: Base URL prefix for responses
            ical_text: Original iCalendar text

        Returns:
            HTTP response with schedule-response XML
        """
        from moreradicale import httputils, xmlutils
        from http import client
        import xml.etree.ElementTree as ET

        try:
            # Get the component (VEVENT/VTODO/VJOURNAL)
            component = None
            comp_type_name = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    comp_type_name = comp_type.upper()
                    break

            if not component:
                logger.warning("No schedulable component in PUBLISH")
                return httputils.BAD_REQUEST

            # Extract UID
            if not hasattr(component, 'uid'):
                logger.warning("PUBLISH missing UID")
                return httputils.BAD_REQUEST

            uid = component.uid.value

            # Extract ORGANIZER (required for PUBLISH)
            if not hasattr(component, 'organizer'):
                logger.warning("PUBLISH missing ORGANIZER")
                return httputils.BAD_REQUEST

            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)

            if not organizer_email:
                logger.warning(f"Invalid ORGANIZER email: {organizer_uri}")
                return httputils.BAD_REQUEST

            # Verify user is the organizer
            org_local = organizer_email.split('@')[0] if '@' in organizer_email else organizer_email
            user_local = user.split('@')[0] if '@' in user else user
            if org_local.lower() != user_local.lower():
                logger.warning(f"PUBLISH posted by {user} but organizer is {organizer_email}")
                return self._build_schedule_response_error(
                    base_prefix, "Only organizer can PUBLISH")

            # RFC 5546 §3.2.5: PUBLISH should NOT have ATTENDEE properties
            # If ATTENDEE is present, it's technically invalid, but we'll just log a warning
            if hasattr(component, 'attendee'):
                logger.warning(f"PUBLISH for {uid} contains ATTENDEE property (RFC 5546 violation)")

            logger.info(f"Processing PUBLISH from {organizer_email} for {uid} ({comp_type_name})")

            # PUBLISH is stored in organizer's calendar, not delivered to anyone
            # The organizer has already stored this by POSTing to their outbox
            # We just need to return success

            # Build simple schedule-response XML indicating success
            root = ET.Element(f"{{{xmlutils.NAMESPACES['C']}}}schedule-response")

            response_elem = ET.SubElement(root, f"{{{xmlutils.NAMESPACES['C']}}}response")

            # Recipient is the organizer themselves
            recipient = ET.SubElement(response_elem, f"{{{xmlutils.NAMESPACES['C']}}}recipient")
            href = ET.SubElement(recipient, f"{{{xmlutils.NAMESPACES['D']}}}href")
            href.text = f"mailto:{organizer_email}"

            # Request status: 2.0 = Success
            status_elem = ET.SubElement(response_elem, f"{{{xmlutils.NAMESPACES['C']}}}request-status")
            status_elem.text = "2.0;Success"

            # Calendar data (optional, shows what was published)
            cal_data = ET.SubElement(response_elem, f"{{{xmlutils.NAMESPACES['C']}}}calendar-data")
            cal_data.text = ical_text

            # Create response
            headers = {"Content-Type": "application/xml; charset=utf-8"}
            xml_response = ET.tostring(root, encoding='utf-8')

            logger.info(f"PUBLISH successful for {uid}")
            return client.MULTI_STATUS, headers, xml_response, None

        except Exception as e:
            logger.error(f"Error processing PUBLISH: {e}", exc_info=True)
            return 500, {}, b"Internal Server Error", None

    def _process_add(self, vcal: vobject.base.Component, user: str,
                     base_prefix: str, ical_text: str):
        """
        Process iTIP ADD message for adding instances to recurring events.

        RFC 5546 §3.2.4: The ADD method is used to add one or more new
        instances to an existing recurring event. The component in the ADD
        message contains only the new instance with its RECURRENCE-ID.

        Args:
            vcal: Parsed VCALENDAR with METHOD:ADD
            user: User posting the ADD (organizer)
            base_prefix: Base URL prefix for responses
            ical_text: Original iCalendar text

        Returns:
            HTTP response with schedule-response XML
        """
        from moreradicale import httputils, xmlutils
        from http import client
        import xml.etree.ElementTree as ET

        try:
            # Get the component (VEVENT/VTODO/VJOURNAL)
            component = None
            comp_type_name = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    comp_type_name = comp_type.upper()
                    break

            if not component:
                logger.warning("No schedulable component in ADD")
                return httputils.BAD_REQUEST

            # Extract UID - must reference existing recurring event
            if not hasattr(component, 'uid'):
                logger.warning("ADD missing UID")
                return httputils.BAD_REQUEST

            uid = component.uid.value

            # RFC 5546 §3.2.4: ADD MUST have RECURRENCE-ID to identify the new instance
            if not hasattr(component, 'recurrence_id'):
                logger.warning("ADD missing required RECURRENCE-ID")
                return httputils.BAD_REQUEST

            recurrence_id = str(component.recurrence_id.value)

            # Extract ORGANIZER
            if not hasattr(component, 'organizer'):
                logger.warning("ADD missing ORGANIZER")
                return httputils.BAD_REQUEST

            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)

            if not organizer_email:
                logger.warning(f"Invalid ORGANIZER email: {organizer_uri}")
                return httputils.BAD_REQUEST

            # Verify user is the organizer
            # Compare the local part of organizer email with the authenticated user
            org_local = organizer_email.split('@')[0] if '@' in organizer_email else organizer_email
            user_local = user.split('@')[0] if '@' in user else user
            if org_local.lower() != user_local.lower():
                logger.warning(f"ADD posted by {user} but organizer is {organizer_email}")
                return self._build_schedule_response_error(
                    base_prefix, "Only organizer can send ADD")

            # Extract ATTENDEE(s)
            if not hasattr(component, 'attendee'):
                logger.warning("ADD missing ATTENDEE")
                return httputils.BAD_REQUEST

            attendees_raw = component.attendee_list if hasattr(component, 'attendee_list') else [component.attendee]

            logger.info(
                f"Processing ADD from {organizer_email} for {uid} "
                f"(RECURRENCE-ID: {recurrence_id}) with {len(attendees_raw)} attendees"
            )

            # Build ITIPAttendee list with routing info
            attendees = []
            for att in attendees_raw:
                att_email = extract_email(att.value)
                if not att_email:
                    continue

                # Check SCHEDULE-AGENT parameter
                schedule_agent = ScheduleAgent.SERVER  # Default
                if hasattr(att, 'params'):
                    agent_param = att.params.get('SCHEDULE-AGENT', ['SERVER'])[0].upper()
                    try:
                        schedule_agent = ScheduleAgent(agent_param)
                    except ValueError:
                        schedule_agent = ScheduleAgent.SERVER

                # Route attendee
                is_internal, principal_path = route_attendee(att_email, self.storage)

                # Extract optional parameters
                cn = att.params.get('CN', [None])[0] if hasattr(att, 'params') else None
                partstat_str = att.params.get('PARTSTAT', ['NEEDS-ACTION'])[0] if hasattr(att, 'params') else 'NEEDS-ACTION'
                try:
                    partstat = AttendeePartStat(partstat_str.upper())
                except ValueError:
                    partstat = AttendeePartStat.NEEDS_ACTION

                attendees.append(ITIPAttendee(
                    email=att_email,
                    partstat=partstat,
                    cn=cn,
                    is_internal=is_internal,
                    principal_path=principal_path,
                    schedule_agent=schedule_agent
                ))

            # Build ITIPMessage
            itip_msg = ITIPMessage(
                method=ITIPMethod.ADD,
                uid=uid,
                sequence=int(component.sequence.value) if hasattr(component, 'sequence') else 0,
                organizer=organizer_email,
                attendees=attendees,
                component_type=comp_type_name,
                icalendar_text=ical_text,
                recurrence_id=recurrence_id
            )

            # Deliver ADD to internal attendees
            self._deliver_internal(itip_msg)

            # Deliver ADD to external attendees via email
            self._deliver_external(itip_msg)

            # Build schedule-response XML
            root = ET.Element(f"{{{xmlutils.NAMESPACES['C']}}}schedule-response")

            for attendee in attendees:
                response_elem = ET.SubElement(root, f"{{{xmlutils.NAMESPACES['C']}}}response")

                # Recipient
                recipient = ET.SubElement(response_elem, f"{{{xmlutils.NAMESPACES['C']}}}recipient")
                href = ET.SubElement(recipient, f"{{{xmlutils.NAMESPACES['D']}}}href")
                href.text = f"mailto:{attendee.email}"

                # Request status based on schedule_status
                status_elem = ET.SubElement(response_elem, f"{{{xmlutils.NAMESPACES['C']}}}request-status")
                if attendee.schedule_status:
                    status_elem.text = attendee.schedule_status.value
                elif attendee.schedule_agent != ScheduleAgent.SERVER:
                    status_elem.text = ScheduleStatus.NO_SCHEDULING.value
                elif attendee.is_internal:
                    status_elem.text = ScheduleStatus.DELIVERED.value
                else:
                    status_elem.text = ScheduleStatus.PENDING.value

            # Create response
            headers = {"Content-Type": "application/xml; charset=utf-8"}
            xml_response = ET.tostring(root, encoding='utf-8')
            return client.MULTI_STATUS, headers, xml_response, None

        except Exception as e:
            logger.error(f"Error processing ADD: {e}", exc_info=True)
            return 500, {}, b"Internal Server Error", None

    def _process_vevent_request(self, vcal: vobject.base.Component, user: str,
                                base_prefix: str, ical_text: str):
        """
        Process iTIP REQUEST message for meeting invitations.

        When an organizer posts a VEVENT/VTODO/VJOURNAL REQUEST to their
        schedule-outbox, we deliver the invitation to all attendees:
        - Internal attendees: Deliver to their schedule-inbox
        - External attendees: Send via email (if configured)

        Args:
            vcal: Parsed VCALENDAR with METHOD:REQUEST
            user: User posting the REQUEST (organizer)
            base_prefix: Base URL prefix for responses
            ical_text: Original iCalendar text

        Returns:
            HTTP response with schedule-response XML
        """
        from moreradicale import httputils, xmlutils
        from http import client
        import xml.etree.ElementTree as ET

        try:
            # Get the component (VEVENT/VTODO/VJOURNAL)
            component = None
            comp_type_name = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    component = getattr(vcal, comp_type)
                    comp_type_name = comp_type.upper()
                    break

            if not component:
                logger.warning("No schedulable component in REQUEST")
                return httputils.BAD_REQUEST

            # Extract UID, SEQUENCE, and ORGANIZER
            if not hasattr(component, 'uid'):
                logger.warning("REQUEST missing UID")
                return httputils.BAD_REQUEST

            uid = component.uid.value
            sequence = component.sequence.value if hasattr(component, 'sequence') else 0

            if not hasattr(component, 'organizer'):
                logger.warning("REQUEST missing ORGANIZER")
                return httputils.BAD_REQUEST

            organizer_uri = component.organizer.value
            organizer_email = extract_email(organizer_uri)

            if not organizer_email:
                logger.warning(f"Invalid ORGANIZER email: {organizer_uri}")
                return httputils.BAD_REQUEST

            # Verify user is authorized as organizer (or is their delegate)
            from moreradicale.itip.router import validate_organizer_permission
            if not validate_organizer_permission(organizer_email, user, self.configuration, self.storage):
                logger.warning(f"User {user} not authorized as organizer {organizer_email}")
                return httputils.FORBIDDEN

            # Expand CUTYPE=GROUP attendees into individual members
            self._expand_groups(component)

            # Extract and route attendees
            if not hasattr(component, 'attendee'):
                logger.warning("REQUEST has no attendees")
                return httputils.BAD_REQUEST

            # Handle single or multiple attendees
            attendees_raw = component.contents.get('attendee', [])
            if not isinstance(attendees_raw, list):
                attendees_raw = [attendees_raw]

            # Check max attendees limit
            max_attendees = self.configuration.get("scheduling", "max_attendees")
            if len(attendees_raw) > max_attendees:
                logger.warning(f"Too many attendees: {len(attendees_raw)} > {max_attendees}")
                return httputils.FORBIDDEN

            # Build attendee list with routing
            itip_attendees = []
            for att in attendees_raw:
                att_email = extract_email(att.value)
                if not att_email:
                    continue

                # Get PARTSTAT
                partstat_str = 'NEEDS-ACTION'
                if hasattr(att, 'params') and 'PARTSTAT' in att.params:
                    partstat_str = att.params['PARTSTAT'][0]

                # RFC 6638: Get SCHEDULE-AGENT parameter
                schedule_agent = ScheduleAgent.SERVER  # Default
                if hasattr(att, 'params') and 'SCHEDULE-AGENT' in att.params:
                    agent_str = att.params['SCHEDULE-AGENT'][0].upper()
                    try:
                        schedule_agent = ScheduleAgent(agent_str)
                    except ValueError:
                        logger.warning(f"Unknown SCHEDULE-AGENT value: {agent_str}, using SERVER")
                        schedule_agent = ScheduleAgent.SERVER

                # Extract CUTYPE (INDIVIDUAL, ROOM, RESOURCE, GROUP, etc.)
                cutype = 'INDIVIDUAL'
                if hasattr(att, 'params') and 'CUTYPE' in att.params:
                    cutype = att.params['CUTYPE'][0].upper()

                # Create ITIPAttendee
                itip_attendee = ITIPAttendee(
                    email=att_email,
                    partstat=AttendeePartStat(partstat_str),
                    schedule_agent=schedule_agent,
                    cutype=cutype
                )

                # Route attendee
                is_internal, principal_path = route_attendee(att_email, self.storage)
                itip_attendee.is_internal = is_internal
                itip_attendee.principal_path = principal_path

                itip_attendees.append(itip_attendee)

            if not itip_attendees:
                logger.warning("No valid attendees in REQUEST")
                return httputils.BAD_REQUEST

            # Create ITIPMessage
            itip_msg = ITIPMessage(
                method=ITIPMethod.REQUEST,
                uid=uid,
                sequence=sequence,
                organizer=organizer_email,
                attendees=itip_attendees,
                component_type=comp_type_name,
                icalendar_text=ical_text
            )

            # Deliver to internal attendees
            self._deliver_internal(itip_msg)

            # Process resource auto-accept for ROOM/RESOURCE attendees
            # This checks for conflicts and auto-accepts if available
            self._process_resource_auto_accept(itip_msg, vcal, component)

            # Deliver to external attendees via email
            try:
                self._deliver_external(itip_msg)
            except Exception as e:
                logger.error(f"External delivery failed: {e}", exc_info=True)

            # Update organizer's calendar with SCHEDULE-STATUS for each attendee
            self._update_organizer_calendar_schedule_status(
                organizer_email, uid, itip_attendees
            )

            # Build schedule-response with status for each attendee
            response = ET.Element(xmlutils.make_clark("C:schedule-response"))

            for attendee in itip_attendees:
                response_elem = ET.SubElement(response, xmlutils.make_clark("C:response"))

                # Recipient
                recipient = ET.SubElement(response_elem, xmlutils.make_clark("C:recipient"))
                href = ET.SubElement(recipient, xmlutils.make_clark("D:href"))
                href.text = f"mailto:{attendee.email}"

                # Request status - use tracked schedule_status if available
                request_status = ET.SubElement(response_elem,
                                               xmlutils.make_clark("C:request-status"))
                if attendee.schedule_status:
                    status = attendee.schedule_status
                    if status == ScheduleStatus.DELIVERED:
                        request_status.text = "2.0;Success"
                    elif status == ScheduleStatus.PENDING:
                        request_status.text = "2.8;NoAuthorization (external delivery not configured)"
                    elif status == ScheduleStatus.INVALID_USER:
                        request_status.text = "3.7;Invalid calendar user"
                    elif status == ScheduleStatus.DELIVERY_FAILED:
                        request_status.text = "5.1;Could not deliver"
                    else:
                        request_status.text = f"{status.value};Scheduling status"
                else:
                    # Fallback for any attendees without status
                    request_status.text = "1.0;Unknown status"

            logger.info(
                f"Processed REQUEST for {uid}: "
                f"{len([a for a in itip_attendees if a.is_internal])} internal, "
                f"{len([a for a in itip_attendees if not a.is_internal])} external"
            )

            headers = (
                ("Content-Type", "application/xml; charset=utf-8"),
            )
            return client.OK, headers, ET.tostring(response, encoding="utf-8"), None

        except Exception as e:
            logger.error(f"Error processing REQUEST: {e}", exc_info=True)
            return httputils.INTERNAL_SERVER_ERROR

    def _process_freebusy_request(self, vcal: vobject.base.Component, user: str, base_prefix: str):
        """
        Process iTIP VFREEBUSY REQUEST message.

        When a user wants to check attendees' availability before scheduling,
        they POST a VFREEBUSY REQUEST with the time range and attendee list.
        We query each internal attendee's calendars and return their busy times.

        RFC 6638 Section 5.3 - Free/Busy Scheduling

        Args:
            vcal: Parsed VCALENDAR with METHOD:REQUEST and VFREEBUSY component
            user: User posting the request (organizer)
            base_prefix: Base URL prefix for responses

        Returns:
            HTTP response with schedule-response containing VFREEBUSY for each attendee
        """
        from moreradicale import httputils, xmlutils
        from http import client
        import xml.etree.ElementTree as ET

        try:
            # Get the VFREEBUSY component
            vfreebusy = vcal.vfreebusy

            # Extract ORGANIZER
            if not hasattr(vfreebusy, 'organizer'):
                logger.warning("VFREEBUSY REQUEST missing ORGANIZER")
                return httputils.BAD_REQUEST

            organizer_uri = vfreebusy.organizer.value
            organizer_email = extract_email(organizer_uri)

            # Extract time range
            if not hasattr(vfreebusy, 'dtstart') or not hasattr(vfreebusy, 'dtend'):
                logger.warning("VFREEBUSY REQUEST missing DTSTART/DTEND")
                return httputils.BAD_REQUEST

            dtstart = vfreebusy.dtstart.value
            dtend = vfreebusy.dtend.value

            logger.info(f"Processing VFREEBUSY REQUEST from {organizer_email} "
                        f"for {dtstart} to {dtend}")

            # Extract ATTENDEEs
            if not hasattr(vfreebusy, 'attendee'):
                logger.warning("VFREEBUSY REQUEST missing ATTENDEE")
                return httputils.BAD_REQUEST

            attendees = vfreebusy.attendee_list if hasattr(vfreebusy, 'attendee_list') else [vfreebusy.attendee]

            # Build schedule-response with VFREEBUSY for each attendee
            response = ET.Element(xmlutils.make_clark("C:schedule-response"))

            for attendee in attendees:
                attendee_email = extract_email(attendee.value)
                if not attendee_email:
                    continue

                # Create response element for this attendee
                response_elem = ET.SubElement(response, xmlutils.make_clark("C:response"))

                # Add recipient
                recipient = ET.SubElement(response_elem, xmlutils.make_clark("C:recipient"))
                href = ET.SubElement(recipient, xmlutils.make_clark("D:href"))
                href.text = f"mailto:{attendee_email}"

                # Route attendee
                is_internal, attendee_principal = route_attendee(attendee_email, self.storage)

                if not is_internal:
                    # External attendee - return request-status indicating unavailable
                    request_status = ET.SubElement(response_elem,
                                                   xmlutils.make_clark("C:request-status"))
                    request_status.text = "3.7;Invalid calendar user"
                    logger.info(f"VFREEBUSY: External attendee {attendee_email} - no data available")
                    continue

                # Calculate busy times for internal attendee
                try:
                    freebusy_ical = self._calculate_freebusy(
                        attendee_principal, attendee_email, organizer_email,
                        dtstart, dtend
                    )

                    # Success - add calendar-data with VFREEBUSY REPLY
                    request_status = ET.SubElement(response_elem,
                                                   xmlutils.make_clark("C:request-status"))
                    request_status.text = "2.0;Success"

                    calendar_data = ET.SubElement(response_elem,
                                                  xmlutils.make_clark("C:calendar-data"))
                    calendar_data.text = freebusy_ical

                    logger.info(f"VFREEBUSY: Returned busy times for {attendee_email}")

                except Exception as e:
                    logger.error(f"Failed to calculate free/busy for {attendee_email}: {e}",
                                 exc_info=True)
                    request_status = ET.SubElement(response_elem,
                                                   xmlutils.make_clark("C:request-status"))
                    request_status.text = "5.3;No scheduling support for user"

            headers = (
                ("Content-Type", "application/xml; charset=utf-8"),
            )

            return client.OK, headers, ET.tostring(response, encoding="unicode"), None

        except Exception as e:
            logger.error(f"Error processing VFREEBUSY REQUEST: {e}", exc_info=True)
            return httputils.INTERNAL_SERVER_ERROR

    def _calculate_freebusy(self, principal_path: str, attendee_email: str,
                            organizer_email: str, dtstart, dtend) -> str:
        """
        Calculate free/busy times for a user within a time range.

        Scans all calendars under the user's principal and returns busy periods.
        If VAVAILABILITY components are present (RFC 7953), they are used to
        determine when the user is generally available vs unavailable.

        RFC 7953 Algorithm:
        1. Collect event-based busy periods (VEVENT with TRANSP:OPAQUE)
        2. Apply VAVAILABILITY patterns (sorted by priority)
        3. Times outside AVAILABLE slots become BUSY-UNAVAILABLE
        4. Combine with actual event busy times

        Args:
            principal_path: User's principal path (e.g., /alice/)
            attendee_email: Attendee's email address
            organizer_email: Organizer's email address
            dtstart: Start of time range
            dtend: End of time range

        Returns:
            iCalendar text with METHOD:REPLY and VFREEBUSY component
        """
        from moreradicale.itip import availability
        from vobject.icalendar import utc as vobj_utc
        from datetime import datetime

        # Build time-range filter element for the query
        # Convert datetime to ISO format strings
        if hasattr(dtstart, 'strftime'):
            start_str = dtstart.strftime('%Y%m%dT%H%M%SZ')
        else:
            start_str = str(dtstart).replace('-', '').replace(':', '')

        if hasattr(dtend, 'strftime'):
            end_str = dtend.strftime('%Y%m%dT%H%M%SZ')
        else:
            end_str = str(dtend).replace('-', '').replace(':', '')

        # Collect all busy periods
        busy_periods = []

        # Discover all calendar collections under user's principal
        discovered = list(self.storage.discover(principal_path, depth="1"))

        for collection in discovered:
            # Skip non-calendar collections
            if not hasattr(collection, 'tag') or collection.tag != 'VCALENDAR':
                continue

            # Skip schedule-inbox (not real events)
            if 'schedule-inbox' in collection.path.lower():
                continue

            # Get all items in calendar
            try:
                hrefs = list(collection._list())

                for href in hrefs:
                    item = collection._get(href)
                    if not item:
                        continue

                    # Process each component in the item
                    vcal = item.vobject_item

                    for subcomp in vcal.getChildren():
                        if subcomp.name != 'VEVENT':
                            continue

                        # Check TRANSP - ignore TRANSPARENT events
                        transp = getattr(subcomp, 'transp', None)
                        if transp and transp.value.upper() == 'TRANSPARENT':
                            continue

                        # Determine FBTYPE based on STATUS
                        status = getattr(subcomp, 'status', None)
                        if status:
                            status_val = status.value.upper()
                            if status_val == 'CANCELLED':
                                continue  # Skip cancelled events
                            elif status_val == 'TENTATIVE':
                                fbtype = 'BUSY-TENTATIVE'
                            else:
                                fbtype = 'BUSY'
                        else:
                            fbtype = 'BUSY'

                        # Get occurrences within time range
                        occurrences = self._get_event_occurrences(
                            subcomp, dtstart, dtend
                        )

                        for occ_start, occ_end in occurrences:
                            busy_periods.append((occ_start, occ_end, fbtype))

            except Exception as e:
                logger.warning(f"Error reading calendar {collection.path}: {e}")
                continue

        # Sort and merge overlapping periods (optional optimization)
        busy_periods.sort(key=lambda x: x[0])

        # RFC 7953: Apply VAVAILABILITY if present
        # This adds BUSY-UNAVAILABLE periods for times outside AVAILABLE slots
        try:
            avail_processor = availability.AvailabilityProcessor(
                self.storage, self.configuration
            )
            busy_periods = avail_processor.calculate_freebusy_with_availability(
                principal_path, dtstart, dtend, busy_periods
            )
            logger.debug(f"Applied VAVAILABILITY for {principal_path}, "
                         f"resulting in {len(busy_periods)} busy periods")
        except Exception as e:
            logger.warning(f"Error processing VAVAILABILITY for {principal_path}: {e}")
            # Continue with event-only busy periods

        # Build VFREEBUSY REPLY
        reply = vobject.iCalendar()
        reply.add('method').value = 'REPLY'

        vfb = reply.add('vfreebusy')

        # Set required properties
        vfb.add('dtstamp').value = datetime.now(vobj_utc)
        vfb.add('dtstart').value = dtstart if hasattr(dtstart, 'tzinfo') else dtstart
        vfb.add('dtend').value = dtend if hasattr(dtend, 'tzinfo') else dtend

        # Add organizer and attendee
        vfb.add('organizer').value = f"mailto:{organizer_email}"
        att = vfb.add('attendee')
        att.value = f"mailto:{attendee_email}"

        # Add busy periods
        for start, end, fbtype in busy_periods:
            fb = vfb.add('freebusy')
            # Format as PERIOD: start/end
            if hasattr(start, 'strftime'):
                start_str = start.strftime('%Y%m%dT%H%M%SZ')
            else:
                start_str = str(start)
            if hasattr(end, 'strftime'):
                end_str = end.strftime('%Y%m%dT%H%M%SZ')
            else:
                end_str = str(end)

            fb.value = [(start, end)]
            fb.params['FBTYPE'] = [fbtype]

        return reply.serialize()

    def _get_event_occurrences(self, vevent, range_start, range_end) -> list:
        """
        Get all occurrences of an event within a time range.

        Handles both single events and recurring events with RRULE.

        Args:
            vevent: VEVENT vobject component
            range_start: Start of time range
            range_end: End of time range

        Returns:
            List of (start, end) tuples for each occurrence
        """
        from datetime import datetime, timedelta
        from vobject.icalendar import utc as vobj_utc

        occurrences = []

        # Get event start/end
        if not hasattr(vevent, 'dtstart'):
            return occurrences

        event_start = vevent.dtstart.value
        event_end = vevent.dtend.value if hasattr(vevent, 'dtend') else None

        # Calculate duration
        if event_end:
            if hasattr(event_start, 'hour') and hasattr(event_end, 'hour'):
                duration = event_end - event_start
            else:
                # All-day event
                duration = timedelta(days=1)
        else:
            # Default 1 hour duration
            duration = timedelta(hours=1)

        # Normalize range to datetime for comparison
        if hasattr(range_start, 'hour'):
            range_start_dt = range_start
        else:
            range_start_dt = datetime.combine(range_start, datetime.min.time())

        if hasattr(range_end, 'hour'):
            range_end_dt = range_end
        else:
            range_end_dt = datetime.combine(range_end, datetime.max.time())

        # Make timezone-aware if needed
        if hasattr(range_start_dt, 'tzinfo') and range_start_dt.tzinfo is None:
            range_start_dt = range_start_dt.replace(tzinfo=vobj_utc)
        if hasattr(range_end_dt, 'tzinfo') and range_end_dt.tzinfo is None:
            range_end_dt = range_end_dt.replace(tzinfo=vobj_utc)

        # Check for RRULE (recurring event)
        if hasattr(vevent, 'rrule'):
            try:
                # Use dateutil for RRULE expansion

                # Get the rruleset from vobject
                if hasattr(vevent, 'rruleset'):
                    rruleset = vevent.rruleset
                else:
                    # Build rruleset manually
                    rruleset = vevent.getrruleset(addRDate=True)

                if rruleset:
                    # Limit occurrences to prevent runaway expansion
                    max_occurrences = 1000
                    count = 0

                    for occ_start in rruleset:
                        if count >= max_occurrences:
                            break

                        # Make timezone-aware for comparison
                        if hasattr(occ_start, 'tzinfo') and occ_start.tzinfo is None:
                            occ_start = occ_start.replace(tzinfo=vobj_utc)

                        occ_end = occ_start + duration

                        # Check if occurrence is in range
                        if occ_end <= range_start_dt:
                            continue  # Before range
                        if occ_start >= range_end_dt:
                            break  # Past range, stop

                        occurrences.append((occ_start, occ_end))
                        count += 1

            except Exception as e:
                logger.warning(f"Error expanding RRULE: {e}")
                # Fall back to single occurrence
                if hasattr(event_start, 'tzinfo') and event_start.tzinfo is None:
                    event_start = event_start.replace(tzinfo=vobj_utc)
                event_end_dt = event_start + duration
                if event_end_dt > range_start_dt and event_start < range_end_dt:
                    occurrences.append((event_start, event_end_dt))

        else:
            # Single occurrence - check if in range
            if hasattr(event_start, 'tzinfo') and event_start.tzinfo is None:
                event_start = event_start.replace(tzinfo=vobj_utc)
            event_end_dt = event_start + duration

            if event_end_dt > range_start_dt and event_start < range_end_dt:
                occurrences.append((event_start, event_end_dt))

        return occurrences

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

    def _check_sequence_ordering(
            self,
            event_path: str,
            collection,
            incoming_sequence: int,
            recurrence_id: Optional[str] = None
    ) -> Tuple[bool, int]:
        """
        Check if incoming message has valid sequence ordering per RFC 5546 §2.1.4.

        Messages with SEQUENCE less than the stored event's SEQUENCE should be
        rejected as stale/outdated to prevent processing out-of-order messages.

        Args:
            event_path: Path to the stored event
            collection: Calendar collection containing the event
            incoming_sequence: SEQUENCE number from incoming iTIP message
            recurrence_id: Optional RECURRENCE-ID for specific occurrence

        Returns:
            Tuple of (is_valid, stored_sequence):
            - is_valid: True if incoming sequence is >= stored sequence
            - stored_sequence: The current stored SEQUENCE value
        """
        try:
            href = event_path.split('/')[-1]
            item = collection._get(href)

            if not item:
                # Event doesn't exist, any sequence is valid
                return True, 0

            vcal = item.vobject_item
            stored_sequence = 0

            # Find the appropriate component
            for subcomp in vcal.getChildren():
                if subcomp.name not in ('VEVENT', 'VTODO', 'VJOURNAL'):
                    continue

                # If recurrence_id specified, look for that specific occurrence
                if recurrence_id:
                    if hasattr(subcomp, 'recurrence_id'):
                        comp_recur_id = self._normalize_recurrence_id(
                            subcomp.recurrence_id.value
                        )
                        target_recur_id = self._normalize_recurrence_id(recurrence_id)
                        if comp_recur_id != target_recur_id:
                            continue
                    else:
                        continue
                else:
                    # For non-recurring or master component
                    if hasattr(subcomp, 'recurrence_id'):
                        continue  # Skip exception components when looking for master

                # Found target component, get its SEQUENCE
                if hasattr(subcomp, 'sequence'):
                    try:
                        stored_sequence = int(subcomp.sequence.value)
                    except (ValueError, TypeError):
                        stored_sequence = 0
                break

            # RFC 5546 §2.1.4: Incoming SEQUENCE must be >= stored SEQUENCE
            is_valid = incoming_sequence >= stored_sequence

            if not is_valid:
                logger.warning(
                    f"Rejecting stale iTIP message: incoming SEQUENCE {incoming_sequence} "
                    f"< stored SEQUENCE {stored_sequence} for {event_path}"
                )
            else:
                logger.debug(
                    f"Sequence check passed: incoming {incoming_sequence} >= stored {stored_sequence}"
                )

            return is_valid, stored_sequence

        except Exception as e:
            logger.error(f"Error checking sequence ordering: {e}", exc_info=True)
            # On error, allow processing to continue
            return True, 0

    def _update_attendee_partstat(self, event_path: str, collection,
                                  attendee_email: str, new_partstat: str,
                                  recurrence_id: Optional[str] = None,
                                  delegated_to: Optional[str] = None) -> bool:
        """
        Update the PARTSTAT of an attendee in an event.

        For recurring events, if recurrence_id is specified, only the matching
        occurrence is updated. If no exception component exists for that
        occurrence, one is created from the master template.

        RFC 5546 Delegation: When delegated_to is specified, also adds the
        DELEGATED-TO parameter to the attendee line.

        Args:
            event_path: Full path to event (e.g., /alice/calendar.ics/meeting.ics)
            collection: Calendar collection containing the event
            attendee_email: Email of attendee to update
            new_partstat: New participation status
            recurrence_id: Optional RECURRENCE-ID for specific occurrence (RFC 5545)
            delegated_to: Optional email of delegate (RFC 5546)

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
            master_component = None

            # First pass: find the target component
            for subcomp in vcal.getChildren():
                if subcomp.name not in ('VEVENT', 'VTODO', 'VJOURNAL'):
                    continue

                # Track master component (no RECURRENCE-ID)
                if not hasattr(subcomp, 'recurrence_id'):
                    master_component = subcomp

                if recurrence_id:
                    # Looking for specific occurrence
                    if hasattr(subcomp, 'recurrence_id'):
                        # Normalize RECURRENCE-ID comparison (handle different formats)
                        comp_recur_id = self._normalize_recurrence_id(
                            subcomp.recurrence_id.value
                        )
                        target_recur_id = self._normalize_recurrence_id(recurrence_id)

                        if comp_recur_id != target_recur_id:
                            continue  # Not the target occurrence
                    else:
                        continue  # Skip master when looking for specific occurrence
                else:
                    # No recurrence_id specified - update master or all occurrences
                    # For non-recurring events, there's only one component
                    pass

                # Found target component - update the attendee
                if hasattr(subcomp, 'attendee_list'):
                    attendees = subcomp.attendee_list
                else:
                    attendees = [subcomp.attendee] if hasattr(subcomp, 'attendee') else []

                for att in attendees:
                    att_email = extract_email(att.value)
                    if att_email and att_email.lower() == attendee_email.lower():
                        # Update PARTSTAT
                        att.params['PARTSTAT'] = [new_partstat]
                        # RFC 5546: Add DELEGATED-TO if delegation
                        if delegated_to and new_partstat == "DELEGATED":
                            att.params['DELEGATED-TO'] = [f"mailto:{delegated_to}"]
                        updated = True
                        log_parts = [f"Updated PARTSTAT for {attendee_email} to {new_partstat}"]
                        if delegated_to:
                            log_parts.append(f"DELEGATED-TO: {delegated_to}")
                        if recurrence_id:
                            log_parts.append(f"RECURRENCE-ID: {recurrence_id}")
                        logger.debug(" ".join(log_parts))
                        break

                if updated:
                    # Note: Do NOT increment SEQUENCE here - per RFC 5546, SEQUENCE is
                    # only incremented by the Organizer when making significant changes.
                    # REPLY processing (PARTSTAT updates) should preserve the original
                    # SEQUENCE value.
                    break

            # If recurrence_id specified but no matching exception found,
            # create exception from master component
            if recurrence_id and not updated and master_component:
                updated = self._create_recurrence_exception(
                    vcal, master_component, attendee_email,
                    new_partstat, recurrence_id
                )

            if not updated:
                logger.warning(
                    f"Attendee {attendee_email} not found in event"
                    + (f" (RECURRENCE-ID: {recurrence_id})" if recurrence_id else "")
                )
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

    def _handle_delegation(self, event_path: str, collection, delegator_email: str,
                           delegate_email: str, component: vobject.base.Component,
                           base_prefix: str, recurrence_id: Optional[str] = None):
        """
        Handle RFC 5546 delegation workflow.

        When an attendee delegates their attendance to someone else:
        1. Update delegator's PARTSTAT to DELEGATED with DELEGATED-TO
        2. Add delegate as new ATTENDEE with DELEGATED-FROM
        3. Send REQUEST to delegate (inbox for internal, email for external)

        RFC 5546 Section 2.2.4 - Delegation:
        "When delegating, the Attendee MUST set PARTSTAT to DELEGATED and
        include the DELEGATED-TO parameter. The delegate is then added as
        a new ATTENDEE with DELEGATED-FROM parameter."

        Args:
            event_path: Full path to event
            collection: Calendar collection containing the event
            delegator_email: Email of attendee delegating
            delegate_email: Email of person being delegated to
            component: VEVENT/VTODO/VJOURNAL component from REPLY
            base_prefix: Base URL prefix for responses
            recurrence_id: Optional RECURRENCE-ID for specific occurrence

        Returns:
            HTTP response with schedule-response, or None to fall through
        """
        try:
            # Get the item
            href = event_path.split('/')[-1]
            item = collection._get(href)

            if not item:
                logger.warning(f"Event not found for delegation: {event_path}")
                return None

            vcal = item.vobject_item
            updated = False
            delegate_added = False
            master_component = None

            # Find the target component
            for subcomp in vcal.getChildren():
                if subcomp.name not in ('VEVENT', 'VTODO', 'VJOURNAL'):
                    continue

                # Track master component (no RECURRENCE-ID) for exception creation
                if not hasattr(subcomp, 'recurrence_id'):
                    master_component = subcomp

                # Handle recurrence_id if specified
                if recurrence_id:
                    if hasattr(subcomp, 'recurrence_id'):
                        comp_recur_id = self._normalize_recurrence_id(
                            subcomp.recurrence_id.value
                        )
                        target_recur_id = self._normalize_recurrence_id(recurrence_id)
                        if comp_recur_id != target_recur_id:
                            continue
                    else:
                        continue  # Skip master when looking for specific occurrence

                # Update delegator's PARTSTAT and add DELEGATED-TO
                if hasattr(subcomp, 'attendee_list'):
                    attendees = subcomp.attendee_list
                else:
                    attendees = [subcomp.attendee] if hasattr(subcomp, 'attendee') else []

                for att in attendees:
                    att_email = extract_email(att.value)
                    if att_email and att_email.lower() == delegator_email.lower():
                        att.params['PARTSTAT'] = ['DELEGATED']
                        att.params['DELEGATED-TO'] = [f"mailto:{delegate_email}"]
                        updated = True
                        logger.debug(
                            f"Updated delegator {delegator_email}: "
                            f"PARTSTAT=DELEGATED, DELEGATED-TO={delegate_email}"
                        )
                        break

                if not updated:
                    logger.warning(f"Delegator {delegator_email} not found in event")
                    return None

                # Add delegate as new ATTENDEE with DELEGATED-FROM
                # Get properties from delegator for the delegate
                delegator_att = None
                for att in attendees:
                    att_email = extract_email(att.value)
                    if att_email and att_email.lower() == delegator_email.lower():
                        delegator_att = att
                        break

                # Create new attendee for delegate
                delegate_att = subcomp.add('attendee')
                delegate_att.value = f"mailto:{delegate_email}"
                delegate_att.params['PARTSTAT'] = ['NEEDS-ACTION']
                delegate_att.params['DELEGATED-FROM'] = [f"mailto:{delegator_email}"]
                delegate_att.params['ROLE'] = ['REQ-PARTICIPANT']
                delegate_att.params['CUTYPE'] = ['INDIVIDUAL']
                delegate_att.params['RSVP'] = ['TRUE']

                delegate_added = True
                logger.info(
                    f"Added delegate {delegate_email} with DELEGATED-FROM={delegator_email}"
                )

                # Increment SEQUENCE
                if hasattr(subcomp, 'sequence'):
                    subcomp.sequence.value = str(int(subcomp.sequence.value) + 1)
                else:
                    subcomp.add('sequence').value = '1'

                break

            # If recurrence_id specified but no matching exception found,
            # create exception from master component for delegation
            if recurrence_id and not updated and master_component:
                result = self._create_delegation_exception(
                    vcal, master_component, delegator_email,
                    delegate_email, recurrence_id
                )
                if result:
                    updated = True
                    delegate_added = True
                    logger.info(
                        f"Created recurrence exception for delegation: "
                        f"{recurrence_id}, delegator={delegator_email}, "
                        f"delegate={delegate_email}"
                    )

            if not updated or not delegate_added:
                logger.warning(
                    "Delegation update incomplete"
                    + (f" (RECURRENCE-ID: {recurrence_id})" if recurrence_id else "")
                )
                return None

            # Save the updated event
            updated_item = radicale_item.Item(
                collection_path=collection.path,
                vobject_item=vcal
            )
            updated_item.prepare()
            collection.upload(href, updated_item)

            logger.info(f"Saved delegation update to {event_path}")

            # Now send REQUEST to the delegate
            self._send_delegation_request(
                vcal, delegate_email, delegator_email, base_prefix
            )

            # Return success response
            return self._build_schedule_response_success(base_prefix, delegator_email)

        except Exception as e:
            logger.error(f"Error handling delegation: {e}", exc_info=True)
            return None

    def _send_delegation_request(self, vcal: vobject.base.Component,
                                 delegate_email: str, delegator_email: str,
                                 base_prefix: str) -> None:
        """
        Send a REQUEST to the delegate inviting them to the event.

        For internal delegates, delivers to their schedule-inbox.
        For external delegates, sends via email.

        Args:
            vcal: Updated VCALENDAR with delegate added
            delegate_email: Email of the delegate
            delegator_email: Email of the person who delegated
            base_prefix: Base URL prefix
        """
        try:
            # Generate iTIP REQUEST for the delegate
            request_ical = self._generate_itip_request_for_delegation(vcal, delegate_email)

            # Route the delegate
            is_internal, delegate_principal = route_attendee(delegate_email, self.storage)

            if is_internal:
                # Deliver to delegate's schedule-inbox
                inbox_path = get_inbox_path(delegate_principal)
                discovered = list(self.storage.discover(inbox_path, depth="0"))

                if not discovered:
                    logger.warning(f"Delegate inbox not found: {inbox_path}")
                    return

                inbox = discovered[0]

                # Create item
                request_vobject = vobject.readOne(request_ical)
                request_item = radicale_item.Item(
                    collection_path=inbox.path,
                    vobject_item=request_vobject
                )
                request_item.prepare()

                # Generate filename
                uid = self._extract_uid(request_ical)
                import time
                timestamp = int(time.time())
                filename = f"{uid}-delegation-{timestamp}.ics"

                # Upload
                inbox.upload(filename, request_item)
                logger.info(
                    f"Delivered delegation REQUEST to {delegate_email} inbox "
                    f"(delegated from {delegator_email})"
                )

            else:
                # External delegate - send via email
                if not self.email_config:
                    logger.warning(
                        f"Cannot send delegation to external delegate {delegate_email}: "
                        "email not configured"
                    )
                    return

                # Extract organizer
                organizer_email = self._extract_field(request_ical, "ORGANIZER")
                organizer_email = extract_email(organizer_email) if organizer_email else "unknown"

                # Get UID
                uid = self._extract_uid(request_ical)

                # Create ITIPMessage
                itip_attendee = ITIPAttendee(
                    email=delegate_email,
                    is_internal=False,
                    principal_path=None,
                    cn=None,
                    delegated_from=delegator_email
                )

                itip_msg = ITIPMessage(
                    method=ITIPMethod.REQUEST,
                    uid=uid or "unknown",
                    sequence=0,
                    organizer=organizer_email,
                    attendees=[itip_attendee],
                    component_type="VEVENT",
                    icalendar_text=request_ical
                )

                # Send via email
                self._deliver_external(itip_msg)
                logger.info(
                    f"Sent delegation REQUEST email to {delegate_email} "
                    f"(delegated from {delegator_email})"
                )

        except Exception as e:
            logger.error(f"Error sending delegation request: {e}", exc_info=True)

    def _generate_itip_request_for_delegation(self, vcal: vobject.base.Component,
                                              delegate_email: str) -> str:
        """
        Generate iTIP REQUEST for a delegated attendee.

        Creates a REQUEST message specifically for the delegate, containing
        the updated event with DELEGATED-FROM information.

        Args:
            vcal: Updated VCALENDAR with delegate added
            delegate_email: Email of the delegate

        Returns:
            iCalendar text with METHOD:REQUEST
        """
        # Clone the calendar for the REQUEST
        itip_vcal = vobject.newFromBehavior('VCALENDAR')
        itip_vcal.add('version').value = '2.0'
        itip_vcal.add('prodid').value = '-//Radicale//NONSGML Radicale Server//EN'
        itip_vcal.add('method').value = 'REQUEST'

        # Get the component
        component = None
        for comp_type in ('vevent', 'vtodo', 'vjournal'):
            if hasattr(vcal, comp_type):
                component = getattr(vcal, comp_type)
                break

        if not component:
            return ""

        # Clone the component
        itip_comp = itip_vcal.add(component.name.lower())

        # Copy all properties
        for prop in component.getChildren():
            if prop.name.lower() not in ('method',):
                itip_comp.add(prop.name).value = prop.value
                # Copy parameters
                if hasattr(prop, 'params'):
                    for param_name, param_values in prop.params.items():
                        itip_comp.contents[prop.name.lower()][-1].params[param_name] = param_values

        return itip_vcal.serialize()

    def _update_organizer_calendar_schedule_status(
            self,
            organizer_email: str,
            uid: str,
            attendees: List[ITIPAttendee]
    ) -> None:
        """
        Update organizer's calendar event with SCHEDULE-STATUS for each attendee.

        RFC 6638 Section 3.2.9 defines SCHEDULE-STATUS as a property parameter
        on ATTENDEE that indicates the result of scheduling delivery.

        Args:
            organizer_email: Organizer's email address
            uid: Event UID
            attendees: List of attendees with schedule_status set

        Note:
            Failures are logged but don't interrupt the scheduling flow.
        """
        try:
            # Find organizer's principal
            is_internal, principal_path = route_attendee(organizer_email, self.storage)

            if not is_internal or not principal_path:
                logger.debug(f"Organizer {organizer_email} not internal, skipping SCHEDULE-STATUS update")
                return

            # Find the event in organizer's calendars
            # Look for .ics files containing the UID in their calendars
            calendar_path = f"{principal_path}calendar/"
            discovered = list(self.storage.discover(calendar_path, depth="1"))

            event_item = None
            event_collection = None
            event_filename = None

            for resource in discovered:
                if hasattr(resource, 'get_meta') and resource.path.endswith('.ics'):
                    try:
                        item_vcal = resource.vobject_item
                        component = None
                        for comp_type in ('vevent', 'vtodo', 'vjournal'):
                            if hasattr(item_vcal, comp_type):
                                component = getattr(item_vcal, comp_type)
                                break
                        if component and hasattr(component, 'uid'):
                            if component.uid.value == uid:
                                event_item = resource
                                # Get collection path
                                event_path = resource.path
                                collection_path = "/".join(event_path.split("/")[:-1]) + "/"
                                event_filename = event_path.split("/")[-1]
                                collections = list(self.storage.discover(collection_path, depth="0"))
                                if collections:
                                    event_collection = collections[0]
                                break
                    except Exception:
                        continue

            if not event_item or not event_collection:
                logger.debug(f"Event {uid} not found in organizer's calendar, skipping SCHEDULE-STATUS update")
                return

            # Update ATTENDEE properties with SCHEDULE-STATUS
            item_vcal = event_item.vobject_item
            component = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(item_vcal, comp_type):
                    component = getattr(item_vcal, comp_type)
                    break

            if not component:
                return

            # Build email to status mapping
            status_map = {
                att.email.lower(): att.schedule_status
                for att in attendees
                if att.schedule_status
            }

            if not status_map:
                logger.debug("No schedule statuses to update")
                return

            # Update each attendee's SCHEDULE-STATUS
            attendee_list = component.contents.get('attendee', [])
            updated = False

            for att_prop in attendee_list:
                att_email = extract_email(att_prop.value)
                if att_email and att_email.lower() in status_map:
                    status = status_map[att_email.lower()]
                    # RFC 6638: SCHEDULE-STATUS is a quoted list of status values
                    att_prop.params['SCHEDULE-STATUS'] = [status.value]
                    updated = True
                    logger.debug(f"Set SCHEDULE-STATUS={status.value} for {att_email}")

            if updated:
                # Save the updated event
                updated_item = radicale_item.Item(
                    collection_path=event_collection.path,
                    vobject_item=item_vcal
                )
                updated_item.prepare()
                event_collection.upload(event_filename, updated_item)
                logger.info(f"Updated SCHEDULE-STATUS for event {uid} in organizer's calendar")

        except Exception as e:
            # Don't fail scheduling if status update fails
            logger.error(f"Failed to update SCHEDULE-STATUS for {uid}: {e}", exc_info=True)

    def _notify_delegator_of_decline(
            self,
            event_path: str,
            collection,
            vcal: vobject.base.Component,
            component: vobject.base.Component,
            delegate_email: str,
            delegator_email: str,
            organizer_email: str,
            base_prefix: str,
            recurrence_id: Optional[str] = None
    ) -> None:
        """
        Notify the original delegator when their delegate declines.

        Per RFC 5546 Section 2.2.4, when a delegate declines an invitation,
        the original delegator should be notified so they can:
        1. Attend the event themselves
        2. Find another delegate
        3. Decline themselves

        We send a custom iTIP message to the delegator's schedule-inbox
        containing the event with the delegate's DECLINED status.

        Args:
            event_path: Full path to the organizer's event
            collection: Calendar collection containing the event
            vcal: Original VCALENDAR from the REPLY
            component: VEVENT/VTODO/VJOURNAL component
            delegate_email: Email of the delegate who declined
            delegator_email: Email of the original delegator to notify
            organizer_email: Email of the organizer
            base_prefix: Base URL prefix
            recurrence_id: Optional RECURRENCE-ID for specific occurrence
        """
        try:
            logger.info(
                f"Delegate {delegate_email} declined invitation "
                f"(delegated from {delegator_email}). Notifying delegator..."
            )

            # Route the delegator
            is_internal, delegator_principal = route_attendee(delegator_email, self.storage)

            if not is_internal:
                # For external delegators, we'd send email notification
                logger.info(
                    f"External delegator {delegator_email} - "
                    "delegate decline notification not yet supported for external users"
                )
                return

            # Get the current event from organizer's calendar to build notification
            href = event_path.rsplit('/', 1)[-1]
            item = collection._get(href)

            if not item:
                logger.warning(
                    f"Could not find event {event_path} to build delegate decline notification"
                )
                return

            # Generate notification iTIP message
            notification_ical = self._generate_delegate_decline_notification(
                item.vobject_item, delegate_email, delegator_email,
                recurrence_id=recurrence_id
            )

            if not notification_ical:
                logger.warning("Failed to generate delegate decline notification")
                return

            # Deliver to delegator's schedule-inbox
            inbox_path = get_inbox_path(delegator_principal)
            discovered = list(self.storage.discover(inbox_path, depth="0"))

            if not discovered:
                logger.warning(
                    f"Delegator inbox not found: {inbox_path}. "
                    "Cannot deliver delegate decline notification."
                )
                return

            inbox = discovered[0]

            # Create inbox item
            from moreradicale.item import Item
            notification_item = Item(
                collection=inbox,
                vobject_item=vobject.readOne(notification_ical)
            )
            notification_item.prepare()

            # Generate unique filename
            uid = self._extract_uid(notification_ical)
            import time
            timestamp = int(time.time())
            filename = f"{uid}-delegate-declined-{timestamp}.ics"

            # Upload to inbox
            inbox.upload(filename, notification_item)
            logger.info(
                f"Delivered delegate decline notification to {delegator_email} inbox: "
                f"{delegate_email} declined the delegated invitation"
            )

        except Exception as e:
            logger.error(
                f"Error notifying delegator of decline: {e}", exc_info=True
            )

    def _generate_delegate_decline_notification(
            self,
            vcal: vobject.base.Component,
            delegate_email: str,
            delegator_email: str,
            recurrence_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Generate an iTIP REPLY notification for delegate decline.

        Creates a REPLY message that informs the delegator their delegate
        has declined. This allows the delegator's calendar client to:
        - Display notification about the decline
        - Prompt the delegator to take action (attend or find new delegate)

        Args:
            vcal: Current event VCALENDAR
            delegate_email: Email of the delegate who declined
            delegator_email: Email of the original delegator
            recurrence_id: Optional RECURRENCE-ID for specific occurrence

        Returns:
            iCalendar text with METHOD:REPLY or None on error
        """
        try:
            from datetime import datetime
            from vobject.icalendar import utc as vobj_utc

            # Create new VCALENDAR for the notification
            itip_vcal = vobject.newFromBehavior('VCALENDAR')
            itip_vcal.add('version').value = '2.0'
            itip_vcal.add('prodid').value = '-//Radicale//NONSGML Radicale Server//EN'
            # Use REPLY method - this is the delegate's reply forwarded to delegator
            itip_vcal.add('method').value = 'REPLY'

            # Find the schedulable component
            source_component = None
            comp_name = None
            for comp_type in ('vevent', 'vtodo', 'vjournal'):
                if hasattr(vcal, comp_type):
                    source_component = getattr(vcal, comp_type)
                    comp_name = comp_type
                    break

            if not source_component:
                logger.warning("No schedulable component found for decline notification")
                return None

            # If recurrence_id specified, find the matching component or use master
            target_component = source_component
            if recurrence_id:
                # Look for matching exception component
                for subcomp in vcal.getChildren():
                    if subcomp.name.lower() == comp_name:
                        if hasattr(subcomp, 'recurrence_id'):
                            rid_value = self._normalize_recurrence_id(subcomp.recurrence_id.value)
                            if rid_value == self._normalize_recurrence_id(recurrence_id):
                                target_component = subcomp
                                break

            # Create the notification component
            itip_comp = itip_vcal.add(comp_name)

            # Copy essential properties
            if hasattr(target_component, 'uid'):
                itip_comp.add('uid').value = target_component.uid.value

            # Use vobject's UTC timezone for compatibility
            itip_comp.add('dtstamp').value = datetime.now(vobj_utc)

            if hasattr(target_component, 'dtstart'):
                itip_comp.add('dtstart').value = target_component.dtstart.value

            if hasattr(target_component, 'dtend'):
                itip_comp.add('dtend').value = target_component.dtend.value
            elif hasattr(target_component, 'due'):
                itip_comp.add('due').value = target_component.due.value

            if hasattr(target_component, 'summary'):
                itip_comp.add('summary').value = target_component.summary.value

            if hasattr(target_component, 'organizer'):
                org = itip_comp.add('organizer')
                org.value = target_component.organizer.value
                if hasattr(target_component.organizer, 'params'):
                    for p_name, p_vals in target_component.organizer.params.items():
                        org.params[p_name] = p_vals

            # Add RECURRENCE-ID if applicable
            if recurrence_id and hasattr(target_component, 'recurrence_id'):
                rid = itip_comp.add('recurrence_id')
                rid.value = target_component.recurrence_id.value

            # Add the delegate as ATTENDEE with DECLINED status
            # Include DELEGATED-FROM to show the delegation chain
            delegate_att = itip_comp.add('attendee')
            delegate_att.value = f"mailto:{delegate_email}"
            delegate_att.params['PARTSTAT'] = ['DECLINED']
            delegate_att.params['DELEGATED-FROM'] = [f"mailto:{delegator_email}"]
            delegate_att.params['ROLE'] = ['REQ-PARTICIPANT']

            # Add X-RADICALE-DELEGATE-DECLINED to help calendar clients
            # understand this is a delegate decline notification
            itip_comp.add('x-radicale-delegate-declined').value = delegate_email

            # Add a COMMENT explaining the situation
            itip_comp.add('comment').value = (
                f"Your delegate {delegate_email} has declined this invitation. "
                "You may want to attend yourself or delegate to someone else."
            )

            return itip_vcal.serialize()

        except Exception as e:
            logger.error(
                f"Error generating delegate decline notification: {e}",
                exc_info=True
            )
            return None

    def _extract_uid(self, ical_text: str) -> Optional[str]:
        """
        Extract UID from iCalendar text.

        Args:
            ical_text: iCalendar text

        Returns:
            UID value or None
        """
        return self._extract_field(ical_text, "UID")

    def _normalize_recurrence_id(self, value) -> str:
        """
        Normalize RECURRENCE-ID value for comparison.

        Handles different formats:
        - datetime object
        - string with/without timezone
        - date object

        Args:
            value: RECURRENCE-ID value (various types)

        Returns:
            Normalized string representation
        """
        if hasattr(value, 'strftime'):
            # datetime or date object
            if hasattr(value, 'hour'):
                return value.strftime('%Y%m%dT%H%M%SZ')
            else:
                return value.strftime('%Y%m%d')
        # String - normalize by removing common variations
        s = str(value).replace('-', '').replace(':', '').replace('Z', '')
        # Handle VALUE=DATE format
        if 'T' not in s and len(s) == 8:
            return s  # Date only
        return s + 'Z' if 'T' in s else s

    def _create_recurrence_exception(self, vcal, master_component,
                                     attendee_email: str, new_partstat: str,
                                     recurrence_id: str) -> bool:
        """
        Create a recurrence exception component for a specific occurrence.

        When an attendee responds to a single occurrence of a recurring event
        but no exception component exists yet, we create one based on the
        master component.

        Args:
            vcal: Parent VCALENDAR
            master_component: The master VEVENT/VTODO/VJOURNAL
            attendee_email: Email of attendee to update
            new_partstat: New participation status
            recurrence_id: RECURRENCE-ID for the exception

        Returns:
            True if exception created successfully
        """
        try:
            # Create new exception component
            exception = vcal.add(master_component.name.lower())

            # Copy key properties from master
            if hasattr(master_component, 'uid'):
                exception.add('uid').value = master_component.uid.value
            if hasattr(master_component, 'summary'):
                exception.add('summary').value = master_component.summary.value
            if hasattr(master_component, 'organizer'):
                org = exception.add('organizer')
                org.value = master_component.organizer.value
                if hasattr(master_component.organizer, 'params'):
                    for k, v in master_component.organizer.params.items():
                        org.params[k] = v

            # Parse recurrence_id and set as proper datetime/date
            from datetime import datetime as dt
            from vobject.icalendar import utc as vobj_utc

            recurrence_dt = None
            is_date_only = False

            if hasattr(recurrence_id, 'strftime'):
                # Already a datetime/date object
                recurrence_dt = recurrence_id
                is_date_only = not hasattr(recurrence_id, 'hour')
            elif 'T' in str(recurrence_id):
                # DateTime format string
                normalized = self._normalize_recurrence_id(recurrence_id).replace('Z', '')
                try:
                    recurrence_dt = dt.strptime(normalized, '%Y%m%dT%H%M%S')
                    # Make it UTC-aware using vobject's UTC timezone
                    recurrence_dt = recurrence_dt.replace(tzinfo=vobj_utc)
                except ValueError as e:
                    logger.warning(f"Could not parse RECURRENCE-ID: {recurrence_id}: {e}")
                    return False
            else:
                # Date-only format
                try:
                    recurrence_dt = dt.strptime(str(recurrence_id), '%Y%m%d').date()
                    is_date_only = True
                except ValueError as e:
                    logger.warning(f"Could not parse RECURRENCE-ID date: {recurrence_id}: {e}")
                    return False

            # Set RECURRENCE-ID
            recur = exception.add('recurrence-id')
            recur.value = recurrence_dt

            # Set DTSTART/DTEND based on RECURRENCE-ID
            if not is_date_only:
                occurrence_start = recurrence_dt
                exception.add('dtstart').value = occurrence_start

                # Compute duration from master
                if hasattr(master_component, 'dtstart') and hasattr(master_component, 'dtend'):
                    master_start = master_component.dtstart.value
                    master_end = master_component.dtend.value
                    if hasattr(master_start, 'hour') and hasattr(master_end, 'hour'):
                        duration = master_end - master_start
                        exception.add('dtend').value = occurrence_start + duration
            else:
                # Date-only
                exception.add('dtstart').value = recurrence_dt

            # Add DTSTAMP
            exception.add('dtstamp').value = dt.now(vobj_utc)

            # Copy attendees from master, updating the responding attendee
            attendees_copied = False
            if hasattr(master_component, 'attendee_list'):
                master_attendees = master_component.attendee_list
            elif hasattr(master_component, 'attendee'):
                master_attendees = [master_component.attendee]
            else:
                master_attendees = []

            for master_att in master_attendees:
                att = exception.add('attendee')
                att.value = master_att.value

                # Copy params
                if hasattr(master_att, 'params'):
                    for k, v in master_att.params.items():
                        att.params[k] = list(v) if isinstance(v, list) else [v]

                # Update PARTSTAT for the responding attendee
                att_email = extract_email(master_att.value)
                if att_email and att_email.lower() == attendee_email.lower():
                    att.params['PARTSTAT'] = [new_partstat]
                    attendees_copied = True

            if not attendees_copied:
                logger.warning(
                    f"Attendee {attendee_email} not found in master component"
                )
                return False

            # Inherit SEQUENCE from master component (per RFC 5546)
            master_seq = '0'
            if hasattr(master_component, 'sequence'):
                try:
                    master_seq = str(int(master_component.sequence.value))
                except (ValueError, TypeError):
                    master_seq = '0'
            exception.add('sequence').value = master_seq

            logger.info(
                f"Created recurrence exception for {recurrence_id}, "
                f"updated {attendee_email} to {new_partstat}"
            )
            return True

        except Exception as e:
            logger.error(f"Error creating recurrence exception: {e}", exc_info=True)
            return False

    def _create_delegation_exception(self, vcal, master_component,
                                     delegator_email: str, delegate_email: str,
                                     recurrence_id: str) -> bool:
        """
        Create a recurrence exception for delegation of a specific occurrence.

        When an attendee delegates a specific occurrence of a recurring event
        but no exception component exists yet, we create one based on the
        master component with delegation applied.

        Args:
            vcal: Parent VCALENDAR
            master_component: The master VEVENT/VTODO/VJOURNAL
            delegator_email: Email of attendee delegating
            delegate_email: Email of delegate
            recurrence_id: RECURRENCE-ID for the exception

        Returns:
            True if exception created successfully
        """
        try:
            from datetime import datetime as dt
            from vobject.icalendar import utc as vobj_utc

            # Create new exception component
            exception = vcal.add(master_component.name.lower())

            # Copy key properties from master
            if hasattr(master_component, 'uid'):
                exception.add('uid').value = master_component.uid.value
            if hasattr(master_component, 'summary'):
                exception.add('summary').value = master_component.summary.value
            if hasattr(master_component, 'organizer'):
                org = exception.add('organizer')
                org.value = master_component.organizer.value
                if hasattr(master_component.organizer, 'params'):
                    for k, v in master_component.organizer.params.items():
                        org.params[k] = v
            if hasattr(master_component, 'location'):
                exception.add('location').value = master_component.location.value
            if hasattr(master_component, 'description'):
                exception.add('description').value = master_component.description.value

            # Parse recurrence_id and set as proper datetime/date
            recurrence_dt = None
            is_date_only = False

            if hasattr(recurrence_id, 'strftime'):
                recurrence_dt = recurrence_id
                is_date_only = not hasattr(recurrence_id, 'hour')
            elif 'T' in str(recurrence_id):
                normalized = self._normalize_recurrence_id(recurrence_id).replace('Z', '')
                try:
                    recurrence_dt = dt.strptime(normalized, '%Y%m%dT%H%M%S')
                    recurrence_dt = recurrence_dt.replace(tzinfo=vobj_utc)
                except ValueError as e:
                    logger.warning(f"Could not parse RECURRENCE-ID: {recurrence_id}: {e}")
                    return False
            else:
                try:
                    recurrence_dt = dt.strptime(str(recurrence_id), '%Y%m%d').date()
                    is_date_only = True
                except ValueError as e:
                    logger.warning(f"Could not parse RECURRENCE-ID date: {recurrence_id}: {e}")
                    return False

            # Set RECURRENCE-ID
            recur = exception.add('recurrence-id')
            recur.value = recurrence_dt

            # Set DTSTART/DTEND based on RECURRENCE-ID
            if not is_date_only:
                occurrence_start = recurrence_dt
                exception.add('dtstart').value = occurrence_start

                if hasattr(master_component, 'dtstart') and hasattr(master_component, 'dtend'):
                    master_start = master_component.dtstart.value
                    master_end = master_component.dtend.value
                    if hasattr(master_start, 'hour') and hasattr(master_end, 'hour'):
                        duration = master_end - master_start
                        exception.add('dtend').value = occurrence_start + duration
            else:
                exception.add('dtstart').value = recurrence_dt

            # Add DTSTAMP
            exception.add('dtstamp').value = dt.now(vobj_utc)

            # Copy attendees from master, applying delegation to delegator
            delegator_found = False
            if hasattr(master_component, 'attendee_list'):
                master_attendees = master_component.attendee_list
            elif hasattr(master_component, 'attendee'):
                master_attendees = [master_component.attendee]
            else:
                master_attendees = []

            for master_att in master_attendees:
                att = exception.add('attendee')
                att.value = master_att.value

                # Copy params
                if hasattr(master_att, 'params'):
                    for k, v in master_att.params.items():
                        att.params[k] = list(v) if isinstance(v, list) else [v]

                # Apply delegation to the delegator
                att_email = extract_email(master_att.value)
                if att_email and att_email.lower() == delegator_email.lower():
                    att.params['PARTSTAT'] = ['DELEGATED']
                    att.params['DELEGATED-TO'] = [f"mailto:{delegate_email}"]
                    delegator_found = True

            if not delegator_found:
                logger.warning(
                    f"Delegator {delegator_email} not found in master component"
                )
                return False

            # Add delegate as new attendee
            delegate_att = exception.add('attendee')
            delegate_att.value = f"mailto:{delegate_email}"
            delegate_att.params['PARTSTAT'] = ['NEEDS-ACTION']
            delegate_att.params['DELEGATED-FROM'] = [f"mailto:{delegator_email}"]
            delegate_att.params['ROLE'] = ['REQ-PARTICIPANT']
            delegate_att.params['CUTYPE'] = ['INDIVIDUAL']
            delegate_att.params['RSVP'] = ['TRUE']

            # Increment SEQUENCE (delegation is a significant change)
            master_seq = 0
            if hasattr(master_component, 'sequence'):
                try:
                    master_seq = int(master_component.sequence.value)
                except (ValueError, TypeError):
                    master_seq = 0
            exception.add('sequence').value = str(master_seq + 1)

            logger.info(
                f"Created delegation exception for {recurrence_id}, "
                f"delegator={delegator_email}, delegate={delegate_email}"
            )
            return True

        except Exception as e:
            logger.error(f"Error creating delegation exception: {e}", exc_info=True)
            return False

    def _build_schedule_response_success(self, base_prefix: str, attendee_email: str):
        """Build successful RFC 6638 schedule-response."""
        from moreradicale import xmlutils
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

    def _build_schedule_response_external(
            self, base_prefix: str, recipient_email: str,
            schedule_status: ScheduleStatus
    ):
        """Build RFC 6638 schedule-response for external delivery.

        Used when delivering iTIP messages to external recipients via email.
        Returns appropriate SCHEDULE-STATUS codes per RFC 6638:
        - 1.1 (PENDING): Email not configured, delivery status unknown
        - 1.2 (DELIVERED): Email accepted by SMTP server
        - 5.1 (DELIVERY_FAILED): SMTP delivery failed

        Args:
            base_prefix: URL prefix
            recipient_email: External recipient's email address
            schedule_status: Delivery status from email sending

        Returns:
            HTTP response tuple (status, headers, body, None)
        """
        from moreradicale import xmlutils
        from http import client
        import xml.etree.ElementTree as ET

        # Map ScheduleStatus to RFC 6638 status codes
        status_map = {
            ScheduleStatus.PENDING: ("1.1", "Pending - delivery status unknown"),
            ScheduleStatus.DELIVERED: ("1.2", "Delivered to mail server"),
            ScheduleStatus.DELIVERY_FAILED: ("5.1", "Could not be delivered"),
            ScheduleStatus.NO_SCHEDULING: ("3.8", "No scheduling support"),
            ScheduleStatus.INVALID_USER: ("3.7", "Invalid calendar user"),
        }

        code, description = status_map.get(
            schedule_status, ("5.3", "Unknown error")
        )

        # Build schedule-response XML
        response = ET.Element(xmlutils.make_clark("C:schedule-response"))
        response_elem = ET.SubElement(response, xmlutils.make_clark("C:response"))

        # Recipient
        recipient = ET.SubElement(response_elem, xmlutils.make_clark("C:recipient"))
        href = ET.SubElement(recipient, xmlutils.make_clark("D:href"))
        href.text = f"mailto:{recipient_email}"

        # Request status
        request_status = ET.SubElement(
            response_elem, xmlutils.make_clark("C:request-status")
        )
        request_status.text = f"{code};{description}"

        # Response description
        response_desc = ET.SubElement(
            response_elem, xmlutils.make_clark("C:response-description")
        )
        if schedule_status == ScheduleStatus.DELIVERED:
            response_desc.text = "REPLY sent to external organizer via email"
        elif schedule_status == ScheduleStatus.PENDING:
            response_desc.text = "Email not configured - delivery pending"
        else:
            response_desc.text = f"Delivery failed: {description}"

        headers = (
            ("Content-Type", "application/xml; charset=utf-8"),
        )

        return client.OK, headers, ET.tostring(response, encoding="utf-8"), None

    def _build_schedule_response_error(self, base_prefix: str, error_msg: str,
                                       schedule_status: str = "5.3"):
        """Build error RFC 6638 schedule-response.

        Args:
            base_prefix: URL prefix
            error_msg: Error description
            schedule_status: RFC 6638 status code (default: 5.3 No authority)
                Common codes:
                - 3.7: Invalid calendar user
                - 5.1: Could not be delivered
                - 5.3: No authority / Invalid date-time
        """
        from moreradicale import xmlutils
        from http import client
        import xml.etree.ElementTree as ET

        response = ET.Element(xmlutils.make_clark("C:schedule-response"))
        response_elem = ET.SubElement(response, xmlutils.make_clark("C:response"))

        # Request status with provided code
        status_text = f"{schedule_status};Error"
        request_status = ET.SubElement(response_elem,
                                       xmlutils.make_clark("C:request-status"))
        request_status.text = status_text

        # Error description
        response_desc = ET.SubElement(response_elem,
                                      xmlutils.make_clark("C:response-description"))
        response_desc.text = error_msg

        headers = (
            ("Content-Type", "application/xml; charset=utf-8"),
        )

        return client.OK, headers, ET.tostring(response, encoding="utf-8"), None

    # =========================================================================
    # External iTIP Processing (Webhook)
    # =========================================================================

    def process_reply_external(self, itip_text: str, sender_email: str,
                               base_prefix: str = "") -> bool:
        """
        Process REPLY from external attendee via webhook.

        This handles iTIP REPLY messages received through email webhooks.
        The sender is validated against the ATTENDEE in the iTIP message,
        and the organizer must be an internal user.

        Security checks:
        1. Sender email must match ATTENDEE in iTIP
        2. Organizer must be an internal user
        3. Event must exist in organizer's calendar

        Args:
            itip_text: iCalendar text with METHOD:REPLY
            sender_email: Email address of webhook sender
            base_prefix: URL base prefix

        Returns:
            True if REPLY was processed successfully
        """
        try:
            # Parse the iTIP message
            vcal = vobject.readOne(itip_text)

            # Verify METHOD is REPLY
            method = vcal.method.value if hasattr(vcal, 'method') else None
            if method != 'REPLY':
                logger.warning(f"Expected REPLY method, got {method}")
                return False

            # Get component
            component = self._get_component(vcal)
            if not component:
                logger.warning("No schedulable component in REPLY")
                return False

            # Extract UID
            if not hasattr(component, 'uid'):
                logger.warning("REPLY missing UID")
                return False
            uid = component.uid.value

            # Extract organizer
            if not hasattr(component, 'organizer'):
                logger.warning("REPLY missing ORGANIZER")
                return False
            organizer_email = extract_email(component.organizer.value)

            # Extract attendee
            attendees = component.attendee_list if hasattr(component, 'attendee_list') else []
            if not attendees and hasattr(component, 'attendee'):
                attendees = [component.attendee]

            if not attendees:
                logger.warning("REPLY missing ATTENDEE")
                return False

            # Get the first attendee (should be the replying attendee)
            attendee = attendees[0]
            attendee_email = extract_email(attendee.value)

            # SECURITY: Verify sender matches ATTENDEE
            if sender_email.lower() != attendee_email.lower():
                logger.warning(
                    f"External REPLY sender mismatch: sender={sender_email}, "
                    f"attendee={attendee_email}"
                )
                return False

            # SECURITY: Verify organizer is internal
            is_internal, organizer_principal = route_attendee(organizer_email, self.storage)
            if not is_internal:
                logger.warning(
                    f"External REPLY for external organizer: {organizer_email}"
                )
                return False

            # Find the organizer's event
            found, event_path, collection = self._find_organizer_event(
                organizer_principal, uid
            )
            if not found:
                logger.warning(f"Event {uid} not found for organizer {organizer_email}")
                return False

            # Extract PARTSTAT
            new_partstat = 'NEEDS-ACTION'
            if hasattr(attendee, 'params') and 'PARTSTAT' in attendee.params:
                new_partstat = attendee.params['PARTSTAT'][0]

            # Extract RECURRENCE-ID for recurring event support
            recurrence_id = None
            if hasattr(component, 'recurrence_id'):
                recurrence_id = component.recurrence_id.value

            # Update the attendee's PARTSTAT in organizer's event
            with self.storage.acquire_lock("w", organizer_principal.strip("/")):
                success = self._update_attendee_partstat(
                    event_path, collection, attendee_email, new_partstat,
                    recurrence_id=recurrence_id
                )

            if success:
                log_msg = (
                    f"External REPLY processed: {attendee_email} -> {new_partstat} "
                    f"for event {uid}"
                )
                if recurrence_id:
                    log_msg += f" (RECURRENCE-ID: {recurrence_id})"
                logger.info(log_msg)
            else:
                logger.warning(f"Failed to update PARTSTAT for {attendee_email}")

            return success

        except Exception as e:
            logger.error(f"Error processing external REPLY: {e}", exc_info=True)
            return False

    def process_counter_external(self, itip_text: str, sender_email: str,
                                 base_prefix: str = "") -> bool:
        """
        Process COUNTER from external attendee via webhook.

        This handles iTIP COUNTER messages received through email webhooks.
        The sender is validated against the ATTENDEE, and the counter-proposal
        is delivered to the organizer's schedule-inbox.

        Security checks:
        1. Sender email must match ATTENDEE in iTIP
        2. Organizer must be an internal user

        Args:
            itip_text: iCalendar text with METHOD:COUNTER
            sender_email: Email address of webhook sender
            base_prefix: URL base prefix

        Returns:
            True if COUNTER was processed successfully
        """
        try:
            # Parse the iTIP message
            vcal = vobject.readOne(itip_text)

            # Verify METHOD is COUNTER
            method = vcal.method.value if hasattr(vcal, 'method') else None
            if method != 'COUNTER':
                logger.warning(f"Expected COUNTER method, got {method}")
                return False

            # Get component
            component = self._get_component(vcal)
            if not component:
                logger.warning("No schedulable component in COUNTER")
                return False

            # Extract UID
            if not hasattr(component, 'uid'):
                logger.warning("COUNTER missing UID")
                return False
            uid = component.uid.value

            # Extract organizer
            if not hasattr(component, 'organizer'):
                logger.warning("COUNTER missing ORGANIZER")
                return False
            organizer_email = extract_email(component.organizer.value)

            # Extract attendee
            attendees = component.attendee_list if hasattr(component, 'attendee_list') else []
            if not attendees and hasattr(component, 'attendee'):
                attendees = [component.attendee]

            if not attendees:
                logger.warning("COUNTER missing ATTENDEE")
                return False

            attendee = attendees[0]
            attendee_email = extract_email(attendee.value)

            # SECURITY: Verify sender matches ATTENDEE
            if sender_email.lower() != attendee_email.lower():
                logger.warning(
                    f"External COUNTER sender mismatch: sender={sender_email}, "
                    f"attendee={attendee_email}"
                )
                return False

            # SECURITY: Verify organizer is internal
            is_internal, organizer_principal = route_attendee(organizer_email, self.storage)
            if not is_internal:
                logger.warning(
                    f"External COUNTER for external organizer: {organizer_email}"
                )
                return False

            # Deliver to organizer's schedule-inbox
            inbox_path = get_inbox_path(organizer_principal)

            with self.storage.acquire_lock("w", organizer_principal.strip("/")):
                # Create inbox if needed
                inbox = next(iter(self.storage.discover(inbox_path)), None)
                if not inbox:
                    logger.warning(f"Organizer inbox not found: {inbox_path}")
                    return False

                # Build iTIP filename
                timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                filename = f"{uid}-{timestamp}-counter.ics"

                # Create item
                item = radicale_item.Item(
                    collection_path=inbox_path,
                    vobject_item=vcal
                )
                item.prepare()

                # Upload to inbox
                inbox.upload(filename, item)
                logger.info(
                    f"External COUNTER delivered to {inbox_path}{filename} "
                    f"from {attendee_email}"
                )

            return True

        except Exception as e:
            logger.error(f"Error processing external COUNTER: {e}", exc_info=True)
            return False

    def _get_component(self, vcal):
        """Get the main component from a vCalendar object."""
        for comp_type in ('vevent', 'vtodo', 'vjournal'):
            if hasattr(vcal, comp_type):
                return getattr(vcal, comp_type)
        return None

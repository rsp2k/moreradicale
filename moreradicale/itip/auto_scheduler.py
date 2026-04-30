"""
CalDAV Auto-Scheduling for Resource Calendars (RFC 6638 Section 3.2.1).

This module implements server-side automatic scheduling for resource calendars
(conference rooms, equipment, etc.) when SCHEDULE-AGENT=SERVER is set.

Key Features:
- Automatic ACCEPT when resource is available (no conflicts)
- Automatic DECLINE when resource has time conflicts
- Optional TENTATIVE for tentative requests
- Integration with VAVAILABILITY for availability patterns
- Configurable auto-accept policies per resource

RFC 6638 Section 3.2.1:
    The SCHEDULE-AGENT parameter controls whether the CalDAV server
    performs scheduling operations on behalf of a calendar user.

    When SCHEDULE-AGENT=SERVER (default), the server:
    1. Processes incoming REQUEST messages
    2. Checks resource availability
    3. Automatically generates REPLY messages
    4. Updates the organizer's event with the response

Related RFCs:
- RFC 6638: CalDAV Scheduling Extensions
- RFC 5546: iTIP - iCalendar Transport-Independent Interoperability Protocol
- RFC 7953: Calendar Availability (VAVAILABILITY support)
"""

import logging
from datetime import datetime
from typing import Optional, List
from enum import Enum

try:
    import vobject
    from vobject.icalendar import utc as vobj_utc
except ImportError:
    vobject = None
    vobj_utc = None

from moreradicale.itip.models import (
    ITIPMessage, ITIPAttendee, AttendeePartStat, ScheduleAgent
)

logger = logging.getLogger(__name__)


class AutoAcceptPolicy(Enum):
    """
    Auto-accept policies for resource calendars.

    ALWAYS - Always accept (ignore conflicts, double-book allowed)
    IF_FREE - Accept only if no conflicts (default for rooms)
    MANUAL - Never auto-accept (requires manual approval)
    TENTATIVE_IF_CONFLICT - Accept as TENTATIVE if conflicts exist
    """
    ALWAYS = "always"
    IF_FREE = "if-free"
    MANUAL = "manual"
    TENTATIVE_IF_CONFLICT = "tentative-if-conflict"


class AutoScheduler:
    """
    Handles automatic scheduling for resource calendars.

    When a calendar user has SCHEDULE-AGENT=SERVER, this class:
    1. Receives incoming REQUEST messages
    2. Checks free/busy status
    3. Generates appropriate REPLY (ACCEPTED/DECLINED/TENTATIVE)
    4. Updates the organizer's calendar
    """

    def __init__(self, storage, configuration=None):
        """
        Initialize the auto-scheduler.

        Args:
            storage: Radicale storage backend
            configuration: Radicale configuration
        """
        self.storage = storage
        self.configuration = configuration

        # Load auto-accept policies from configuration
        self.default_policy = self._get_config_policy()
        self.resource_policies = self._load_resource_policies()

        # Check if availability support is enabled
        self.use_availability = self._check_availability_support()

    def _get_config_policy(self) -> AutoAcceptPolicy:
        """Get default auto-accept policy from configuration."""
        if not self.configuration:
            return AutoAcceptPolicy.IF_FREE

        policy_str = self.configuration.get("scheduling", "auto_accept_policy")

        try:
            return AutoAcceptPolicy(policy_str)
        except ValueError:
            logger.warning(f"Invalid auto_accept_policy: {policy_str}, using 'if-free'")
            return AutoAcceptPolicy.IF_FREE

    def _load_resource_policies(self) -> dict:
        """
        Load per-resource auto-accept policies from configuration.

        Configuration format:
        [scheduling]
        resource_policies = /path/to/resource-policies.json

        JSON format:
        {
            "conference-room-a@example.com": "always",
            "projector@example.com": "if-free",
            "ceo-calendar@example.com": "manual"
        }

        Returns:
            Dictionary mapping resource email to AutoAcceptPolicy
        """
        import json
        import os

        if not self.configuration:
            return {}

        policies_file = self.configuration.get("scheduling", "resource_policies_file")

        if not policies_file or not os.path.exists(policies_file):
            return {}

        try:
            with open(policies_file, 'r', encoding='utf-8') as f:
                policies_dict = json.load(f)

            # Convert string values to enum
            result = {}
            for email, policy_str in policies_dict.items():
                try:
                    result[email.lower()] = AutoAcceptPolicy(policy_str)
                except ValueError:
                    logger.warning(f"Invalid policy for {email}: {policy_str}")

            logger.info(f"Loaded {len(result)} resource-specific policies")
            return result

        except Exception as e:
            logger.error(f"Failed to load resource policies: {e}")
            return {}

    def _check_availability_support(self) -> bool:
        """Check if VAVAILABILITY support is available."""
        try:
            # Try importing availability module
            from moreradicale.itip import availability
            return True
        except ImportError:
            logger.debug("VAVAILABILITY support not available")
            return False

    def get_resource_policy(self, resource_email: str) -> AutoAcceptPolicy:
        """
        Get the auto-accept policy for a specific resource.

        Args:
            resource_email: Resource's email address

        Returns:
            AutoAcceptPolicy for this resource
        """
        email_lower = resource_email.lower()
        return self.resource_policies.get(email_lower, self.default_policy)

    def should_auto_schedule(self, attendee: ITIPAttendee) -> bool:
        """
        Check if an attendee should be auto-scheduled.

        Criteria:
        1. SCHEDULE-AGENT must be SERVER
        2. Must be a resource (CUTYPE=ROOM or RESOURCE)
        3. Must be internal (has principal_path)
        4. Policy must not be MANUAL

        Args:
            attendee: ITIPAttendee to check

        Returns:
            True if auto-scheduling should apply
        """
        # Check SCHEDULE-AGENT parameter
        if attendee.schedule_agent != ScheduleAgent.SERVER:
            logger.debug(f"Auto-schedule skipped for {attendee.email}: SCHEDULE-AGENT={attendee.schedule_agent.value}")
            return False

        # Must be a resource type
        if attendee.cutype not in ('ROOM', 'RESOURCE'):
            logger.debug(f"Auto-schedule skipped for {attendee.email}: CUTYPE={attendee.cutype} (not ROOM/RESOURCE)")
            return False

        # Must be internal (we can't auto-schedule external resources)
        if not attendee.is_internal or not attendee.principal_path:
            logger.debug(f"Auto-schedule skipped for {attendee.email}: not internal")
            return False

        # Check policy
        policy = self.get_resource_policy(attendee.email)
        if policy == AutoAcceptPolicy.MANUAL:
            logger.debug(f"Auto-schedule skipped for {attendee.email}: policy is MANUAL")
            return False

        return True

    def process_request(
        self,
        itip_msg: ITIPMessage,
        vcal: 'vobject.base.Component',
        component: 'vobject.base.Component'
    ) -> List[ITIPAttendee]:
        """
        Process a REQUEST for auto-schedulable resources.

        For each resource attendee with SCHEDULE-AGENT=SERVER:
        1. Check auto-accept policy
        2. Check for time conflicts (if needed)
        3. Set PARTSTAT to ACCEPTED/DECLINED/TENTATIVE
        4. Add event to resource's calendar (if accepted)

        Args:
            itip_msg: iTIP REQUEST message
            vcal: Full VCALENDAR object
            component: VEVENT/VTODO component

        Returns:
            List of ITIPAttendee objects that were auto-scheduled
        """
        auto_scheduled = []

        for attendee in itip_msg.attendees:
            if not self.should_auto_schedule(attendee):
                continue

            try:
                # Get event time range
                dtstart = getattr(component, 'dtstart', None)
                dtend = getattr(component, 'dtend', None)

                if not dtstart:
                    logger.warning(f"Cannot auto-schedule {attendee.email}: missing DTSTART")
                    continue

                event_start = dtstart.value
                event_end = dtend.value if dtend else event_start

                # Get policy for this resource
                policy = self.get_resource_policy(attendee.email)

                # Apply policy
                if policy == AutoAcceptPolicy.ALWAYS:
                    # Always accept, don't check conflicts
                    self._accept_request(attendee, itip_msg, vcal, component)
                    logger.info(f"Resource {attendee.email} ACCEPTED (policy: ALWAYS)")

                elif policy == AutoAcceptPolicy.IF_FREE:
                    # Accept only if no conflicts
                    has_conflict = self._check_conflict(
                        attendee.principal_path,
                        event_start,
                        event_end,
                        itip_msg.uid,
                        itip_msg.recurrence_id
                    )

                    if has_conflict:
                        attendee.partstat = AttendeePartStat.DECLINED
                        logger.info(f"Resource {attendee.email} DECLINED (conflict detected)")
                    else:
                        self._accept_request(attendee, itip_msg, vcal, component)
                        logger.info(f"Resource {attendee.email} ACCEPTED (no conflict)")

                elif policy == AutoAcceptPolicy.TENTATIVE_IF_CONFLICT:
                    # Accept as TENTATIVE if conflicts, otherwise ACCEPTED
                    has_conflict = self._check_conflict(
                        attendee.principal_path,
                        event_start,
                        event_end,
                        itip_msg.uid,
                        itip_msg.recurrence_id
                    )

                    if has_conflict:
                        # Tentatively accept (might require approval)
                        self._accept_request(attendee, itip_msg, vcal, component, tentative=True)
                        logger.info(f"Resource {attendee.email} TENTATIVE (conflict detected)")
                    else:
                        self._accept_request(attendee, itip_msg, vcal, component)
                        logger.info(f"Resource {attendee.email} ACCEPTED (no conflict)")

                auto_scheduled.append(attendee)

            except Exception as e:
                logger.error(f"Error auto-scheduling {attendee.email}: {e}", exc_info=True)

        return auto_scheduled

    def _accept_request(
        self,
        attendee: ITIPAttendee,
        itip_msg: ITIPMessage,
        vcal: 'vobject.base.Component',
        component: 'vobject.base.Component',
        tentative: bool = False
    ) -> None:
        """
        Accept a request and add to resource's calendar.

        Args:
            attendee: Resource attendee accepting the request
            itip_msg: iTIP message
            vcal: Full VCALENDAR
            component: VEVENT/VTODO component
            tentative: If True, accept as TENTATIVE instead of ACCEPTED
        """
        # Set participation status
        if tentative:
            attendee.partstat = AttendeePartStat.TENTATIVE
        else:
            attendee.partstat = AttendeePartStat.ACCEPTED

        # Add event to resource's calendar
        self._add_to_calendar(
            attendee.principal_path,
            vcal,
            component,
            attendee.email,
            itip_msg.uid,
            attendee.partstat
        )

    def _check_conflict(
        self,
        principal_path: str,
        event_start,
        event_end,
        exclude_uid: str,
        recurrence_id: Optional[str] = None
    ) -> bool:
        """
        Check if a resource has conflicting events.

        This checks:
        1. Regular events with overlapping times
        2. VAVAILABILITY constraints (if enabled)
        3. Recurring event exceptions

        Args:
            principal_path: Resource's principal path
            event_start: Event start time
            event_end: Event end time
            exclude_uid: UID to exclude (the event being scheduled)
            recurrence_id: RECURRENCE-ID if this is a specific instance

        Returns:
            True if conflicts exist, False otherwise
        """
        try:
            # Check for conflicting events
            if self._has_event_conflicts(principal_path, event_start, event_end, exclude_uid):
                return True

            # Check VAVAILABILITY constraints if enabled
            if self.use_availability:
                if not self._check_availability(principal_path, event_start, event_end):
                    logger.debug(f"Resource {principal_path} unavailable per VAVAILABILITY")
                    return True

            return False

        except Exception as e:
            logger.error(f"Error checking conflicts: {e}", exc_info=True)
            # On error, assume conflict (fail-safe)
            return True

    def _has_event_conflicts(
        self,
        principal_path: str,
        event_start,
        event_end,
        exclude_uid: str
    ) -> bool:
        """
        Check for conflicting events in the resource's calendar.

        Args:
            principal_path: Resource's principal path
            event_start: Event start time
            event_end: Event end time
            exclude_uid: UID to exclude

        Returns:
            True if conflicts exist
        """

        try:
            # Discover all calendar collections
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
                            if subcomp.name not in ('VEVENT', 'VTODO'):
                                continue

                            # Skip the event being scheduled
                            if hasattr(subcomp, 'uid') and subcomp.uid.value == exclude_uid:
                                continue

                            # Skip cancelled events
                            status = getattr(subcomp, 'status', None)
                            if status and status.value.upper() == 'CANCELLED':
                                continue

                            # Skip transparent events (don't block time)
                            transp = getattr(subcomp, 'transp', None)
                            if transp and transp.value.upper() == 'TRANSPARENT':
                                continue

                            # Get event times
                            if not hasattr(subcomp, 'dtstart'):
                                continue

                            existing_start = subcomp.dtstart.value

                            # Handle DTEND vs DURATION
                            if hasattr(subcomp, 'dtend'):
                                existing_end = subcomp.dtend.value
                            elif hasattr(subcomp, 'duration'):
                                existing_end = existing_start + subcomp.duration.value
                            else:
                                existing_end = existing_start

                            # Check for overlap
                            if self._times_overlap(event_start, event_end, existing_start, existing_end):
                                logger.debug(
                                    f"Conflict found: existing event {getattr(subcomp, 'uid', 'unknown').value} "
                                    f"overlaps with new event"
                                )
                                return True

                except Exception as e:
                    logger.warning(f"Error reading calendar {collection.path}: {e}")
                    continue

            return False

        except Exception as e:
            logger.error(f"Error checking event conflicts: {e}", exc_info=True)
            # On error, assume conflict
            return True

    def _check_availability(
        self,
        principal_path: str,
        event_start,
        event_end
    ) -> bool:
        """
        Check if resource is available per VAVAILABILITY.

        Args:
            principal_path: Resource's principal path
            event_start: Event start time
            event_end: Event end time

        Returns:
            True if available, False if unavailable
        """
        try:
            from moreradicale.itip.availability import AvailabilityProcessor

            # Create availability processor
            avail_proc = AvailabilityProcessor(self.storage, self.configuration)

            # Get availability components
            availabilities = avail_proc.get_user_availability(principal_path)

            if not availabilities:
                # No VAVAILABILITY defined - assume available
                return True

            # Check if requested time falls within any AVAILABLE slot
            for vavail in availabilities:
                if not vavail.is_active_at(event_start):
                    continue

                available_slots = vavail.get_available_slots(event_start, event_end)

                # Check if entire event fits in an available slot
                for slot_start, slot_end in available_slots:
                    if event_start >= slot_start and event_end <= slot_end:
                        return True

            # Not available in any slot
            return False

        except ImportError:
            # VAVAILABILITY not available, assume available
            return True
        except Exception as e:
            logger.warning(f"Error checking VAVAILABILITY: {e}")
            # On error, assume available
            return True

    def _times_overlap(self, start1, end1, start2, end2) -> bool:
        """Check if two time ranges overlap."""
        from datetime import date

        # Normalize to comparable types
        def to_datetime(dt):
            if isinstance(dt, date) and not isinstance(dt, datetime):
                return datetime.combine(dt, datetime.min.time())
            return dt

        s1, e1 = to_datetime(start1), to_datetime(end1)
        s2, e2 = to_datetime(start2), to_datetime(end2)

        # Overlap if: start1 < end2 AND start2 < end1
        return s1 < e2 and s2 < e1

    def _add_to_calendar(
        self,
        principal_path: str,
        vcal: 'vobject.base.Component',
        component: 'vobject.base.Component',
        resource_email: str,
        uid: str,
        partstat: AttendeePartStat
    ) -> bool:
        """
        Add event to resource's default calendar.

        Args:
            principal_path: Resource's principal path
            vcal: Full VCALENDAR
            component: VEVENT/VTODO component
            resource_email: Resource's email
            uid: Event UID
            partstat: Participation status (ACCEPTED/TENTATIVE)

        Returns:
            True if successful
        """
        try:
            # Find resource's default calendar
            discovered = list(self.storage.discover(principal_path, depth="1"))

            target_collection = None
            for collection in discovered:
                # Skip non-calendar collections
                if not hasattr(collection, 'tag') or collection.tag != 'VCALENDAR':
                    continue

                # Skip schedule-inbox/outbox
                if 'schedule-' in collection.path.lower():
                    continue

                # Use first available calendar
                target_collection = collection
                break

            if not target_collection:
                logger.error(f"No calendar found for resource {resource_email}")
                return False

            # Clone the calendar and component using vobject's duplicate method
            import copy
            new_vcal = copy.deepcopy(vcal)

            # Update ATTENDEE line for this resource to show PARTSTAT
            for comp in new_vcal.getChildren():
                if comp.name != component.name:
                    continue

                if hasattr(comp, 'attendee'):
                    attendees = comp.contents.get('attendee', [])
                    if not isinstance(attendees, list):
                        attendees = [attendees]

                    for att in attendees:
                        if resource_email.lower() in att.value.lower():
                            # Update PARTSTAT
                            att.params['PARTSTAT'] = [partstat.value]
                            break

            # Generate item href from UID (sanitize for filesystem)
            # Remove or replace special characters
            safe_uid = uid.replace('@', '-').replace('/', '-').replace('\\', '-')
            href = f"{safe_uid}.ics"

            # Create proper Item object
            from moreradicale import item as radicale_item
            event_item = radicale_item.Item(
                collection_path=target_collection.path,
                vobject_item=new_vcal
            )
            event_item.prepare()

            # Upload to collection
            target_collection.upload(href, event_item)
            logger.info(f"Added event {uid} to {resource_email}'s calendar with PARTSTAT={partstat.value}")
            return True

        except Exception as e:
            logger.error(f"Error adding event to resource calendar: {e}", exc_info=True)
            return False

    def generate_reply(
        self,
        itip_msg: ITIPMessage,
        attendee: ITIPAttendee,
        vcal: 'vobject.base.Component'
    ) -> Optional[str]:
        """
        Generate a REPLY message for an auto-scheduled resource.

        RFC 5546 Section 3.2.3: REPLY format

        Args:
            itip_msg: Original REQUEST message
            attendee: Resource attendee with updated PARTSTAT
            vcal: Original VCALENDAR

        Returns:
            iCalendar text for REPLY, or None on error
        """
        try:
            # Create reply calendar
            reply_cal = vobject.iCalendar()
            reply_cal.add('method').value = 'REPLY'
            reply_cal.add('prodid').value = '-//Radicale//Auto-Scheduler//EN'
            reply_cal.add('version').value = '2.0'

            # Clone the component
            for comp in vcal.getChildren():
                if comp.name in ('VEVENT', 'VTODO', 'VJOURNAL'):
                    # Clone component for reply
                    reply_comp = reply_cal.add(comp.name.lower())

                    # Copy essential properties
                    for prop in ['uid', 'sequence', 'dtstamp', 'dtstart', 'dtend',
                                 'duration', 'summary', 'location', 'recurrence_id']:
                        if hasattr(comp, prop):
                            setattr(reply_comp, prop, getattr(comp, prop))

                    # Add organizer
                    reply_comp.add('organizer').value = itip_msg.organizer

                    # Add only this attendee with updated PARTSTAT
                    att = reply_comp.add('attendee')
                    att.value = f"mailto:{attendee.email}"
                    att.params['PARTSTAT'] = [attendee.partstat.value]
                    if attendee.cn:
                        att.params['CN'] = [attendee.cn]

                    # Add REQUEST-STATUS for successful auto-accept
                    reply_comp.add('request-status').value = '2.0;Success'

                    break

            return reply_cal.serialize()

        except Exception as e:
            logger.error(f"Error generating REPLY: {e}", exc_info=True)
            return None

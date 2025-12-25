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
iTIP message processor for RFC 6638 CalDAV Scheduling.

This module handles the core scheduling operations:
- Processing iTIP messages from schedule-outbox
- Routing to internal/external attendees
- Delivering messages to schedule-inbox
- Generating schedule-response
"""

import posixpath
import xml.etree.ElementTree as ET
from http import client
from typing import List

import vobject

from radicale import httputils, item as radicale_item, pathutils, storage, xmlutils
from radicale.itip import router, validator
from radicale.itip.models import ITIPMessage, ScheduleResponse
from radicale.log import logger


class ITIPProcessor:
    """Process iTIP scheduling messages."""

    def __init__(self, configuration, storage_instance):
        """Initialize processor.

        Args:
            configuration: Radicale configuration
            storage_instance: Radicale storage instance
        """
        self.configuration = configuration
        self.storage = storage_instance

    def process_outbox_post(self, user: str, ical_text: str,
                           base_prefix: str) -> httputils.types.WSGIResponse:
        """Process iTIP message POSTed to schedule-outbox.

        Args:
            user: Authenticated username
            ical_text: iCalendar text with METHOD property
            base_prefix: Base URL prefix

        Returns:
            WSGI response with schedule-response
        """
        # Parse and validate iTIP message
        try:
            items = list(radicale_item.read_components(ical_text))
            if not items:
                logger.warning("No components in iTIP message")
                return httputils.BAD_REQUEST

            vcal = items[0]
            itip_msg = validator.parse_itip_message(vcal)

        except validator.ITIPValidationError as e:
            logger.warning("iTIP validation failed: %s", e)
            return httputils.BAD_REQUEST
        except Exception as e:
            logger.error("Failed to parse iTIP message: %s", e, exc_info=True)
            return httputils.BAD_REQUEST

        # Security check: verify user is authorized as organizer
        if not router.validate_organizer_permission(
                itip_msg.organizer, user, self.configuration):
            logger.warning("User %s not authorized as organizer %s",
                         user, itip_msg.organizer)
            return httputils.FORBIDDEN

        # Check max attendees limit
        max_attendees = self.configuration.get("scheduling", "max_attendees")
        if len(itip_msg.attendees) > max_attendees:
            logger.warning("Too many attendees: %d > %d",
                         len(itip_msg.attendees), max_attendees)
            return httputils.FORBIDDEN

        # Route attendees
        self._route_attendees(itip_msg)

        # Deliver to internal attendees
        responses = []
        for attendee in itip_msg.internal_attendees:
            success = self._deliver_to_inbox(
                attendee.principal_path, itip_msg)
            if success:
                responses.append(ScheduleResponse(
                    recipient=attendee.email,
                    request_status="2.0;Success"
                ))
            else:
                responses.append(ScheduleResponse(
                    recipient=attendee.email,
                    request_status="5.1;Service unavailable"
                ))

        # External attendees - not yet supported (Phase 4)
        for attendee in itip_msg.external_attendees:
            responses.append(ScheduleResponse(
                recipient=attendee.email,
                request_status="2.8;NoAuthorization (external delivery not yet implemented)"
            ))

        # Generate schedule-response
        return self._build_schedule_response(responses)

    def _route_attendees(self, itip_msg: ITIPMessage) -> None:
        """Route attendees to internal or external.

        Updates itip_msg.attendees in-place with routing information.

        Args:
            itip_msg: iTIP message with attendees
        """
        for attendee in itip_msg.attendees:
            is_internal, principal_path = router.route_attendee(
                attendee.email, self.storage, self.configuration)

            attendee.is_internal = is_internal
            attendee.principal_path = principal_path

        logger.info("Routed %d internal, %d external attendees",
                   len(itip_msg.internal_attendees),
                   len(itip_msg.external_attendees))

    def _deliver_to_inbox(self, principal_path: str,
                         itip_msg: ITIPMessage) -> bool:
        """Deliver iTIP message to attendee's schedule-inbox.

        Args:
            principal_path: Principal path like "/alice/"
            itip_msg: iTIP message to deliver

        Returns:
            True if delivered successfully
        """
        if not principal_path:
            return False

        inbox_path = posixpath.join(principal_path, "schedule-inbox")

        # Generate filename: UID-SEQUENCE.ics
        filename = f"{itip_msg.uid}-{itip_msg.sequence}.ics"

        try:
            with self.storage.acquire_lock("w"):
                # Discover inbox collection
                discovered = list(self.storage.discover(
                    inbox_path, depth="0"))
                if not discovered:
                    logger.warning("Schedule-inbox not found: %s", inbox_path)
                    return False

                inbox = discovered[0]
                if not isinstance(inbox, storage.BaseCollection):
                    logger.warning("Not a collection: %s", inbox_path)
                    return False

                if inbox.tag != "SCHEDULING-INBOX":
                    logger.warning("Not a schedule-inbox: %s (tag=%s)",
                                 inbox_path, inbox.tag)
                    return False

                # Create item from iTIP message
                items = list(radicale_item.read_components(
                    itip_msg.vobject_data))
                if not items:
                    logger.error("No components in iTIP message")
                    return False

                # Upload to inbox
                new_item = radicale_item.Item(collection=inbox, vobject_item=items[0])
                inbox.upload(filename, new_item)

                logger.info("Delivered iTIP message to %s/%s",
                           inbox_path, filename)
                return True

        except Exception as e:
            logger.error("Failed to deliver to inbox %s: %s",
                        inbox_path, e, exc_info=True)
            return False

    def _build_schedule_response(
            self, responses: List[ScheduleResponse]
            ) -> httputils.types.WSGIResponse:
        """Build RFC 6638 schedule-response XML.

        Args:
            responses: List of per-recipient responses

        Returns:
            WSGI response with schedule-response
        """
        # Build XML per RFC 6638 Section 3.2.9
        root = ET.Element(xmlutils.make_clark("C:schedule-response"))

        for resp in responses:
            response_elem = ET.Element(xmlutils.make_clark("C:response"))

            # Recipient
            recipient = ET.Element(xmlutils.make_clark("C:recipient"))
            href = ET.Element(xmlutils.make_clark("D:href"))
            href.text = f"mailto:{resp.recipient}"
            recipient.append(href)
            response_elem.append(recipient)

            # Request status
            req_status = ET.Element(xmlutils.make_clark("C:request-status"))
            req_status.text = resp.request_status
            response_elem.append(req_status)

            # Calendar data (optional)
            if resp.calendar_data:
                cal_data = ET.Element(xmlutils.make_clark("C:calendar-data"))
                cal_data.text = resp.calendar_data
                response_elem.append(cal_data)

            root.append(response_elem)

        # Serialize XML
        xml_content = ET.tostring(root, encoding="utf-8")

        # Build HTTP response
        headers = {
            "Content-Type": "application/xml; charset=utf-8",
            "Content-Length": str(len(xml_content))
        }

        return client.OK, headers, xml_content, None

# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2008 Nicolas Kandel
# Copyright © 2008 Pascal Halter
# Copyright © 2008-2017 Guillaume Ayoub
# Copyright © 2017-2021 Unrud <unrud@outlook.com>
# Copyright © 2020-2020 Tom Hacohen <tom@stosb.com>
# Copyright © 2025-2025 Peter Bieringer <pb@bieringer.de>
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

import socket
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs

from moreradicale import httputils, storage, types
from moreradicale.app.base import ApplicationBase
from moreradicale.log import logger


class ApplicationPartPost(ApplicationBase):

    def do_POST(self, environ: types.WSGIEnviron, base_prefix: str,
                path: str, user: str, remote_host: str, remote_useragent: str) -> types.WSGIResponse:
        """Manage POST request."""
        # Web interface
        if path == "/.web" or path.startswith("/.web/"):
            return self._web.post(environ, base_prefix, path, user)

        # RFC 8607: Check for managed attachment operations via query string
        query_string = environ.get("QUERY_STRING", "")
        if query_string:
            from moreradicale.attachments.handler import is_attachment_request
            action = is_attachment_request(query_string)
            if action:
                return self._handle_attachment_post(
                    environ, base_prefix, path, user, action, query_string
                )

        # Discover the target collection
        with self._storage.acquire_lock("r", user):
            item = next(iter(self._storage.discover(path)), None)
            if not item or not isinstance(item, storage.BaseCollection):
                return httputils.NOT_FOUND

        # Check content type to determine request type
        content_type = environ.get("CONTENT_TYPE", "")

        # XML requests may be sharing operations
        if "xml" in content_type.lower():
            return self._handle_xml_post(environ, base_prefix, path, user, item)

        # RFC 6638 Scheduling: POST to schedule-outbox
        # Check if scheduling is enabled
        if not self.configuration.get("scheduling", "enabled"):
            return httputils.METHOD_NOT_ALLOWED

        # POST only valid on schedule-outbox for iTIP
        if item.tag != "SCHEDULING-OUTBOX":
            logger.debug("POST attempted on non-outbox collection: %s (tag=%s)",
                         path, item.tag)
            return httputils.METHOD_NOT_ALLOWED

        # Verify this is the user's own outbox (security check)
        if not item.owner or item.owner != user:
            logger.warning("User %s attempted to POST to %s's outbox",
                           user, item.owner)
            return httputils.FORBIDDEN

        # Handle the iTIP POST
        return self._handle_scheduling_post(environ, base_prefix, path, user)

    def _handle_xml_post(self, environ: types.WSGIEnviron, base_prefix: str,
                         path: str, user: str,
                         collection: storage.BaseCollection) -> types.WSGIResponse:
        """Handle XML POST requests (sharing operations)."""
        from moreradicale.sharing.handler import SharingHandler, is_sharing_request

        # Read and parse XML body
        try:
            text = httputils.read_request_body(self.configuration, environ)
        except RuntimeError as e:
            logger.warning("Failed to read XML request body: %s", e)
            return httputils.BAD_REQUEST
        except socket.timeout:
            logger.warning("XML request body read timeout")
            return httputils.REQUEST_TIMEOUT

        if not text:
            logger.warning("Empty XML POST request")
            return httputils.BAD_REQUEST

        try:
            xml_content = ET.fromstring(text)
        except ET.ParseError as e:
            logger.warning("Failed to parse XML POST request: %s", e)
            return httputils.BAD_REQUEST

        # Check if this is a sharing request
        if is_sharing_request(xml_content):
            # Verify sharing is enabled
            if not self.configuration.get("sharing", "enabled"):
                logger.debug("Sharing request received but sharing is disabled")
                return httputils.METHOD_NOT_ALLOWED

            # Handle sharing request
            handler = SharingHandler(self._storage, self.configuration)
            with self._storage.acquire_lock("w", user):
                # Re-discover collection with write lock
                item = next(iter(self._storage.discover(path)), None)
                if not item or not isinstance(item, storage.BaseCollection):
                    return httputils.NOT_FOUND

                return handler.handle_sharing_post(user, xml_content, item, base_prefix)

        # Unknown XML POST type
        logger.debug("Unknown XML POST request type: %s", xml_content.tag)
        return httputils.METHOD_NOT_ALLOWED

    def _handle_scheduling_post(self, environ: types.WSGIEnviron,
                                base_prefix: str, path: str,
                                user: str) -> types.WSGIResponse:
        """Handle iTIP message POST to schedule-outbox.

        Args:
            environ: WSGI environment
            base_prefix: Base URL prefix
            path: Request path (schedule-outbox)
            user: Authenticated username

        Returns:
            WSGI response with schedule-response
        """
        from moreradicale.itip import processor

        # Read the iTIP message from request body
        try:
            text = httputils.read_request_body(self.configuration, environ)
        except RuntimeError as e:
            logger.warning("Failed to read iTIP request body: %s", e)
            return httputils.BAD_REQUEST
        except socket.timeout:
            logger.warning("iTIP request body read timeout")
            return httputils.REQUEST_TIMEOUT

        # Process the iTIP message
        try:
            itip_processor = processor.ITIPProcessor(self._storage, self.configuration)
            return itip_processor.process_outbox_post(user, text, base_prefix)
        except Exception as e:
            logger.error("Failed to process iTIP message: %s", e, exc_info=True)
            return httputils.INTERNAL_SERVER_ERROR

    def _handle_attachment_post(self, environ: types.WSGIEnviron,
                                base_prefix: str, path: str, user: str,
                                action: str, query_string: str) -> types.WSGIResponse:
        """Handle RFC 8607 managed attachment POST requests.

        Args:
            environ: WSGI environment
            base_prefix: Server base URL prefix
            path: Path to calendar object (e.g., /alice/calendar/event.ics)
            user: Authenticated username
            action: Attachment action (attachment-add, attachment-update, attachment-remove)
            query_string: Raw query string

        Returns:
            WSGI response tuple
        """
        from moreradicale.attachments.handler import AttachmentHandler

        # Check if attachments are enabled
        if not self.configuration.get("attachments", "enabled"):
            logger.debug("Attachment request received but attachments are disabled")
            return httputils.NOT_IMPLEMENTED

        # Parse query parameters
        query_params = parse_qs(query_string)

        # Handle the attachment operation
        handler = AttachmentHandler(self._storage, self.configuration)
        return handler.handle_attachment_post(
            environ, base_prefix, path, user, action, query_params
        )

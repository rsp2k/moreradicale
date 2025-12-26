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

from radicale import httputils, storage, types
from radicale.app.base import ApplicationBase
from radicale.log import logger


class ApplicationPartPost(ApplicationBase):

    def do_POST(self, environ: types.WSGIEnviron, base_prefix: str,
                path: str, user: str, remote_host: str, remote_useragent: str) -> types.WSGIResponse:
        """Manage POST request."""
        # Web interface
        if path == "/.web" or path.startswith("/.web/"):
            return self._web.post(environ, base_prefix, path, user)

        # RFC 6638 Scheduling: POST to schedule-outbox
        # Check if scheduling is enabled
        if not self.configuration.get("scheduling", "enabled"):
            return httputils.METHOD_NOT_ALLOWED

        # Discover the target collection
        with self._storage.acquire_lock("r", user):
            item = next(iter(self._storage.discover(path)), None)
            if not item or not isinstance(item, storage.BaseCollection):
                return httputils.NOT_FOUND

            # POST only valid on schedule-outbox
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
        from radicale.itip import processor

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
            itip_processor = processor.ITIPProcessor(self._storage)
            return itip_processor.process_outbox_post(user, text, base_prefix)
        except Exception as e:
            logger.error("Failed to process iTIP message: %s", e, exc_info=True)
            return httputils.INTERNAL_SERVER_ERROR

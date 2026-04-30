# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 RFC 3253 Activity Support
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
RFC 3253 MKACTIVITY method handler.

MKACTIVITY creates a new activity resource for grouping related changes.
Activities help organize parallel development by tracking change sets.

Per RFC 3253 §13.1:
- MKACTIVITY creates a new activity resource at the Request-URI
- The activity can then be used to group checkouts and versions
- Returns 201 Created on success
"""

import logging
import xml.etree.ElementTree as ET
from http import client

from moreradicale import httputils, types, xmlutils
from moreradicale.app.base import ApplicationBase

logger = logging.getLogger(__name__)


class ApplicationPartMkactivity(ApplicationBase):
    """Handle MKACTIVITY requests for RFC 3253 activities."""

    def do_MKACTIVITY(self, environ: types.WSGIEnviron, base_prefix: str,
                      path: str, user: str, remote_host: str,
                      remote_useragent: str) -> types.WSGIResponse:
        """
        Handle MKACTIVITY request (RFC 3253 §13.1).

        Creates a new activity for grouping related changes.

        Request body (optional):
            <D:mkactivity xmlns:D="DAV:">
              <D:displayname>Q1 2025 Updates</D:displayname>
              <D:comment>All calendar changes for Q1 2025</D:comment>
            </D:mkactivity>

        Response:
            201 Created with Location header
            405 Method Not Allowed if versioning disabled
            409 Conflict if activity already exists at this URL
        """
        # Check if versioning is enabled
        if not self.configuration.get("storage", "versioning"):
            return httputils.METHOD_NOT_ALLOWED

        # Parse request path
        # Activities are created at URLs like /.activities/{id}
        path = path.strip("/")

        # Parse request body for activity metadata
        display_name = ""
        description = ""

        try:
            xml_content = httputils.read_request_body(self.configuration, environ)
            if xml_content:
                root = ET.fromstring(xml_content)

                # Extract display name and description
                for child in root:
                    tag = xmlutils.make_human_tag(child.tag)
                    if tag == "D:displayname" and child.text:
                        display_name = child.text.strip()
                    elif tag == "D:comment" and child.text:
                        description = child.text.strip()
        except ET.ParseError as e:
            logger.warning("Invalid XML in MKACTIVITY request: %s", e)
            return httputils.BAD_REQUEST
        except Exception:
            # Empty body is OK - we'll use defaults
            pass

        # Default display name if not provided
        if not display_name:
            display_name = f"Activity by {user or 'anonymous'}"

        # Get activity manager
        activity_manager = self._get_activity_manager()
        if activity_manager is None:
            logger.warning("Activity manager not available")
            return httputils.INTERNAL_SERVER_ERROR

        # If path specifies an activity ID, check it doesn't exist
        # Path format: .activities/{activity-id}
        if path.startswith(".activities/"):
            activity_id = path[12:]  # Remove ".activities/" prefix
            if activity_manager.activity_exists(activity_id):
                return self._conflict_response("Activity already exists")
            # Client specified activity ID - use it
            # (This is non-standard but allows clients to control IDs)
        else:
            # Standard case: server generates activity ID
            activity_id = None

        # Create the activity
        if activity_id:
            # Non-standard: use client-provided ID
            # We need to bypass the UUID generation
            from moreradicale.versioning.activity_manager import ActivityInfo
            from datetime import datetime, timezone
            activity = ActivityInfo(
                activity_id=activity_id,
                creator=user or "anonymous",
                created=datetime.now(timezone.utc).isoformat(),
                display_name=display_name,
                description=description,
                checkouts=[],
                versions=[]
            )
            activity_manager._save_activity(activity)
        else:
            # Standard: server generates UUID
            activity = activity_manager.create_activity(
                creator=user or "anonymous",
                display_name=display_name,
                description=description
            )

        logger.info("MKACTIVITY '%s' by %s -> %s",
                    display_name, user, activity.activity_id[:8])

        # Build success response
        return self._mkactivity_success_response(
            base_prefix, activity.activity_id, display_name
        )

    def _get_activity_manager(self):
        """Get or create activity manager."""
        try:
            from moreradicale.versioning.activity_manager import ActivityManager
            storage_folder = self.configuration.get("storage", "filesystem_folder")
            return ActivityManager(storage_folder)
        except ImportError:
            return None

    def _conflict_response(self, message: str) -> types.WSGIResponse:
        """Return 409 Conflict response."""
        error = ET.Element(xmlutils.make_clark("D:error"))
        desc = ET.SubElement(error, xmlutils.make_clark("D:responsedescription"))
        desc.text = message

        xml_output = xmlutils.pretty_xml(error)
        return (
            client.CONFLICT,
            {"Content-Type": "text/xml; charset=utf-8"},
            xml_output,
            error
        )

    def _mkactivity_success_response(self, base_prefix: str,
                                     activity_id: str,
                                     display_name: str) -> types.WSGIResponse:
        """Build successful MKACTIVITY response."""
        # Activity URL
        activity_url = f"/.activities/{activity_id}"
        full_url = xmlutils.make_href(base_prefix, activity_url)

        # Build response body
        multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
        response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

        href = ET.SubElement(response, xmlutils.make_clark("D:href"))
        href.text = full_url

        propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
        prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

        # DAV:resourcetype with activity
        rtype = ET.SubElement(prop, xmlutils.make_clark("D:resourcetype"))
        ET.SubElement(rtype, xmlutils.make_clark("D:activity"))

        # DAV:displayname
        dname = ET.SubElement(prop, xmlutils.make_clark("D:displayname"))
        dname.text = display_name

        status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
        status.text = xmlutils.make_response(200)

        xml_output = xmlutils.pretty_xml(multistatus)

        # RFC 3253 requires 201 Created with Location header
        return (
            client.CREATED,
            {
                "Content-Type": "text/xml; charset=utf-8",
                "Location": full_url,
                "Cache-Control": "no-cache"
            },
            xml_output,
            multistatus
        )

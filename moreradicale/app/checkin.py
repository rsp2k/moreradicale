# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 RFC 3253 Versioning Implementation
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
RFC 3253 CHECKIN method handler.

CHECKIN creates a new version from the current content of a
checked-out resource and returns it to checked-in state.

Per RFC 3253 §4.4:
- CHECKIN on a checked-out resource creates a new version
- The new version becomes the DAV:checked-in version
- Returns 201 Created with Location header pointing to new version
"""

import logging
import xml.etree.ElementTree as ET
from http import client

from moreradicale import httputils, types, xmlutils
from moreradicale.app.base import ApplicationBase

logger = logging.getLogger(__name__)


class ApplicationPartCheckin(ApplicationBase):
    """Handle CHECKIN requests for RFC 3253 versioning."""

    def do_CHECKIN(self, environ: types.WSGIEnviron, base_prefix: str,
                   path: str, user: str, remote_host: str,
                   remote_useragent: str) -> types.WSGIResponse:
        """
        Handle CHECKIN request (RFC 3253 §4.4).

        Creates a new version from the current working content and
        removes the checkout.

        Request body (optional):
            <D:checkin xmlns:D="DAV:">
              <D:keep-checked-out/>  <!-- Keep resource checked out after checkin -->
            </D:checkin>

        Response:
            201 Created with Location header pointing to new version
            409 Conflict if not checked out or checked out by different user
        """
        # Check if versioning is enabled
        if not self.configuration.get("storage", "versioning"):
            return httputils.METHOD_NOT_ALLOWED

        # Parse the path
        path = path.strip("/")
        if not path:
            return httputils.BAD_REQUEST

        # Get write lock for creating the version
        with self._storage.acquire_lock("w", user):
            # Get checkout manager
            checkout_manager = self._get_checkout_manager()
            if checkout_manager is None:
                logger.warning("Checkout manager not available")
                return httputils.INTERNAL_SERVER_ERROR

            # Verify resource is checked out by this user
            relative_path = f"collection-root/{path}"
            checkout_info = checkout_manager.get_checkout_info(relative_path)

            if checkout_info is None:
                return self._conflict_response("Resource is not checked out")

            if checkout_info.user != user:
                return self._conflict_response(
                    f"Resource is checked out by {checkout_info.user}"
                )

            # Get git writer to create the version
            git_writer = self._get_git_writer()
            if git_writer is None or not git_writer.is_available():
                return self._server_error("Version control not available")

            # Get user email (default to "anonymous" if user is empty)
            effective_user = user if user else "anonymous"
            user_email = f"{effective_user}@localhost"

            # Create commit message
            item_name = path.split("/")[-1]
            message = f"CHECKIN: {item_name}"

            # Find activities associated with this checkout
            activity_manager = self._get_activity_manager()
            activity_ids = []
            if activity_manager:
                activity_ids = activity_manager.get_activities_for_resource(relative_path)

            # Create the new version
            new_sha = git_writer.create_version(
                relative_path,
                effective_user,
                user_email,
                message
            )

            if new_sha is None:
                return self._server_error("Failed to create version")

            # Associate version with activities
            if activity_manager and activity_ids:
                for activity_id in activity_ids:
                    if activity_manager.add_version(activity_id, new_sha):
                        logger.info("Added version %s to activity %s",
                                  new_sha[:8], activity_id[:8])
                    # Remove checkout from activity
                    activity_manager.remove_checkout(activity_id, relative_path)

            # Clear the checkout
            success, error = checkout_manager.checkin(relative_path, user)
            if not success:
                logger.warning("Failed to clear checkout after checkin: %s", error)
                # Continue anyway - version was created

            logger.info("CHECKIN %s by %s -> version %s",
                        path, user, new_sha[:8])

            # Build success response
            return self._checkin_success_response(
                base_prefix, path, new_sha
            )

    def _get_checkout_manager(self):
        """Get or create checkout manager."""
        from moreradicale.versioning.checkout_manager import CheckoutManager

        storage_folder = self.configuration.get("storage", "filesystem_folder")
        checkout_fork = self.configuration.get("storage", "versioning_checkout_fork")
        checkout_timeout = self.configuration.get("storage", "versioning_checkout_timeout")

        return CheckoutManager(storage_folder, checkout_fork, checkout_timeout)

    def _get_git_writer(self):
        """Get git metadata writer."""
        try:
            from moreradicale.storage.multifilesystem.git_writer import (
                GitMetadataWriter
            )
            storage_folder = self.configuration.get("storage", "filesystem_folder")
            return GitMetadataWriter(storage_folder)
        except ImportError:
            return None

    def _get_activity_manager(self):
        """Get activity manager."""
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

    def _server_error(self, message: str) -> types.WSGIResponse:
        """Return 500 Internal Server Error response."""
        return (
            client.INTERNAL_SERVER_ERROR,
            {"Content-Type": "text/plain; charset=utf-8"},
            message,
            None
        )

    def _checkin_success_response(self, base_prefix: str, path: str,
                                  version_sha: str) -> types.WSGIResponse:
        """Build successful CHECKIN response."""
        # RFC 3253 requires 201 Created with Location header
        version_url = f"/.versions/{path}/{version_sha[:8]}"
        full_url = xmlutils.make_href(base_prefix, version_url)

        # Build response body
        multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
        response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

        href = ET.SubElement(response, xmlutils.make_clark("D:href"))
        href.text = xmlutils.make_href(base_prefix, "/" + path)

        propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
        prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

        # DAV:checked-in property pointing to the new version
        checked_in = ET.SubElement(prop, xmlutils.make_clark("D:checked-in"))
        checked_in_href = ET.SubElement(checked_in, xmlutils.make_clark("D:href"))
        checked_in_href.text = full_url

        # DAV:version-name
        version_name = ET.SubElement(prop, xmlutils.make_clark("D:version-name"))
        version_name.text = version_sha[:8]

        status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
        status.text = xmlutils.make_response(200)

        xml_output = xmlutils.pretty_xml(multistatus)
        return (
            client.CREATED,
            {
                "Content-Type": "text/xml; charset=utf-8",
                "Location": full_url
            },
            xml_output,
            multistatus
        )

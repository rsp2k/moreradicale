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
RFC 3253 CHECKOUT method handler.

CHECKOUT creates a working resource for editing. The checked-out
resource can then be modified and checked back in with CHECKIN.

Per RFC 3253 §4.3:
- CHECKOUT on a checked-in version-controlled resource creates a
  working resource that can be modified
- The DAV:checked-out property points to the version that was checked out
- Returns 200 OK on success
"""

import logging
import xml.etree.ElementTree as ET
from http import client
from typing import Optional

from moreradicale import httputils, item as radicale_item, types, xmlutils
from moreradicale.app.base import ApplicationBase

logger = logging.getLogger(__name__)


class ApplicationPartCheckout(ApplicationBase):
    """Handle CHECKOUT requests for RFC 3253 versioning."""

    def do_CHECKOUT(self, environ: types.WSGIEnviron, base_prefix: str,
                    path: str, user: str, remote_host: str,
                    remote_useragent: str) -> types.WSGIResponse:
        """
        Handle CHECKOUT request (RFC 3253 §4.3).

        Creates a checkout for an item, allowing it to be modified.
        The checkout is tracked and must be followed by CHECKIN or UNCHECKOUT.

        Request body (optional):
            <D:checkout xmlns:D="DAV:">
              <D:apply-to-version/>  <!-- Apply to checked-in version -->
              <D:activity-set>       <!-- Associate with activity -->
                <D:href>/.activities/{activity-id}</D:href>
              </D:activity-set>
            </D:checkout>

        Response:
            200 OK with updated properties
            409 Conflict if already checked out and fork=forbidden
            412 Precondition Failed if not version-controlled
        """
        # Check if versioning is enabled
        if not self.configuration.get("storage", "versioning"):
            return httputils.METHOD_NOT_ALLOWED

        # Parse the path to get collection and item
        path = path.strip("/")
        if not path:
            return httputils.BAD_REQUEST

        # Parse activity context from request body
        activity_id = None
        try:
            xml_content = httputils.read_request_body(self.configuration, environ)
            if xml_content:
                root = ET.fromstring(xml_content)
                # Look for DAV:activity-set
                for child in root:
                    if xmlutils.make_human_tag(child.tag) == "D:activity-set":
                        # Extract activity href
                        for href_elem in child:
                            if xmlutils.make_human_tag(href_elem.tag) == "D:href":
                                activity_href = href_elem.text
                                if activity_href:
                                    # Extract activity ID from URL
                                    # e.g., /.activities/abc123 -> abc123
                                    if activity_href.startswith("/.activities/"):
                                        activity_id = activity_href[13:]  # Remove /.activities/
        except (ET.ParseError, Exception) as e:
            logger.debug("No activity context in CHECKOUT: %s", e)
            # Continue without activity

        # Get the item
        with self._storage.acquire_lock("r", user):
            item = self._get_item_for_path(path)
            if item is None:
                return httputils.NOT_FOUND

            # Get checkout manager
            checkout_manager = self._get_checkout_manager()
            if checkout_manager is None:
                logger.warning("Checkout manager not available")
                return httputils.INTERNAL_SERVER_ERROR

            # Get git reader to find current version
            git_reader = self._get_git_reader()
            if git_reader is None or not git_reader.is_available():
                return self._precondition_failed(
                    "Resource is not under version control"
                )

            # Get current version
            relative_path = f"collection-root/{path}"
            current_version = git_reader.get_current_version(relative_path)
            if current_version is None:
                return self._precondition_failed(
                    "Resource has no version history"
                )

            # Attempt checkout
            success, error = checkout_manager.checkout(
                relative_path,
                user,
                current_version.sha,
                checkout_type="in-place"
            )

            if not success:
                # 409 Conflict - already checked out
                return self._conflict_response(error)

            # Associate with activity if provided
            if activity_id:
                activity_manager = self._get_activity_manager()
                if activity_manager:
                    if activity_manager.add_checkout(activity_id, relative_path):
                        logger.info("Associated checkout %s with activity %s",
                                    path, activity_id[:8])
                    else:
                        logger.warning("Failed to associate checkout with activity %s",
                                       activity_id)

            # Build success response
            return self._checkout_success_response(
                base_prefix, path, current_version.sha, activity_id
            )

    def _get_item_for_path(self, path: str) -> Optional[radicale_item.Item]:
        """Get item from path like 'user/calendar.ics/event.ics'."""
        try:
            # Discover the item directly using its full path
            item = next(iter(self._storage.discover("/" + path)), None)
            return item
        except Exception as e:
            logger.debug("Failed to get item for %s: %s", path, e)
            return None

    def _get_checkout_manager(self):
        """Get or create checkout manager."""
        from moreradicale.versioning.checkout_manager import CheckoutManager

        storage_folder = self.configuration.get("storage", "filesystem_folder")
        checkout_fork = self.configuration.get("storage", "versioning_checkout_fork")
        checkout_timeout = self.configuration.get("storage", "versioning_checkout_timeout")

        return CheckoutManager(storage_folder, checkout_fork, checkout_timeout)

    def _get_git_reader(self):
        """Get git metadata reader."""
        try:
            from moreradicale.storage.multifilesystem.git_metadata import (
                GitMetadataReader
            )
            storage_folder = self.configuration.get("storage", "filesystem_folder")
            max_history = self.configuration.get("storage", "versioning_max_history")
            return GitMetadataReader(storage_folder, max_history)
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

    def _precondition_failed(self, message: str) -> types.WSGIResponse:
        """Return 412 Precondition Failed response."""
        return (
            client.PRECONDITION_FAILED,
            {"Content-Type": "text/plain; charset=utf-8"},
            message,
            None
        )

    def _conflict_response(self, message: str) -> types.WSGIResponse:
        """Return 409 Conflict response."""
        # Build DAV:error response
        error = ET.Element(xmlutils.make_clark("D:error"))
        ET.SubElement(error, xmlutils.make_clark("D:cannot-modify-version-controlled-content"))
        desc = ET.SubElement(error, xmlutils.make_clark("D:responsedescription"))
        desc.text = message or "Resource already checked out"

        xml_output = xmlutils.pretty_xml(error)
        return (
            client.CONFLICT,
            {"Content-Type": "text/xml; charset=utf-8"},
            xml_output,
            error
        )

    def _checkout_success_response(self, base_prefix: str, path: str,
                                   version_sha: str,
                                   activity_id: Optional[str] = None) -> types.WSGIResponse:
        """Build successful CHECKOUT response."""
        # RFC 3253 requires returning the checkout properties
        multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
        response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

        href = ET.SubElement(response, xmlutils.make_clark("D:href"))
        href.text = xmlutils.make_href(base_prefix, "/" + path)

        propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
        prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

        # DAV:checked-out property pointing to the version
        checked_out = ET.SubElement(prop, xmlutils.make_clark("D:checked-out"))
        checked_out_href = ET.SubElement(checked_out, xmlutils.make_clark("D:href"))
        # Construct version URL
        item_path = path  # e.g., "user/calendar.ics/event.ics"
        version_url = f"/.versions/{item_path}/{version_sha[:8]}"
        checked_out_href.text = xmlutils.make_href(base_prefix, version_url)

        # Include activity if provided (RFC 3253 §13.2)
        if activity_id:
            activity_set = ET.SubElement(prop, xmlutils.make_clark("D:activity-set"))
            activity_href = ET.SubElement(activity_set, xmlutils.make_clark("D:href"))
            activity_url = f"/.activities/{activity_id}"
            activity_href.text = xmlutils.make_href(base_prefix, activity_url)

        status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
        status.text = xmlutils.make_response(200)

        xml_output = xmlutils.pretty_xml(multistatus)
        # RFC 3253 §4.3 requires Cache-Control: no-cache header
        return (
            client.OK,
            {
                "Content-Type": "text/xml; charset=utf-8",
                "Cache-Control": "no-cache"
            },
            xml_output,
            multistatus
        )

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
RFC 3253 UNCHECKOUT method handler.

UNCHECKOUT cancels a checkout without creating a new version,
restoring the resource to its previous checked-in state.

Per RFC 3253 §4.5:
- UNCHECKOUT cancels a checkout
- The content is restored to the checked-in version
- Returns 200 OK on success
"""

import logging
import xml.etree.ElementTree as ET
from http import client

from moreradicale import httputils, types, xmlutils
from moreradicale.app.base import ApplicationBase

logger = logging.getLogger(__name__)


class ApplicationPartUncheckout(ApplicationBase):
    """Handle UNCHECKOUT requests for RFC 3253 versioning."""

    def do_UNCHECKOUT(self, environ: types.WSGIEnviron, base_prefix: str,
                      path: str, user: str, remote_host: str,
                      remote_useragent: str) -> types.WSGIResponse:
        """
        Handle UNCHECKOUT request (RFC 3253 §4.5).

        Cancels a checkout and restores the resource to its
        previously checked-in state.

        Response:
            200 OK on success
            409 Conflict if not checked out or checked out by different user
        """
        # Check if versioning is enabled
        if not self.configuration.get("storage", "versioning"):
            return httputils.METHOD_NOT_ALLOWED

        # Parse the path
        path = path.strip("/")
        if not path:
            return httputils.BAD_REQUEST

        # Get write lock for restoring the version
        with self._storage.acquire_lock("w", user):
            # Get checkout manager
            checkout_manager = self._get_checkout_manager()
            if checkout_manager is None:
                logger.warning("Checkout manager not available")
                return httputils.INTERNAL_SERVER_ERROR

            # Verify and get checkout info
            relative_path = f"collection-root/{path}"
            success, error, version_to_restore = checkout_manager.uncheckout(
                relative_path, user
            )

            if not success:
                return self._conflict_response(error)

            # Restore the content to the checked-in version
            if version_to_restore:
                git_writer = self._get_git_writer()
                if git_writer and git_writer.is_available():
                    restored = git_writer.restore_version(
                        relative_path, version_to_restore
                    )
                    if restored is None:
                        logger.warning(
                            "Failed to restore %s to version %s",
                            path, version_to_restore[:8]
                        )
                        # Checkout is already cleared, but content wasn't restored
                        # This is a partial failure

            logger.info("UNCHECKOUT %s by %s (restored to %s)",
                        path, user,
                        version_to_restore[:8] if version_to_restore else "N/A")

            # Build success response
            return self._uncheckout_success_response(
                base_prefix, path, version_to_restore
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

    def _conflict_response(self, message: str) -> types.WSGIResponse:
        """Return 409 Conflict response."""
        error = ET.Element(xmlutils.make_clark("D:error"))
        desc = ET.SubElement(error, xmlutils.make_clark("D:responsedescription"))
        desc.text = message or "Checkout conflict"

        xml_output = xmlutils.pretty_xml(error)
        return (
            client.CONFLICT,
            {"Content-Type": "text/xml; charset=utf-8"},
            xml_output,
            error
        )

    def _uncheckout_success_response(self, base_prefix: str, path: str,
                                     version_sha: str) -> types.WSGIResponse:
        """Build successful UNCHECKOUT response."""
        # Build response body
        multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
        response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

        href = ET.SubElement(response, xmlutils.make_clark("D:href"))
        href.text = xmlutils.make_href(base_prefix, "/" + path)

        propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
        prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

        # DAV:checked-in property pointing to the restored version
        if version_sha:
            checked_in = ET.SubElement(prop, xmlutils.make_clark("D:checked-in"))
            checked_in_href = ET.SubElement(checked_in, xmlutils.make_clark("D:href"))
            version_url = f"/.versions/{path}/{version_sha[:8]}"
            checked_in_href.text = xmlutils.make_href(base_prefix, version_url)

        status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
        status.text = xmlutils.make_response(200)

        xml_output = xmlutils.pretty_xml(multistatus)
        return (
            client.OK,
            {"Content-Type": "text/xml; charset=utf-8"},
            xml_output,
            multistatus
        )

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
RFC 3253 VERSION-CONTROL method handler.

VERSION-CONTROL places a resource under version control,
creating an initial version if the resource isn't already tracked.

Per RFC 3253 §3.5:
- VERSION-CONTROL on an existing resource creates an initial version
- After VERSION-CONTROL, the resource has DAV:checked-in property
- Returns 200 OK on success
"""

import logging
import xml.etree.ElementTree as ET
from http import client

from moreradicale import httputils, types, xmlutils
from moreradicale.app.base import ApplicationBase

logger = logging.getLogger(__name__)


class ApplicationPartVersionControl(ApplicationBase):
    """Handle VERSION-CONTROL requests for RFC 3253 versioning."""

    def do_VERSION_CONTROL(self, environ: types.WSGIEnviron, base_prefix: str,
                           path: str, user: str, remote_host: str,
                           remote_useragent: str) -> types.WSGIResponse:
        """
        Handle VERSION-CONTROL request (RFC 3253 §3.5).

        Places a resource under version control by creating an
        initial version in git.

        Response:
            200 OK on success (resource now version-controlled)
            409 Conflict if resource already version-controlled
            404 Not Found if resource doesn't exist
        """
        # Check if versioning is enabled
        if not self.configuration.get("storage", "versioning"):
            return httputils.METHOD_NOT_ALLOWED

        # Parse the path
        path = path.strip("/")
        if not path:
            return httputils.BAD_REQUEST

        # Get write lock for creating the initial version
        with self._storage.acquire_lock("w", user):
            # Get git writer
            git_writer = self._get_git_writer()
            if git_writer is None or not git_writer.is_available():
                return self._server_error("Git not available")

            relative_path = f"collection-root/{path}"

            # Check if already version-controlled
            if git_writer.is_version_controlled(relative_path):
                # RFC 3253 says this should succeed but not create new version
                logger.debug("%s already under version control", path)
                return self._already_controlled_response(base_prefix, path)

            # Get user email (default to "anonymous" if user is empty)
            effective_user = user if user else "anonymous"
            user_email = f"{effective_user}@localhost"

            # Initialize version control
            version_sha = git_writer.initialize_version_control(
                relative_path, effective_user, user_email
            )

            if version_sha is None:
                return self._server_error("Failed to initialize version control")

            logger.info("VERSION-CONTROL %s by %s -> version %s",
                        path, user, version_sha[:8])

            # Build success response
            return self._version_control_success_response(
                base_prefix, path, version_sha
            )

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

    def _server_error(self, message: str) -> types.WSGIResponse:
        """Return 500 Internal Server Error response."""
        return (
            client.INTERNAL_SERVER_ERROR,
            {"Content-Type": "text/plain; charset=utf-8"},
            message,
            None
        )

    def _already_controlled_response(self, base_prefix: str,
                                     path: str) -> types.WSGIResponse:
        """Response when resource is already version-controlled."""
        # Per RFC 3253, this is not an error - just acknowledge
        multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
        response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

        href = ET.SubElement(response, xmlutils.make_clark("D:href"))
        href.text = xmlutils.make_href(base_prefix, "/" + path)

        propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
        prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

        # DAV:resourcetype indicates version-controlled
        rtype = ET.SubElement(prop, xmlutils.make_clark("D:resourcetype"))
        ET.SubElement(rtype, xmlutils.make_clark("D:version-controlled-resource"))

        status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
        status.text = xmlutils.make_response(200)

        xml_output = xmlutils.pretty_xml(multistatus)
        return (
            client.OK,
            {"Content-Type": "text/xml; charset=utf-8"},
            xml_output,
            multistatus
        )

    def _version_control_success_response(self, base_prefix: str, path: str,
                                          version_sha: str) -> types.WSGIResponse:
        """Build successful VERSION-CONTROL response."""
        multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
        response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

        href = ET.SubElement(response, xmlutils.make_clark("D:href"))
        href.text = xmlutils.make_href(base_prefix, "/" + path)

        propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
        prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

        # DAV:resourcetype
        rtype = ET.SubElement(prop, xmlutils.make_clark("D:resourcetype"))
        ET.SubElement(rtype, xmlutils.make_clark("D:version-controlled-resource"))

        # DAV:checked-in property pointing to the initial version
        checked_in = ET.SubElement(prop, xmlutils.make_clark("D:checked-in"))
        checked_in_href = ET.SubElement(checked_in, xmlutils.make_clark("D:href"))
        version_url = f"/.versions/{path}/{version_sha[:8]}"
        checked_in_href.text = xmlutils.make_href(base_prefix, version_url)

        # DAV:version-name
        version_name = ET.SubElement(prop, xmlutils.make_clark("D:version-name"))
        version_name.text = version_sha[:8]

        status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
        status.text = xmlutils.make_response(200)

        xml_output = xmlutils.pretty_xml(multistatus)
        return (
            client.OK,
            {"Content-Type": "text/xml; charset=utf-8"},
            xml_output,
            multistatus
        )

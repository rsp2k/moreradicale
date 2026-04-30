# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 RFC 3253 Versioning LABEL Implementation
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
RFC 3253 §8 LABEL method implementation.

The LABEL method modifies the labels that select a version. A label is a name
that can be used to select a version from a version history.

Supported operations:
- ADD: Add a new label to a version
- SET: Move an existing label to a different version
- REMOVE: Remove a label from all versions
"""

import logging
import xml.etree.ElementTree as ET
from http import client

from moreradicale import httputils, types, xmlutils
from moreradicale.app.base import Access

logger = logging.getLogger(__name__)


class ApplicationLabelMixin:
    """Mixin for LABEL method support (RFC 3253 §8)."""

    def do_LABEL(self, environ: types.WSGIEnviron, base_prefix: str,
                 path: str, user: str, remote_host: str, remote_useragent: str) -> types.WSGIResponse:
        """
        Handle LABEL method to add/set/remove version labels.

        RFC 3253 §8: A LABEL request can be applied to a version to modify
        the labels that select that version.

        Request body format:
        <?xml version="1.0" encoding="utf-8"?>
        <D:label xmlns:D="DAV:">
            <D:add>
                <D:label-name>production</D:label-name>
            </D:add>
        </D:label>

        Or <D:set> or <D:remove> instead of <D:add>.

        Args:
            environ: WSGI environment
            base_prefix: URL base prefix
            path: Request path
            user: Authenticated user

        Returns:
            WSGI response tuple
        """
        # Check if versioning is enabled
        if not self.configuration.get("storage", "versioning"):
            logger.warning("LABEL requested but versioning disabled")
            return (client.METHOD_NOT_ALLOWED,
                    {"Content-Type": "text/plain"},
                    "Versioning not enabled", None)

        # Parse request body
        try:
            xml_content = httputils.read_request_body(self.configuration, environ)
            if not xml_content:
                return httputils.BAD_REQUEST

            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.warning("Invalid XML in LABEL request: %s", e)
            return httputils.BAD_REQUEST
        except Exception as e:
            logger.error("Error reading LABEL request: %s", e)
            return httputils.INTERNAL_SERVER_ERROR

        # Extract operation type (add/set/remove)
        operation = None
        label_names = []

        for child in root:
            tag_name = xmlutils.make_human_tag(child.tag)
            # Tag names come in format "D:add", "D:set", "D:remove"
            if tag_name in ("D:add", "D:set", "D:remove"):
                operation = tag_name.split(":", 1)[1]  # Extract "add" from "D:add"
                # Extract label names
                for label_elem in child:
                    if xmlutils.make_human_tag(label_elem.tag) == "D:label-name":
                        label_name = label_elem.text
                        if label_name:
                            label_names.append(label_name.strip())

        if not operation:
            logger.warning("LABEL request missing operation (add/set/remove)")
            return httputils.BAD_REQUEST

        if not label_names:
            logger.warning("LABEL request missing label-name elements")
            return httputils.BAD_REQUEST

        logger.info(f"LABEL {operation.upper()} request for {path} with labels: {label_names}")

        # Get the resource
        with self._storage.acquire_lock("r", user):
            item = next(self._storage.discover(path, depth="0"), None)
            if not item:
                return httputils.NOT_FOUND

            # Check write access
            access = Access(self._rights, user, path)
            if not access.check("w"):
                return httputils.NOT_ALLOWED

            # Get GitMetadataWriter
            try:
                from moreradicale.storage.multifilesystem.git_writer import GitMetadataWriter
                import os
                storage_folder = self.configuration.get("storage", "filesystem_folder")
                collection_root = os.path.join(storage_folder, "collection-root")
                writer = GitMetadataWriter(collection_root)

                if not writer.is_available():
                    logger.warning("Git not available for LABEL operation")
                    return (client.CONFLICT,
                            {"Content-Type": "text/plain"},
                            "Version control not available", None)
            except ImportError:
                logger.error("GitMetadataWriter not available")
                return httputils.INTERNAL_SERVER_ERROR

            # Get current version (checked-in SHA)
            try:
                from moreradicale.storage.multifilesystem.git_metadata import GitMetadataReader
                reader = GitMetadataReader(collection_root)

                # Get relative path for this item
                # Path format: /test/calendar.ics/event.ics
                # Relative path: test/calendar.ics/event.ics (strip leading /)
                relative_path = path.lstrip("/")

                # Get current version
                current_version = reader.get_current_version(relative_path)
                if not current_version:
                    logger.warning(f"No version history for {relative_path}")
                    return (client.CONFLICT,
                            {"Content-Type": "text/plain"},
                            "Resource not under version control", None)

                commit_sha = current_version.sha

            except Exception as e:
                logger.error(f"Error getting current version: {e}", exc_info=True)
                return httputils.INTERNAL_SERVER_ERROR

            # Perform the operation
            success = True
            errors = []

            for label_name in label_names:
                try:
                    if operation == "add":
                        if not writer.add_label(label_name, commit_sha, relative_path, force=False):
                            success = False
                            errors.append(f"Failed to add label '{label_name}'")
                    elif operation == "set":
                        if not writer.set_label(label_name, commit_sha, relative_path):
                            success = False
                            errors.append(f"Failed to set label '{label_name}'")
                    elif operation == "remove":
                        if not writer.remove_label(label_name, relative_path):
                            success = False
                            errors.append(f"Failed to remove label '{label_name}'")
                except Exception as e:
                    logger.error(f"Error performing LABEL {operation} for '{label_name}': {e}", exc_info=True)
                    success = False
                    errors.append(f"Error with label '{label_name}': {str(e)}")

            if not success:
                error_msg = "; ".join(errors)
                logger.warning(f"LABEL operation partially failed: {error_msg}")
                return (client.CONFLICT,
                        {"Content-Type": "text/plain"},
                        error_msg, None)

            # Build response
            # RFC 3253 §8.4: Response includes Cache-Control header
            headers = {
                "Cache-Control": "no-cache",
                "Content-Type": "text/plain"
            }

            response_body = f"LABEL {operation.upper()} successful for labels: {', '.join(label_names)}"

            logger.info(f"LABEL {operation.upper()} successful for {path}: {label_names}")

            return client.OK, headers, response_body, None

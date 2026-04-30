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
RFC 3253 Versioning Handler for virtual /.versions/ paths.

This module handles HTTP requests to version history resources,
providing read-only access to git history of calendar/contact items.
"""

import logging
import xml.etree.ElementTree as ET
from http import client
from typing import Optional, Tuple

from moreradicale import config, httputils, storage, types, xmlutils

logger = logging.getLogger(__name__)


class VersioningHandler:
    """Handle versioning requests for RFC 3253 read-only versioning."""

    def __init__(self, configuration: config.Configuration,
                 storage: storage.BaseStorage):
        """Initialize versioning handler.

        Args:
            configuration: Radicale configuration
            storage: Storage instance for accessing collections
        """
        self.configuration = configuration
        self.storage = storage
        self._enabled = configuration.get("storage", "versioning")
        self._git_reader = None

    def _get_git_reader(self):
        """Lazy-load the GitMetadataReader."""
        if self._git_reader is None and self._enabled:
            try:
                from moreradicale.storage.multifilesystem.git_metadata import (
                    GitMetadataReader
                )
                storage_folder = self.configuration.get(
                    "storage", "filesystem_folder")
                max_history = self.configuration.get(
                    "storage", "versioning_max_history")
                self._git_reader = GitMetadataReader(storage_folder, max_history)
            except ImportError:
                logger.warning("Git metadata reader not available")
                self._git_reader = False  # Mark as unavailable
        return self._git_reader if self._git_reader else None

    def should_handle(self, path: str, method: str) -> bool:
        """Check if this handler should process the request.

        Args:
            path: Request path
            method: HTTP method

        Returns:
            True if this handler should process the request
        """
        if not self._enabled:
            return False
        return path.startswith("/.versions/")

    def parse_version_path(self, path: str
                           ) -> Tuple[Optional[str], Optional[str],
                                      Optional[str]]:
        """Parse a version path into components.

        Args:
            path: Path like /.versions/user/calendar.ics/event.ics/abc12345

        Returns:
            Tuple of (collection_path, item_href, version_sha)
            version_sha is None if requesting version history
        """
        # Remove /.versions/ prefix
        if not path.startswith("/.versions/"):
            return None, None, None

        remainder = path[len("/.versions/"):].strip("/")
        if not remainder:
            return None, None, None

        parts = remainder.split("/")
        if len(parts) < 2:
            # Need at least collection and item
            return None, None, None

        # Last part might be a version SHA or part of item path
        # Items end with .ics or .vcf
        # Check if last part looks like a SHA (8+ hex chars)
        last = parts[-1]
        if len(last) >= 8 and all(c in "0123456789abcdef" for c in last.lower()):
            # Last part is a version SHA
            version_sha = last
            item_parts = parts[:-1]
        else:
            # No version SHA, requesting version history
            version_sha = None
            item_parts = parts

        # Find where collection ends and item begins
        # Items are the last component ending in .ics or .vcf
        if len(item_parts) < 2:
            return None, None, None

        item_href = item_parts[-1]
        collection_path = "/".join(item_parts[:-1])

        return collection_path, item_href, version_sha

    def handle_request(self, environ: types.WSGIEnviron, base_prefix: str,
                       path: str, user: str
                       ) -> types.WSGIResponse:
        """Handle a versioning request.

        Args:
            environ: WSGI environment
            base_prefix: URL base prefix
            path: Request path
            user: Authenticated user

        Returns:
            WSGI response tuple
        """
        method = environ.get("REQUEST_METHOD", "GET")

        if method == "GET":
            return self._handle_get(environ, base_prefix, path, user)
        elif method == "PROPFIND":
            return self._handle_propfind(environ, base_prefix, path, user)
        elif method == "REPORT":
            return self._handle_report(environ, base_prefix, path, user)
        else:
            # Versioning is read-only
            return httputils.METHOD_NOT_ALLOWED

    def _handle_get(self, environ: types.WSGIEnviron, base_prefix: str,
                    path: str, user: str) -> types.WSGIResponse:
        """Handle GET request for version content.

        Returns historical content at a specific version.
        """
        collection_path, item_href, version_sha = self.parse_version_path(path)

        if not collection_path or not item_href:
            return httputils.NOT_FOUND

        git_reader = self._get_git_reader()
        if not git_reader or not git_reader.is_available():
            return httputils.NOT_FOUND

        if version_sha is None:
            # Requesting version history listing - return XML
            return self._get_version_history(
                base_prefix, collection_path, item_href, user)

        # Get content at specific version
        relative_path = f"collection-root/{collection_path}/{item_href}"
        content = git_reader.get_version_content(relative_path, version_sha)

        if content is None:
            return httputils.NOT_FOUND

        # Determine content type from item extension
        if item_href.endswith(".ics"):
            content_type = "text/calendar; charset=utf-8"
        elif item_href.endswith(".vcf"):
            content_type = "text/vcard; charset=utf-8"
        else:
            content_type = "application/octet-stream"

        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(content.encode("utf-8"))),
            # Mark as immutable - versions don't change
            "Cache-Control": "public, max-age=31536000, immutable",
        }

        return client.OK, headers, content, None

    def _get_version_history(self, base_prefix: str, collection_path: str,
                             item_href: str, user: str
                             ) -> types.WSGIResponse:
        """Return version history as XML.

        This is a simplified response listing all versions of an item.
        """
        git_reader = self._get_git_reader()
        relative_path = f"collection-root/{collection_path}/{item_href}"
        versions = git_reader.get_item_history(relative_path)

        # Build XML response
        multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))

        for version in versions:
            response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

            # Version URL
            href = ET.SubElement(response, xmlutils.make_clark("D:href"))
            version_url = f"/.versions/{collection_path}/{item_href}/{version.short_sha}"
            href.text = xmlutils.make_href(base_prefix, version_url)

            # Properties
            propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
            prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

            # DAV:version-name
            vname = ET.SubElement(prop, xmlutils.make_clark("D:version-name"))
            vname.text = version.version_name

            # DAV:creator-displayname
            creator = ET.SubElement(prop, xmlutils.make_clark("D:creator-displayname"))
            creator.text = version.author

            # DAV:getlastmodified
            modified = ET.SubElement(prop, xmlutils.make_clark("D:getlastmodified"))
            modified.text = version.timestamp.strftime("%a, %d %b %Y %H:%M:%S GMT")

            # DAV:comment (commit message)
            comment = ET.SubElement(prop, xmlutils.make_clark("D:comment"))
            comment.text = version.message

            status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
            status.text = xmlutils.make_response(200)

        xml_output = xmlutils.pretty_xml(multistatus)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "Content-Length": str(len(xml_output)),
        }

        return client.MULTI_STATUS, headers, xml_output, None

    def _handle_propfind(self, environ: types.WSGIEnviron, base_prefix: str,
                         path: str, user: str) -> types.WSGIResponse:
        """Handle PROPFIND on version resources."""
        collection_path, item_href, version_sha = self.parse_version_path(path)

        if not collection_path or not item_href:
            return httputils.NOT_FOUND

        git_reader = self._get_git_reader()
        if not git_reader or not git_reader.is_available():
            return httputils.NOT_FOUND

        relative_path = f"collection-root/{collection_path}/{item_href}"

        if version_sha:
            # PROPFIND on specific version - get version with relationships
            version = git_reader.get_version_with_relationships(
                relative_path, version_sha)

            if not version:
                return httputils.NOT_FOUND

            # Build response for specific version
            multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
            response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

            href = ET.SubElement(response, xmlutils.make_clark("D:href"))
            href.text = xmlutils.make_href(base_prefix, path)

            propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
            prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

            # Resource type - DAV:version
            rtype = ET.SubElement(prop, xmlutils.make_clark("D:resourcetype"))
            ET.SubElement(rtype, xmlutils.make_clark("D:version"))

            # DAV:version-name
            vname = ET.SubElement(prop, xmlutils.make_clark("D:version-name"))
            vname.text = version.version_name

            # DAV:creator-displayname
            creator = ET.SubElement(prop, xmlutils.make_clark("D:creator-displayname"))
            creator.text = version.author

            # DAV:getlastmodified
            modified = ET.SubElement(prop, xmlutils.make_clark("D:getlastmodified"))
            modified.text = version.timestamp.strftime("%a, %d %b %Y %H:%M:%S GMT")

            # RFC 3253 §3.3.2 - DAV:predecessor-set
            pred_set = ET.SubElement(prop, xmlutils.make_clark("D:predecessor-set"))
            if version.predecessor_sha:
                pred_href = ET.SubElement(pred_set, xmlutils.make_clark("D:href"))
                pred_url = f"/.versions/{collection_path}/{item_href}/{version.predecessor_sha[:8]}"
                pred_href.text = xmlutils.make_href(base_prefix, pred_url)

            # RFC 3253 §3.3.3 - DAV:successor-set
            succ_set = ET.SubElement(prop, xmlutils.make_clark("D:successor-set"))
            if version.successor_shas:
                for succ_sha in version.successor_shas:
                    succ_href = ET.SubElement(succ_set, xmlutils.make_clark("D:href"))
                    succ_url = f"/.versions/{collection_path}/{item_href}/{succ_sha[:8]}"
                    succ_href.text = xmlutils.make_href(base_prefix, succ_url)

            status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
            status.text = xmlutils.make_response(200)

        else:
            # PROPFIND on version-history collection
            multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
            response = ET.SubElement(multistatus, xmlutils.make_clark("D:response"))

            href = ET.SubElement(response, xmlutils.make_clark("D:href"))
            href.text = xmlutils.make_href(base_prefix, path)

            propstat = ET.SubElement(response, xmlutils.make_clark("D:propstat"))
            prop = ET.SubElement(propstat, xmlutils.make_clark("D:prop"))

            # Resource type - DAV:version-history
            rtype = ET.SubElement(prop, xmlutils.make_clark("D:resourcetype"))
            ET.SubElement(rtype, xmlutils.make_clark("D:version-history"))
            ET.SubElement(rtype, xmlutils.make_clark("D:collection"))

            # DAV:displayname
            displayname = ET.SubElement(prop, xmlutils.make_clark("D:displayname"))
            displayname.text = f"Version History: {item_href}"

            status = ET.SubElement(propstat, xmlutils.make_clark("D:status"))
            status.text = xmlutils.make_response(200)

        xml_output = xmlutils.pretty_xml(multistatus)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "Content-Length": str(len(xml_output)),
        }

        return client.MULTI_STATUS, headers, xml_output, None

    def _handle_report(self, environ: types.WSGIEnviron, base_prefix: str,
                       path: str, user: str) -> types.WSGIResponse:
        """Handle REPORT request (VERSION-TREE report).

        RFC 3253 Section 3.6 - VERSION-TREE report.
        """
        # For now, return same as GET version history
        collection_path, item_href, version_sha = self.parse_version_path(path)

        if not collection_path or not item_href:
            return httputils.NOT_FOUND

        # VERSION-TREE report returns the version history
        return self._get_version_history(
            base_prefix, collection_path, item_href, user)

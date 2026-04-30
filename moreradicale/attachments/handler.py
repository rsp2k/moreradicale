# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 Ryan Malloy and contributors
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
HTTP request handler for RFC 8607 managed attachment operations.

Handles POST requests with action= query parameters:
- attachment-add: Upload new attachment
- attachment-update: Replace existing attachment
- attachment-remove: Delete attachment
"""

import re
import socket
from http import client
from typing import TYPE_CHECKING, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote

from moreradicale import httputils, storage, types
from moreradicale.attachments import (
    ATTACHMENT_ADD,
    ATTACHMENT_REMOVE,
    ATTACHMENT_UPDATE,
    CAL_MANAGED_ID_HEADER,
    AttachmentLimitError,
    AttachmentManager,
    AttachmentNotFoundError,
    AttachmentSizeError,
    add_managed_attach,
    count_managed_attachments,
    remove_managed_attach,
    update_managed_attach,
)
from moreradicale.attachments.storage import AttachmentStorage
from moreradicale.log import logger

if TYPE_CHECKING:
    from moreradicale import config, item


class AttachmentHandler:
    """
    Handles RFC 8607 managed attachment POST requests.

    POST /calendar/event.ics?action=attachment-add
    POST /calendar/event.ics?action=attachment-update&managed-id=X
    POST /calendar/event.ics?action=attachment-remove&managed-id=X
    """

    def __init__(self, storage_module: "storage.BaseStorage",
                 configuration: "config.Configuration") -> None:
        self.storage = storage_module
        self.configuration = configuration
        self.manager = AttachmentManager(configuration)
        self.attachment_storage = AttachmentStorage(configuration)

    def handle_attachment_post(
        self,
        environ: types.WSGIEnviron,
        base_prefix: str,
        path: str,
        user: str,
        action: str,
        query_params: Dict[str, list]
    ) -> types.WSGIResponse:
        """
        Handle an attachment POST request.

        Args:
            environ: WSGI environment
            base_prefix: Server base URL prefix
            path: Path to calendar object (e.g., /alice/calendar/event.ics)
            user: Authenticated username
            action: Attachment action (add, update, remove)
            query_params: Parsed query string parameters

        Returns:
            WSGI response tuple
        """
        # Get managed-id for update/remove operations
        managed_id = query_params.get("managed-id", [None])[0]

        if action == ATTACHMENT_ADD:
            return self._handle_add(environ, base_prefix, path, user)
        elif action == ATTACHMENT_UPDATE:
            if not managed_id:
                logger.warning("attachment-update missing managed-id parameter")
                return httputils.BAD_REQUEST
            return self._handle_update(environ, base_prefix, path, user, managed_id)
        elif action == ATTACHMENT_REMOVE:
            if not managed_id:
                logger.warning("attachment-remove missing managed-id parameter")
                return httputils.BAD_REQUEST
            return self._handle_remove(path, user, managed_id)
        else:
            logger.warning("Unknown attachment action: %s", action)
            return httputils.BAD_REQUEST

    def _handle_add(
        self,
        environ: types.WSGIEnviron,
        base_prefix: str,
        path: str,
        user: str
    ) -> types.WSGIResponse:
        """
        Handle attachment-add: upload and associate a new attachment.

        Request:
            POST /calendar/event.ics?action=attachment-add
            Content-Type: application/pdf
            Content-Disposition: attachment; filename="report.pdf"
            [binary data]

        Response:
            201 Created
            Cal-Managed-ID: abc123
        """
        # Read binary data from request body
        try:
            content_length = int(environ.get("CONTENT_LENGTH", 0))
        except ValueError:
            content_length = 0

        # Check size before reading
        if content_length > self.manager.max_size:
            logger.warning("Attachment too large: %d > %d",
                          content_length, self.manager.max_size)
            return (client.REQUEST_ENTITY_TOO_LARGE,
                    {"Content-Type": "text/plain"},
                    f"Attachment exceeds maximum size of {self.manager.max_size} bytes",
                    None)

        try:
            data = environ["wsgi.input"].read(content_length)
        except socket.timeout:
            logger.warning("Timeout reading attachment data")
            return httputils.REQUEST_TIMEOUT

        if not data:
            logger.warning("Empty attachment data")
            return httputils.BAD_REQUEST

        # Verify actual size
        if len(data) > self.manager.max_size:
            return (client.REQUEST_ENTITY_TOO_LARGE,
                    {"Content-Type": "text/plain"},
                    f"Attachment exceeds maximum size of {self.manager.max_size} bytes",
                    None)

        # Get content type
        content_type = environ.get("CONTENT_TYPE", "application/octet-stream")
        # Strip charset and other parameters for storage
        content_type = content_type.split(";")[0].strip()

        # Get filename from Content-Disposition header
        filename = self._extract_filename(environ.get("HTTP_CONTENT_DISPOSITION", ""))
        if not filename:
            filename = "attachment"

        # Get the calendar object
        with self.storage.acquire_lock("w", user):
            item = self._get_calendar_item(path, user)
            if item is None:
                return httputils.NOT_FOUND

            # Check attachment limit
            current_count = count_managed_attachments(item.vobject_item)
            if current_count >= self.manager.max_per_resource:
                logger.warning("Attachment limit reached: %d >= %d",
                              current_count, self.manager.max_per_resource)
                return (client.INSUFFICIENT_STORAGE,
                        {"Content-Type": "text/plain"},
                        f"Maximum of {self.manager.max_per_resource} attachments per resource",
                        None)

            # Get calendar path and event UID
            path_parts = path.strip("/").split("/")
            if len(path_parts) >= 2:
                calendar_path = "/" + "/".join(path_parts[:2]) + "/"
            else:
                calendar_path = path

            event_uid = self._get_event_uid(item)

            # Generate managed ID and store attachment
            managed_id = self.manager.generate_managed_id()

            try:
                metadata = self.attachment_storage.store(
                    owner=user,
                    managed_id=managed_id,
                    data=data,
                    filename=filename,
                    content_type=content_type,
                    calendar_path=calendar_path,
                    event_uid=event_uid,
                )
            except AttachmentSizeError as e:
                return (client.REQUEST_ENTITY_TOO_LARGE,
                        {"Content-Type": "text/plain"}, str(e), None)

            # Get attachment URL
            attachment_url = self.manager.get_attachment_url(
                base_prefix, user, managed_id
            )

            # Add ATTACH property to calendar object
            add_managed_attach(
                item.vobject_item,
                managed_id=managed_id,
                url=attachment_url,
                filename=filename,
                size=len(data),
                fmttype=content_type,
            )

            # Save updated calendar object
            self._save_item(item, path, user)

            logger.info("Added attachment %s to %s (size=%d, type=%s)",
                       managed_id, path, len(data), content_type)

            # Return 201 Created with Cal-Managed-ID header
            return (client.CREATED,
                    {CAL_MANAGED_ID_HEADER: managed_id,
                     "Content-Type": "text/plain"},
                    "", None)

    def _handle_update(
        self,
        environ: types.WSGIEnviron,
        base_prefix: str,
        path: str,
        user: str,
        managed_id: str
    ) -> types.WSGIResponse:
        """
        Handle attachment-update: replace existing attachment data.

        Request:
            POST /calendar/event.ics?action=attachment-update&managed-id=X
            Content-Type: application/pdf
            [binary data]

        Response:
            204 No Content
        """
        # Read binary data
        try:
            content_length = int(environ.get("CONTENT_LENGTH", 0))
        except ValueError:
            content_length = 0

        if content_length > self.manager.max_size:
            return (client.REQUEST_ENTITY_TOO_LARGE,
                    {"Content-Type": "text/plain"},
                    f"Attachment exceeds maximum size of {self.manager.max_size} bytes",
                    None)

        try:
            data = environ["wsgi.input"].read(content_length)
        except socket.timeout:
            return httputils.REQUEST_TIMEOUT

        if not data:
            return httputils.BAD_REQUEST

        content_type = environ.get("CONTENT_TYPE", "application/octet-stream")
        content_type = content_type.split(";")[0].strip()

        filename = self._extract_filename(environ.get("HTTP_CONTENT_DISPOSITION", ""))

        with self.storage.acquire_lock("w", user):
            item = self._get_calendar_item(path, user)
            if item is None:
                return httputils.NOT_FOUND

            # Verify attachment exists
            if not self.attachment_storage.exists(user, managed_id):
                logger.warning("Attachment %s not found for update", managed_id)
                return httputils.NOT_FOUND

            # Get existing metadata for calendar_path and event_uid
            old_metadata = self.attachment_storage.get_metadata(user, managed_id)
            calendar_path = old_metadata.calendar_path if old_metadata else path
            event_uid = old_metadata.event_uid if old_metadata else self._get_event_uid(item)

            # Use old filename if not provided
            if not filename and old_metadata:
                filename = old_metadata.filename
            elif not filename:
                filename = "attachment"

            # Delete old and store new
            self.attachment_storage.delete(user, managed_id)

            try:
                self.attachment_storage.store(
                    owner=user,
                    managed_id=managed_id,
                    data=data,
                    filename=filename,
                    content_type=content_type,
                    calendar_path=calendar_path,
                    event_uid=event_uid,
                )
            except AttachmentSizeError as e:
                return (client.REQUEST_ENTITY_TOO_LARGE,
                        {"Content-Type": "text/plain"}, str(e), None)

            # Update ATTACH property
            attachment_url = self.manager.get_attachment_url(
                base_prefix, user, managed_id
            )

            updated = update_managed_attach(
                item.vobject_item,
                managed_id=managed_id,
                url=attachment_url,
                filename=filename,
                size=len(data),
                fmttype=content_type,
            )

            if updated:
                self._save_item(item, path, user)

            logger.info("Updated attachment %s on %s", managed_id, path)

            return (client.NO_CONTENT, {}, None, None)

    def _handle_remove(
        self,
        path: str,
        user: str,
        managed_id: str
    ) -> types.WSGIResponse:
        """
        Handle attachment-remove: delete an attachment.

        Request:
            POST /calendar/event.ics?action=attachment-remove&managed-id=X

        Response:
            204 No Content
        """
        with self.storage.acquire_lock("w", user):
            item = self._get_calendar_item(path, user)
            if item is None:
                return httputils.NOT_FOUND

            # Remove from storage
            deleted = self.attachment_storage.delete(user, managed_id)

            if not deleted:
                logger.warning("Attachment %s not found for removal", managed_id)
                # Still try to remove from calendar object

            # Remove ATTACH property from calendar object
            removed = remove_managed_attach(item.vobject_item, managed_id)

            if removed:
                self._save_item(item, path, user)

            logger.info("Removed attachment %s from %s", managed_id, path)

            return (client.NO_CONTENT, {}, None, None)

    def _get_calendar_item(self, path: str, user: str) -> Optional["item.Item"]:
        """Get a calendar item by path."""
        items = list(self.storage.discover(path))
        if not items:
            return None

        item = items[0]
        if isinstance(item, storage.BaseCollection):
            # Path is to a collection, not an item
            return None

        return item

    def _save_item(self, item: "item.Item", path: str, user: str) -> None:
        """Save an updated calendar item."""
        # Get the parent collection
        path_parts = path.strip("/").split("/")
        if len(path_parts) >= 2:
            collection_path = "/" + "/".join(path_parts[:-1]) + "/"
        else:
            collection_path = "/"

        collections = list(self.storage.discover(collection_path))
        if collections and isinstance(collections[0], storage.BaseCollection):
            collection = collections[0]
            # Get the item href (last path component)
            href = path_parts[-1] if path_parts else ""
            # Clear cached serialization to force re-serialization from vobject
            # This is necessary because we modified vobject_item directly
            item._text = None
            # Upload the modified item
            collection.upload(href, item)

    def _get_event_uid(self, item: "item.Item") -> str:
        """Extract UID from calendar item."""
        try:
            vobj = item.vobject_item
            if hasattr(vobj, 'vevent') and hasattr(vobj.vevent, 'uid'):
                return str(vobj.vevent.uid.value)
            elif hasattr(vobj, 'vtodo') and hasattr(vobj.vtodo, 'uid'):
                return str(vobj.vtodo.uid.value)
        except Exception:
            pass
        return ""

    def _extract_filename(self, content_disposition: str) -> str:
        """
        Extract filename from Content-Disposition header.

        Handles both RFC 2616 and RFC 5987 (UTF-8) encoded filenames.
        """
        if not content_disposition:
            return ""

        # Try RFC 5987 filename* parameter first (UTF-8 encoded)
        match = re.search(r"filename\*=(?:UTF-8''|utf-8'')([^;\s]+)",
                         content_disposition, re.IGNORECASE)
        if match:
            return unquote(match.group(1))

        # Try standard filename parameter
        match = re.search(r'filename="([^"]+)"', content_disposition)
        if match:
            return match.group(1)

        match = re.search(r"filename=([^;\s]+)", content_disposition)
        if match:
            return match.group(1).strip('"\'')

        return ""


def is_attachment_request(query_string: str) -> Optional[str]:
    """
    Check if a query string indicates an attachment operation.

    Args:
        query_string: URL query string

    Returns:
        Action name if this is an attachment request, None otherwise
    """
    if not query_string:
        return None

    params = parse_qs(query_string)
    action = params.get("action", [None])[0]

    if action in (ATTACHMENT_ADD, ATTACHMENT_UPDATE, ATTACHMENT_REMOVE):
        return action

    return None

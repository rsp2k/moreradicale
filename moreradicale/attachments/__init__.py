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
RFC 8607 Managed Attachments support for CalDAV.

This module implements server-side attachment management, allowing clients
to upload attachments separately from calendar data. The server stores
attachments and returns URL references instead of inline base64 data.

Key concepts:
- MANAGED-ID: Unique identifier for each attachment (server-generated UUID)
- Attachments stored in configurable filesystem path
- ATTACH properties reference server URLs instead of embedding data
- POST requests with action= query parameter for add/update/remove

Reference: https://www.rfc-editor.org/rfc/rfc8607.html
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

from moreradicale.log import logger

if TYPE_CHECKING:
    from moreradicale import config

# Constants for RFC 8607
MANAGED_ID_PARAM = "MANAGED-ID"
FILENAME_PARAM = "FILENAME"
SIZE_PARAM = "SIZE"
FMTTYPE_PARAM = "FMTTYPE"

# Query parameter values for attachment operations
ATTACHMENT_ADD = "attachment-add"
ATTACHMENT_UPDATE = "attachment-update"
ATTACHMENT_REMOVE = "attachment-remove"
ATTACHMENT_ACTIONS = {ATTACHMENT_ADD, ATTACHMENT_UPDATE, ATTACHMENT_REMOVE}

# HTTP header for returning managed ID
CAL_MANAGED_ID_HEADER = "Cal-Managed-ID"

# DAV compliance token
DAV_MANAGED_ATTACHMENTS = "calendar-managed-attachments"

# Default attachment URL path
ATTACHMENTS_PATH = "/.attachments"


class AttachmentError(Exception):
    """Base exception for attachment operations."""
    pass


class AttachmentSizeError(AttachmentError):
    """Attachment exceeds size limit."""
    pass


class AttachmentLimitError(AttachmentError):
    """Too many attachments on resource."""
    pass


class AttachmentNotFoundError(AttachmentError):
    """Attachment with given managed_id not found."""
    pass


class AttachmentAccessError(AttachmentError):
    """User not authorized to access attachment."""
    pass


@dataclass
class AttachmentMetadata:
    """Metadata for a managed attachment."""
    managed_id: str
    filename: str
    content_type: str
    size: int
    created: datetime
    owner: str
    calendar_path: str
    event_uid: str

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "managed_id": self.managed_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "created": self.created.isoformat(),
            "owner": self.owner,
            "calendar_path": self.calendar_path,
            "event_uid": self.event_uid,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AttachmentMetadata":
        """Create from dictionary."""
        return cls(
            managed_id=data["managed_id"],
            filename=data["filename"],
            content_type=data["content_type"],
            size=data["size"],
            created=datetime.fromisoformat(data["created"]),
            owner=data["owner"],
            calendar_path=data["calendar_path"],
            event_uid=data["event_uid"],
        )


class AttachmentManager:
    """
    Manages RFC 8607 managed attachments.

    Handles storage, retrieval, and URL generation for calendar attachments.
    """

    def __init__(self, configuration: "config.Configuration") -> None:
        self.configuration = configuration
        self._enabled = configuration.get("attachments", "enabled")
        self._storage_path = Path(configuration.get("attachments", "filesystem_folder"))
        self._max_size = configuration.get("attachments", "max_size")
        self._max_per_resource = configuration.get("attachments", "max_per_resource")
        self._base_url = configuration.get("attachments", "base_url")

    @property
    def enabled(self) -> bool:
        """Check if managed attachments are enabled."""
        return self._enabled

    @property
    def max_size(self) -> int:
        """Maximum attachment size in bytes."""
        return self._max_size

    @property
    def max_per_resource(self) -> int:
        """Maximum attachments per calendar object."""
        return self._max_per_resource

    def generate_managed_id(self) -> str:
        """Generate a unique MANAGED-ID for a new attachment."""
        return str(uuid.uuid4())

    def get_attachment_path(self, owner: str, managed_id: str) -> Path:
        """
        Get filesystem path for an attachment.

        Args:
            owner: Username who owns the attachment
            managed_id: Unique attachment identifier

        Returns:
            Path to attachment file
        """
        # Sanitize to prevent path traversal
        safe_owner = self._sanitize_path_component(owner)
        safe_id = self._sanitize_path_component(managed_id)
        return self._storage_path / safe_owner / safe_id

    def get_metadata_path(self, owner: str, managed_id: str) -> Path:
        """Get filesystem path for attachment metadata."""
        safe_owner = self._sanitize_path_component(owner)
        safe_id = self._sanitize_path_component(managed_id)
        return self._storage_path / safe_owner / ".metadata" / f"{safe_id}.json"

    def get_attachment_url(self, base_prefix: str, owner: str,
                           managed_id: str) -> str:
        """
        Get the URL for retrieving an attachment.

        Args:
            base_prefix: Server base URL prefix
            owner: Username who owns the attachment
            managed_id: Unique attachment identifier

        Returns:
            Full URL for attachment retrieval
        """
        if self._base_url:
            # Use configured base URL
            base = self._base_url.rstrip("/")
        else:
            # Auto-generate from base_prefix
            base = base_prefix.rstrip("/") if base_prefix else ""

        return f"{base}{ATTACHMENTS_PATH}/{owner}/{managed_id}"

    def _sanitize_path_component(self, component: str) -> str:
        """
        Sanitize a path component to prevent directory traversal.

        Args:
            component: Path component to sanitize

        Returns:
            Sanitized component safe for filesystem use
        """
        # Remove any path separators and parent directory references
        sanitized = component.replace("/", "").replace("\\", "")
        sanitized = sanitized.replace("..", "")

        # Only allow alphanumeric, dash, underscore
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_@.")
        sanitized = "".join(c for c in sanitized if c in allowed)

        if not sanitized:
            raise ValueError(f"Invalid path component: {component}")

        return sanitized

    def ensure_storage_directory(self, owner: str) -> None:
        """Ensure storage directories exist for owner."""
        user_dir = self._storage_path / self._sanitize_path_component(owner)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / ".metadata").mkdir(exist_ok=True)


# Helper functions for manipulating ATTACH properties in vobject items

def get_managed_attachments(vobject_item) -> List[Dict]:
    """
    Get all managed attachments from a calendar object.

    Args:
        vobject_item: vobject calendar item

    Returns:
        List of dicts with managed_id, url, filename, size, fmttype
    """
    attachments = []

    # Get the component (VEVENT or VTODO)
    component = None
    if hasattr(vobject_item, 'vevent'):
        component = vobject_item.vevent
    elif hasattr(vobject_item, 'vtodo'):
        component = vobject_item.vtodo

    if not component:
        return attachments

    # Check for attach properties
    attach_list = component.contents.get('attach', [])
    for attach in attach_list:
        params = attach.params if hasattr(attach, 'params') else {}
        managed_id = params.get(MANAGED_ID_PARAM, [None])[0]

        if managed_id:
            attachments.append({
                "managed_id": managed_id,
                "url": str(attach.value) if attach.value else "",
                "filename": params.get(FILENAME_PARAM, [""])[0],
                "size": params.get(SIZE_PARAM, ["0"])[0],
                "fmttype": params.get(FMTTYPE_PARAM, ["application/octet-stream"])[0],
            })

    return attachments


def add_managed_attach(vobject_item, managed_id: str, url: str,
                       filename: str, size: int, fmttype: str) -> None:
    """
    Add a managed ATTACH property to a calendar object.

    Args:
        vobject_item: vobject calendar item
        managed_id: Server-generated unique ID
        url: URL for attachment retrieval
        filename: Original filename
        size: File size in bytes
        fmttype: MIME content type
    """
    # Get the component (VEVENT or VTODO)
    component = None
    if hasattr(vobject_item, 'vevent'):
        component = vobject_item.vevent
    elif hasattr(vobject_item, 'vtodo'):
        component = vobject_item.vtodo

    if not component:
        logger.warning("Cannot add attachment: no VEVENT or VTODO component")
        return

    # Add new ATTACH property
    attach = component.add('attach')
    attach.value = url

    # Set RFC 8607 parameters
    attach.params[MANAGED_ID_PARAM] = [managed_id]
    attach.params[FILENAME_PARAM] = [filename]
    attach.params[SIZE_PARAM] = [str(size)]
    attach.params[FMTTYPE_PARAM] = [fmttype]

    logger.debug("Added managed attachment %s to item", managed_id)


def update_managed_attach(vobject_item, managed_id: str, url: str,
                          filename: str, size: int, fmttype: str) -> bool:
    """
    Update an existing managed ATTACH property.

    Args:
        vobject_item: vobject calendar item
        managed_id: ID of attachment to update
        url: New URL for attachment
        filename: New filename
        size: New file size
        fmttype: New MIME type

    Returns:
        True if attachment was found and updated, False otherwise
    """
    component = None
    if hasattr(vobject_item, 'vevent'):
        component = vobject_item.vevent
    elif hasattr(vobject_item, 'vtodo'):
        component = vobject_item.vtodo

    if not component:
        return False

    attach_list = component.contents.get('attach', [])
    for attach in attach_list:
        params = attach.params if hasattr(attach, 'params') else {}
        if params.get(MANAGED_ID_PARAM, [None])[0] == managed_id:
            # Update the attachment
            attach.value = url
            attach.params[FILENAME_PARAM] = [filename]
            attach.params[SIZE_PARAM] = [str(size)]
            attach.params[FMTTYPE_PARAM] = [fmttype]
            logger.debug("Updated managed attachment %s", managed_id)
            return True

    return False


def remove_managed_attach(vobject_item, managed_id: str) -> bool:
    """
    Remove a managed ATTACH property from a calendar object.

    Args:
        vobject_item: vobject calendar item
        managed_id: ID of attachment to remove

    Returns:
        True if attachment was found and removed, False otherwise
    """
    component = None
    if hasattr(vobject_item, 'vevent'):
        component = vobject_item.vevent
    elif hasattr(vobject_item, 'vtodo'):
        component = vobject_item.vtodo

    if not component:
        return False

    attach_list = component.contents.get('attach', [])
    for attach in list(attach_list):
        params = attach.params if hasattr(attach, 'params') else {}
        if params.get(MANAGED_ID_PARAM, [None])[0] == managed_id:
            component.contents['attach'].remove(attach)
            # Clean up empty list
            if not component.contents['attach']:
                del component.contents['attach']
            logger.debug("Removed managed attachment %s", managed_id)
            return True

    return False


def count_managed_attachments(vobject_item) -> int:
    """Count the number of managed attachments on a calendar object."""
    return len(get_managed_attachments(vobject_item))

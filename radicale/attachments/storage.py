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
Attachment storage backend for RFC 8607 managed attachments.

Handles file I/O operations for storing and retrieving calendar attachments.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from radicale.attachments import (
    AttachmentManager,
    AttachmentMetadata,
    AttachmentNotFoundError,
    AttachmentSizeError,
)
from radicale.log import logger

if TYPE_CHECKING:
    from radicale import config


class AttachmentStorage:
    """
    File-based storage for managed attachments.

    Stores attachment data and metadata in a configurable directory structure:
    - {storage_path}/{owner}/{managed_id} - Binary attachment data
    - {storage_path}/{owner}/.metadata/{managed_id}.json - JSON metadata
    """

    def __init__(self, configuration: "config.Configuration") -> None:
        self.configuration = configuration
        self.manager = AttachmentManager(configuration)

    def store(self, owner: str, managed_id: str, data: bytes,
              filename: str, content_type: str, calendar_path: str,
              event_uid: str) -> AttachmentMetadata:
        """
        Store an attachment and its metadata.

        Args:
            owner: Username who owns the attachment
            managed_id: Unique attachment identifier
            data: Binary attachment data
            filename: Original filename
            content_type: MIME type
            calendar_path: Path to the calendar containing this attachment
            event_uid: UID of the calendar object

        Returns:
            AttachmentMetadata for the stored attachment

        Raises:
            AttachmentSizeError: If data exceeds max_size
        """
        # Validate size
        if len(data) > self.manager.max_size:
            raise AttachmentSizeError(
                f"Attachment size {len(data)} exceeds limit {self.manager.max_size}"
            )

        # Ensure directory exists
        self.manager.ensure_storage_directory(owner)

        # Get paths
        data_path = self.manager.get_attachment_path(owner, managed_id)
        meta_path = self.manager.get_metadata_path(owner, managed_id)

        # Create metadata
        metadata = AttachmentMetadata(
            managed_id=managed_id,
            filename=filename,
            content_type=content_type,
            size=len(data),
            created=datetime.now(timezone.utc),
            owner=owner,
            calendar_path=calendar_path,
            event_uid=event_uid,
        )

        # Write atomically using temp file + rename
        try:
            # Write data file
            self._atomic_write(data_path, data, binary=True)

            # Write metadata file
            meta_json = json.dumps(metadata.to_dict(), indent=2)
            self._atomic_write(meta_path, meta_json.encode("utf-8"), binary=True)

            logger.info("Stored attachment %s for %s (size=%d, type=%s)",
                       managed_id, owner, len(data), content_type)

            return metadata

        except Exception as e:
            # Clean up on failure
            self._safe_delete(data_path)
            self._safe_delete(meta_path)
            logger.error("Failed to store attachment %s: %s", managed_id, e)
            raise

    def retrieve(self, owner: str, managed_id: str) -> Tuple[bytes, AttachmentMetadata]:
        """
        Retrieve an attachment and its metadata.

        Args:
            owner: Username who owns the attachment
            managed_id: Unique attachment identifier

        Returns:
            Tuple of (binary data, metadata)

        Raises:
            AttachmentNotFoundError: If attachment doesn't exist
        """
        data_path = self.manager.get_attachment_path(owner, managed_id)
        meta_path = self.manager.get_metadata_path(owner, managed_id)

        if not data_path.exists():
            raise AttachmentNotFoundError(
                f"Attachment {managed_id} not found for {owner}"
            )

        try:
            # Read data
            with open(data_path, "rb") as f:
                data = f.read()

            # Read metadata
            metadata = self.get_metadata(owner, managed_id)
            if metadata is None:
                # Create minimal metadata if file exists but metadata missing
                metadata = AttachmentMetadata(
                    managed_id=managed_id,
                    filename="unknown",
                    content_type="application/octet-stream",
                    size=len(data),
                    created=datetime.now(timezone.utc),
                    owner=owner,
                    calendar_path="",
                    event_uid="",
                )

            return data, metadata

        except Exception as e:
            logger.error("Failed to retrieve attachment %s: %s", managed_id, e)
            raise AttachmentNotFoundError(
                f"Failed to retrieve attachment {managed_id}: {e}"
            )

    def get_metadata(self, owner: str, managed_id: str) -> Optional[AttachmentMetadata]:
        """
        Get attachment metadata without loading the data.

        Args:
            owner: Username who owns the attachment
            managed_id: Unique attachment identifier

        Returns:
            AttachmentMetadata or None if not found
        """
        meta_path = self.manager.get_metadata_path(owner, managed_id)

        if not meta_path.exists():
            return None

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_dict = json.load(f)
            return AttachmentMetadata.from_dict(meta_dict)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Invalid metadata for attachment %s: %s", managed_id, e)
            return None

    def delete(self, owner: str, managed_id: str) -> bool:
        """
        Delete an attachment and its metadata.

        Args:
            owner: Username who owns the attachment
            managed_id: Unique attachment identifier

        Returns:
            True if deleted, False if not found
        """
        data_path = self.manager.get_attachment_path(owner, managed_id)
        meta_path = self.manager.get_metadata_path(owner, managed_id)

        deleted = False

        if data_path.exists():
            self._safe_delete(data_path)
            deleted = True

        if meta_path.exists():
            self._safe_delete(meta_path)
            deleted = True

        if deleted:
            logger.info("Deleted attachment %s for %s", managed_id, owner)

        return deleted

    def exists(self, owner: str, managed_id: str) -> bool:
        """Check if an attachment exists."""
        data_path = self.manager.get_attachment_path(owner, managed_id)
        return data_path.exists()

    def list_attachments(self, owner: str) -> list:
        """
        List all attachments for a user.

        Args:
            owner: Username

        Returns:
            List of managed_ids
        """
        try:
            safe_owner = self.manager._sanitize_path_component(owner)
            user_dir = self.manager._storage_path / safe_owner

            if not user_dir.exists():
                return []

            # List files (not directories)
            attachments = []
            for entry in user_dir.iterdir():
                if entry.is_file() and not entry.name.startswith("."):
                    attachments.append(entry.name)

            return attachments

        except Exception as e:
            logger.error("Failed to list attachments for %s: %s", owner, e)
            return []

    def get_user_storage_size(self, owner: str) -> int:
        """
        Calculate total storage used by a user.

        Args:
            owner: Username

        Returns:
            Total bytes used
        """
        total = 0
        try:
            safe_owner = self.manager._sanitize_path_component(owner)
            user_dir = self.manager._storage_path / safe_owner

            if user_dir.exists():
                for entry in user_dir.iterdir():
                    if entry.is_file():
                        total += entry.stat().st_size

        except Exception as e:
            logger.error("Failed to calculate storage for %s: %s", owner, e)

        return total

    def _atomic_write(self, path: Path, data: bytes, binary: bool = True) -> None:
        """
        Write data atomically using temp file + rename.

        Args:
            path: Destination path
            data: Data to write
            binary: Whether to write in binary mode
        """
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file in same directory (for atomic rename)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent)
        try:
            os.write(fd, data)
            os.close(fd)

            # Atomic rename
            os.replace(tmp_path, path)

        except Exception:
            # Clean up temp file on error
            os.close(fd) if fd else None
            self._safe_delete(Path(tmp_path))
            raise

    def _safe_delete(self, path: Path) -> None:
        """Safely delete a file, ignoring errors."""
        try:
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.warning("Failed to delete %s: %s", path, e)

    def get_attachment_calendars(self, owner: str, managed_id: str) -> List[str]:
        """
        Get calendar paths that contain/reference this attachment.

        For shared access control, we need to know which calendars contain
        an attachment so we can check if the requesting user has shared
        access to any of them.

        Args:
            owner: Username who owns the attachment
            managed_id: Unique attachment identifier

        Returns:
            List of calendar collection paths that reference this attachment.
            Returns the primary calendar_path from metadata. If metadata is
            missing or corrupted, returns empty list.
        """
        metadata = self.get_metadata(owner, managed_id)
        if metadata and metadata.calendar_path:
            return [metadata.calendar_path]
        return []

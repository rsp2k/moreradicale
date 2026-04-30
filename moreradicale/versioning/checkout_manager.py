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
Checkout state manager for RFC 3253 versioning.

Tracks which resources are checked out and by whom using
file-based markers. Supports checkout timeout expiration.

Checkout marker files are stored alongside resources:
  event.ics           -> The actual resource
  .event.ics.checkout -> JSON checkout marker
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CheckoutInfo:
    """Information about a checkout."""
    user: str  # User who performed checkout
    timestamp: str  # ISO 8601 checkout timestamp
    version: str  # Version SHA that was checked out
    checkout_type: str  # "in-place" or "fork"

    def is_expired(self, timeout_seconds: int) -> bool:
        """Check if this checkout has expired."""
        if timeout_seconds <= 0:
            return False  # No timeout configured

        try:
            checkout_time = datetime.fromisoformat(self.timestamp)
            now = datetime.now(timezone.utc)
            elapsed = (now - checkout_time).total_seconds()
            return elapsed > timeout_seconds
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CheckoutInfo":
        """Create from dictionary."""
        return cls(
            user=data.get("user", ""),
            timestamp=data.get("timestamp", ""),
            version=data.get("version", ""),
            checkout_type=data.get("checkout_type", "in-place")
        )


class CheckoutManager:
    """
    Manage checkout state for RFC 3253 versioning.

    Uses file-based markers to track checkouts across server restarts.
    The checkout marker is a JSON file stored alongside the resource.

    Fork control policies (per RFC 3253):
    - "forbidden": Only one checkout allowed at a time (default)
    - "discouraged": Warn but allow multiple checkouts
    - "ok": Allow multiple concurrent checkouts
    """

    CHECKOUT_SUFFIX = ".checkout"

    def __init__(self, storage_folder: str, checkout_fork: str = "forbidden",
                 checkout_timeout: int = 3600):
        """
        Initialize checkout manager.

        Args:
            storage_folder: Path to the storage folder
            checkout_fork: Fork policy ("forbidden", "discouraged", "ok")
            checkout_timeout: Checkout timeout in seconds (0=never)
        """
        self.storage_folder = storage_folder
        self.checkout_fork = checkout_fork
        self.checkout_timeout = checkout_timeout

    def _marker_path(self, resource_path: str) -> str:
        """Get the checkout marker file path for a resource."""
        # Resource: /path/to/event.ics
        # Marker:   /path/to/.event.ics.checkout
        dirname = os.path.dirname(resource_path)
        basename = os.path.basename(resource_path)
        marker_name = f".{basename}{self.CHECKOUT_SUFFIX}"
        return os.path.join(dirname, marker_name)

    def _full_path(self, relative_path: str) -> str:
        """Convert relative path to full path."""
        return os.path.join(self.storage_folder, relative_path)

    def is_checked_out(self, relative_path: str) -> bool:
        """
        Check if a resource is currently checked out.

        Args:
            relative_path: Path relative to storage folder

        Returns:
            True if resource is checked out (and not expired)
        """
        info = self.get_checkout_info(relative_path)
        if info is None:
            return False

        # Check for expiration
        if info.is_expired(self.checkout_timeout):
            logger.info("Checkout expired for %s, clearing", relative_path)
            self.clear_checkout(relative_path)
            return False

        return True

    def get_checkout_info(self, relative_path: str) -> Optional[CheckoutInfo]:
        """
        Get checkout information for a resource.

        Args:
            relative_path: Path relative to storage folder

        Returns:
            CheckoutInfo if checked out, None otherwise
        """
        marker_path = self._full_path(
            self._marker_path(relative_path)
        )

        if not os.path.exists(marker_path):
            return None

        try:
            with open(marker_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return CheckoutInfo.from_dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read checkout marker %s: %s",
                           marker_path, e)
            return None

    def checkout(self, relative_path: str, user: str,
                 version: str, checkout_type: str = "in-place"
                 ) -> tuple[bool, Optional[str]]:
        """
        Check out a resource for editing.

        Args:
            relative_path: Path relative to storage folder
            user: User performing checkout
            version: Version SHA being checked out
            checkout_type: "in-place" or "fork"

        Returns:
            Tuple of (success, error_message)
        """
        # Check fork policy
        existing = self.get_checkout_info(relative_path)
        if existing:
            if existing.is_expired(self.checkout_timeout):
                # Expired - clear and allow new checkout
                self.clear_checkout(relative_path)
            elif self.checkout_fork == "forbidden":
                return False, f"Resource already checked out by {existing.user}"
            elif self.checkout_fork == "discouraged":
                logger.warning(
                    "Multiple checkout for %s: %s checking out resource "
                    "already checked out by %s",
                    relative_path, user, existing.user
                )
                # Continue with checkout

        # Create checkout info
        info = CheckoutInfo(
            user=user,
            timestamp=datetime.now(timezone.utc).isoformat(),
            version=version,
            checkout_type=checkout_type
        )

        # Write marker file
        marker_path = self._full_path(
            self._marker_path(relative_path)
        )

        try:
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, "w", encoding="utf-8") as f:
                json.dump(info.to_dict(), f, indent=2)

            logger.info("Checked out %s by %s (version %s)",
                        relative_path, user, version[:8])
            return True, None

        except OSError as e:
            logger.warning("Failed to create checkout marker: %s", e)
            return False, str(e)

    def checkin(self, relative_path: str, user: str
                ) -> tuple[bool, Optional[str]]:
        """
        Check in a resource (called after creating new version).

        Verifies the checkout belongs to this user, then clears it.

        Args:
            relative_path: Path relative to storage folder
            user: User performing checkin

        Returns:
            Tuple of (success, error_message)
        """
        info = self.get_checkout_info(relative_path)

        if info is None:
            return False, "Resource is not checked out"

        if info.user != user:
            return False, f"Resource checked out by {info.user}, not {user}"

        # Clear the checkout
        return self.clear_checkout(relative_path), None

    def uncheckout(self, relative_path: str, user: str
                   ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Cancel a checkout without creating a new version.

        Args:
            relative_path: Path relative to storage folder
            user: User performing uncheckout

        Returns:
            Tuple of (success, error_message, version_to_restore)
        """
        info = self.get_checkout_info(relative_path)

        if info is None:
            return False, "Resource is not checked out", None

        if info.user != user:
            return False, f"Resource checked out by {info.user}, not {user}", None

        version = info.version
        success = self.clear_checkout(relative_path)

        if success:
            logger.info("Uncheckout %s by %s (restoring to %s)",
                        relative_path, user, version[:8])
            return True, None, version

        return False, "Failed to clear checkout", None

    def clear_checkout(self, relative_path: str) -> bool:
        """
        Clear checkout marker for a resource.

        Args:
            relative_path: Path relative to storage folder

        Returns:
            True on success
        """
        marker_path = self._full_path(
            self._marker_path(relative_path)
        )

        if not os.path.exists(marker_path):
            return True  # Already cleared

        try:
            os.remove(marker_path)
            logger.debug("Cleared checkout marker for %s", relative_path)
            return True
        except OSError as e:
            logger.warning("Failed to clear checkout marker: %s", e)
            return False

    def get_checked_out_by_user(self, user: str,
                                collection_path: str) -> list[str]:
        """
        Get all resources checked out by a user in a collection.

        Args:
            user: User to check
            collection_path: Collection path relative to storage

        Returns:
            List of relative paths checked out by this user
        """
        full_collection = self._full_path(collection_path)
        if not os.path.isdir(full_collection):
            return []

        checked_out = []
        for root, _, files in os.walk(full_collection):
            for filename in files:
                if filename.endswith(self.CHECKOUT_SUFFIX):
                    marker_path = os.path.join(root, filename)
                    os.path.relpath(marker_path, self.storage_folder)

                    # Derive resource path from marker path
                    # .event.ics.checkout -> event.ics
                    basename = filename[1:-len(self.CHECKOUT_SUFFIX)]
                    resource_path = os.path.join(
                        os.path.relpath(root, self.storage_folder),
                        basename
                    )

                    info = self.get_checkout_info(resource_path)
                    if info and info.user == user:
                        if not info.is_expired(self.checkout_timeout):
                            checked_out.append(resource_path)

        return checked_out

    def cleanup_expired(self, collection_path: Optional[str] = None) -> int:
        """
        Clean up expired checkout markers.

        Args:
            collection_path: Optional path to limit cleanup scope

        Returns:
            Number of expired checkouts cleaned up
        """
        if self.checkout_timeout <= 0:
            return 0  # No timeout configured

        search_path = self._full_path(collection_path or "collection-root")
        if not os.path.isdir(search_path):
            return 0

        cleaned = 0
        for root, _, files in os.walk(search_path):
            for filename in files:
                if filename.endswith(self.CHECKOUT_SUFFIX):
                    marker_path = os.path.join(root, filename)
                    os.path.relpath(marker_path, self.storage_folder)

                    # Derive resource path
                    basename = filename[1:-len(self.CHECKOUT_SUFFIX)]
                    resource_path = os.path.join(
                        os.path.relpath(root, self.storage_folder),
                        basename
                    )

                    info = self.get_checkout_info(resource_path)
                    if info and info.is_expired(self.checkout_timeout):
                        if self.clear_checkout(resource_path):
                            cleaned += 1

        if cleaned:
            logger.info("Cleaned up %d expired checkouts", cleaned)

        return cleaned

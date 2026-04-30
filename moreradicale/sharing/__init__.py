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
Calendar sharing and delegation module.

This module implements:
- Per-calendar sharing (Alice shares calendar with Bob)
- Scheduling delegation (Secretary can send invites for Boss)

Sharing relationships are stored in collection metadata (.Radicale.props)
using the RADICALE:shares property.

Delegation relationships are stored in principal metadata using
RADICALE:schedule-delegates and RADICALE:calendar-proxy-* properties.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional

from moreradicale.log import logger

if TYPE_CHECKING:
    from moreradicale import config, storage


class ShareAccess(Enum):
    """Access levels for shared calendars."""
    READ = "read"
    READ_WRITE = "read-write"


class InviteStatus(Enum):
    """Status of a share invitation."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"


@dataclass
class Share:
    """Represents a sharing relationship for a calendar."""
    sharee: str  # Username of person calendar is shared with
    access: ShareAccess  # Access level
    cn: Optional[str] = None  # Display name of sharee
    status: InviteStatus = InviteStatus.PENDING
    invited_at: Optional[str] = None  # ISO timestamp
    accepted_at: Optional[str] = None  # ISO timestamp
    comment: Optional[str] = None  # Optional message from sharer

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON storage."""
        return {
            "access": self.access.value,
            "cn": self.cn,
            "status": self.status.value,
            "invited_at": self.invited_at,
            "accepted_at": self.accepted_at,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, sharee: str, data: dict) -> "Share":
        """Create Share from stored dictionary."""
        return cls(
            sharee=sharee,
            access=ShareAccess(data.get("access", "read")),
            cn=data.get("cn"),
            status=InviteStatus(data.get("status", "pending")),
            invited_at=data.get("invited_at"),
            accepted_at=data.get("accepted_at"),
            comment=data.get("comment"),
        )


@dataclass
class Delegation:
    """Represents a delegation relationship."""
    delegate: str  # Username who can act on behalf of principal
    can_read: bool = True  # Can read principal's calendars
    can_write: bool = False  # Can write to principal's calendars
    can_schedule: bool = False  # Can send invitations as principal

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON storage."""
        return {
            "can_read": self.can_read,
            "can_write": self.can_write,
            "can_schedule": self.can_schedule,
        }

    @classmethod
    def from_dict(cls, delegate: str, data: dict) -> "Delegation":
        """Create Delegation from stored dictionary."""
        return cls(
            delegate=delegate,
            can_read=data.get("can_read", True),
            can_write=data.get("can_write", False),
            can_schedule=data.get("can_schedule", False),
        )


# Property names used in .Radicale.props
SHARES_PROPERTY = "RADICALE:shares"
PROXY_READ_PROPERTY = "RADICALE:calendar-proxy-read"
PROXY_WRITE_PROPERTY = "RADICALE:calendar-proxy-write"
SCHEDULE_DELEGATES_PROPERTY = "RADICALE:schedule-delegates"


class SharingManager:
    """
    Manages sharing and delegation operations.

    This class provides methods to:
    - Add/remove/query calendar shares
    - Add/remove/query scheduling delegates
    - Check access permissions for shared calendars
    """

    def __init__(self, configuration: "config.Configuration") -> None:
        """Initialize SharingManager with configuration."""
        self.configuration = configuration

    def is_sharing_enabled(self) -> bool:
        """Check if sharing is enabled in configuration."""
        return self.configuration.get("sharing", "enabled")

    def is_delegation_enabled(self) -> bool:
        """Check if delegation is enabled in configuration."""
        return self.configuration.get("sharing", "delegation_enabled")

    # =========================================================================
    # Calendar Sharing Methods
    # =========================================================================

    def get_shares(self, collection: "storage.BaseCollection") -> Dict[str, Share]:
        """
        Get all shares for a collection.

        Args:
            collection: The calendar collection

        Returns:
            Dictionary mapping sharee usernames to Share objects
        """
        shares_json = collection.get_meta(SHARES_PROPERTY)
        if not shares_json:
            return {}

        try:
            shares_data = json.loads(shares_json)
            return {
                sharee: Share.from_dict(sharee, data)
                for sharee, data in shares_data.items()
            }
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to parse shares for %s: %s",
                           collection.path, e)
            return {}

    def add_share(self, collection: "storage.BaseCollection",
                  owner: str, sharee: str, access: ShareAccess,
                  cn: Optional[str] = None,
                  comment: Optional[str] = None) -> bool:
        """
        Add or update a share for a collection.

        Args:
            collection: The calendar collection to share
            owner: Username of calendar owner (for validation)
            sharee: Username to share with
            access: Access level (read or read-write)
            cn: Display name of sharee (optional)
            comment: Message from sharer (optional, e.g. "Here's my work calendar")

        Returns:
            True if share was added/updated successfully

        Raises:
            PermissionError: If user is not the collection owner
            ValueError: If sharee is invalid
        """
        # Security: Only owner can share
        if collection.owner != owner:
            raise PermissionError(
                f"Only owner '{collection.owner}' can share this calendar")

        # Cannot share with self
        if sharee == owner:
            raise ValueError("Cannot share calendar with yourself")

        # Get existing shares
        shares = self.get_shares(collection)

        # Create or update share
        now = datetime.now(timezone.utc).isoformat()
        if sharee in shares:
            # Update existing share
            shares[sharee].access = access
            if cn:
                shares[sharee].cn = cn
            if comment:
                shares[sharee].comment = comment
            logger.info("Updated share: %s shared %s with %s (access=%s)",
                        owner, collection.path, sharee, access.value)
        else:
            # New share
            shares[sharee] = Share(
                sharee=sharee,
                access=access,
                cn=cn,
                status=InviteStatus.PENDING,
                invited_at=now,
                comment=comment,
            )
            logger.info("Added share: %s shared %s with %s (access=%s)",
                        owner, collection.path, sharee, access.value)

        # Save to collection metadata
        self._save_shares(collection, shares)
        return True

    def remove_share(self, collection: "storage.BaseCollection",
                     owner: str, sharee: str) -> bool:
        """
        Remove a share from a collection.

        Args:
            collection: The calendar collection
            owner: Username of calendar owner (for validation)
            sharee: Username to unshare from

        Returns:
            True if share was removed, False if it didn't exist

        Raises:
            PermissionError: If user is not the collection owner
        """
        # Security: Only owner can remove shares
        if collection.owner != owner:
            raise PermissionError(
                f"Only owner '{collection.owner}' can modify shares")

        shares = self.get_shares(collection)
        if sharee not in shares:
            return False

        del shares[sharee]
        self._save_shares(collection, shares)

        logger.info("Removed share: %s unshared %s from %s",
                    owner, collection.path, sharee)
        return True

    def accept_share(self, collection: "storage.BaseCollection",
                     sharee: str) -> bool:
        """
        Accept a share invitation.

        Args:
            collection: The shared calendar
            sharee: Username accepting the share

        Returns:
            True if invitation was accepted

        Raises:
            ValueError: If no pending invitation exists
        """
        shares = self.get_shares(collection)
        if sharee not in shares:
            raise ValueError(f"No share invitation for {sharee}")

        share = shares[sharee]
        if share.status != InviteStatus.PENDING:
            raise ValueError(f"Share invitation already {share.status.value}")

        share.status = InviteStatus.ACCEPTED
        share.accepted_at = datetime.now(timezone.utc).isoformat()
        self._save_shares(collection, shares)

        logger.info("Share accepted: %s accepted share of %s",
                    sharee, collection.path)
        return True

    def decline_share(self, collection: "storage.BaseCollection",
                      sharee: str) -> bool:
        """
        Decline a share invitation.

        Args:
            collection: The shared calendar
            sharee: Username declining the share

        Returns:
            True if invitation was declined
        """
        shares = self.get_shares(collection)
        if sharee not in shares:
            return False

        # Remove the share entirely when declined
        del shares[sharee]
        self._save_shares(collection, shares)

        logger.info("Share declined: %s declined share of %s",
                    sharee, collection.path)
        return True

    def check_share_access(self, username: str,
                           collection: "storage.BaseCollection"
                           ) -> Optional[ShareAccess]:
        """
        Check if user has shared access to a collection.

        Args:
            username: Username to check
            collection: The calendar collection

        Returns:
            ShareAccess if user has access, None otherwise
        """
        shares = self.get_shares(collection)
        share = shares.get(username)

        if share and share.status == InviteStatus.ACCEPTED:
            return share.access
        return None

    def get_calendars_shared_with(self, username: str,
                                  storage: "storage.BaseStorage"
                                  ) -> List[str]:
        """
        Get all calendar paths shared with a user.

        Args:
            username: Username to find shares for
            storage: Storage instance to scan

        Returns:
            List of collection paths shared with user
        """
        shared_paths = []

        # Scan all collections for shares with this user
        # This is a full scan - consider caching for performance
        try:
            for item in storage.discover("", depth="infinity"):
                if hasattr(item, 'get_meta'):
                    shares = self.get_shares(item)
                    share = shares.get(username)
                    if share and share.status == InviteStatus.ACCEPTED:
                        shared_paths.append(item.path)
        except Exception as e:
            logger.warning("Error scanning for shared calendars: %s", e)

        return shared_paths

    def _save_shares(self, collection: "storage.BaseCollection",
                     shares: Dict[str, Share]) -> None:
        """Save shares to collection metadata."""
        shares_data = {
            sharee: share.to_dict()
            for sharee, share in shares.items()
        }
        # Get all current metadata and update shares
        meta = dict(collection.get_meta() or {})
        if shares_data:
            meta[SHARES_PROPERTY] = json.dumps(shares_data)
        elif SHARES_PROPERTY in meta:
            del meta[SHARES_PROPERTY]
        collection.set_meta(meta)

    # =========================================================================
    # Delegation Methods
    # =========================================================================

    def get_delegates(self, principal: "storage.BaseCollection"
                      ) -> List[str]:
        """
        Get users who can schedule on behalf of this principal.

        Args:
            principal: The principal collection

        Returns:
            List of delegate usernames
        """
        delegates_json = principal.get_meta(SCHEDULE_DELEGATES_PROPERTY)
        if not delegates_json:
            return []

        try:
            return json.loads(delegates_json)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse delegates for %s: %s",
                           principal.path, e)
            return []

    def add_delegate(self, principal: "storage.BaseCollection",
                     owner: str, delegate: str) -> bool:
        """
        Add a scheduling delegate for a principal.

        Args:
            principal: The principal collection
            owner: Username of principal (for validation)
            delegate: Username to add as delegate

        Returns:
            True if delegate was added

        Raises:
            PermissionError: If user is not the principal owner
        """
        if principal.owner != owner:
            raise PermissionError(
                f"Only owner '{principal.owner}' can add delegates")

        if delegate == owner:
            raise ValueError("Cannot delegate to yourself")

        delegates = self.get_delegates(principal)
        if delegate in delegates:
            return False  # Already a delegate

        delegates.append(delegate)
        self._save_delegates(principal, delegates)

        logger.info("Delegate added: %s granted scheduling to %s",
                    owner, delegate)
        return True

    def remove_delegate(self, principal: "storage.BaseCollection",
                        owner: str, delegate: str) -> bool:
        """
        Remove a scheduling delegate from a principal.

        Args:
            principal: The principal collection
            owner: Username of principal (for validation)
            delegate: Username to remove as delegate

        Returns:
            True if delegate was removed
        """
        if principal.owner != owner:
            raise PermissionError(
                f"Only owner '{principal.owner}' can remove delegates")

        delegates = self.get_delegates(principal)
        if delegate not in delegates:
            return False

        delegates.remove(delegate)
        self._save_delegates(principal, delegates)

        logger.info("Delegate removed: %s revoked scheduling from %s",
                    owner, delegate)
        return True

    def is_delegate_for(self, username: str,
                        principal: "storage.BaseCollection") -> bool:
        """
        Check if user is a scheduling delegate for principal.

        Args:
            username: Username to check
            principal: The principal collection

        Returns:
            True if user can schedule on behalf of principal
        """
        return username in self.get_delegates(principal)

    def get_proxy_read_for(self, principal: "storage.BaseCollection"
                           ) -> List[str]:
        """Get principals this user can proxy-read."""
        proxy_json = principal.get_meta(PROXY_READ_PROPERTY)
        if not proxy_json:
            return []
        try:
            return json.loads(proxy_json)
        except json.JSONDecodeError:
            return []

    def get_proxy_write_for(self, principal: "storage.BaseCollection"
                            ) -> List[str]:
        """Get principals this user can proxy-write."""
        proxy_json = principal.get_meta(PROXY_WRITE_PROPERTY)
        if not proxy_json:
            return []
        try:
            return json.loads(proxy_json)
        except json.JSONDecodeError:
            return []

    def _save_delegates(self, principal: "storage.BaseCollection",
                        delegates: List[str]) -> None:
        """Save delegates to principal metadata."""
        meta = dict(principal.get_meta() or {})
        if delegates:
            meta[SCHEDULE_DELEGATES_PROPERTY] = json.dumps(delegates)
        elif SCHEDULE_DELEGATES_PROPERTY in meta:
            del meta[SCHEDULE_DELEGATES_PROPERTY]
        principal.set_meta(meta)


def get_sharing_manager(configuration: "config.Configuration"
                        ) -> SharingManager:
    """Get or create SharingManager instance."""
    return SharingManager(configuration)

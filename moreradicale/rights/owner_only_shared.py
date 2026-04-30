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
Rights backend that allows owner access plus shared calendar access.

This extends owner_only to also grant access when:
1. A calendar has been shared with the user (via RADICALE:shares property)
2. A user is a delegate for another principal (via RADICALE:schedule-delegates)

Configuration:
    [rights]
    type = owner_only_shared

    [sharing]
    enabled = True
    delegation_enabled = True

Sharing is controlled by the RADICALE:shares property on calendar collections.
Delegation is controlled by RADICALE:schedule-delegates on principal collections.
"""

import json
from typing import TYPE_CHECKING

from moreradicale import pathutils
from moreradicale.log import logger
from moreradicale.rights import owner_only
from moreradicale.sharing import SHARES_PROPERTY, PROXY_READ_PROPERTY, PROXY_WRITE_PROPERTY

if TYPE_CHECKING:
    from moreradicale import config


class Rights(owner_only.Rights):
    """
    Rights backend with calendar sharing support.

    Extends owner_only to check for:
    1. Direct ownership (from parent class)
    2. Shared calendar access (read or read-write)
    3. Proxy access to another principal's calendars
    """

    def __init__(self, configuration: "config.Configuration") -> None:
        super().__init__(configuration)
        self._sharing_enabled = configuration.get("sharing", "enabled")
        self._delegation_enabled = configuration.get("sharing", "delegation_enabled")
        # Cache for collection metadata to avoid repeated lookups
        self._meta_cache: dict = {}

    def authorization(self, user: str, path: str) -> str:
        """
        Get granted rights for user on path.

        First checks owner_only permissions, then sharing permissions.

        Args:
            user: Authenticated username (empty for anonymous)
            path: Sanitized collection path

        Returns:
            Permission string (e.g., "rw", "r", "RW", or "")
        """
        # First check standard owner_only permissions
        base_perms = super().authorization(user, path)
        if base_perms:
            return base_perms

        # If no user or sharing disabled, no additional access
        if not user or not self._sharing_enabled:
            return ""

        sane_path = pathutils.strip_path(path)
        if not sane_path:
            return ""  # Root path - no shared access

        # Check for shared access to this path
        shared_access = self._check_shared_access(user, sane_path)
        if shared_access:
            return shared_access

        # Check for proxy access (delegate can access principal's collections)
        if self._delegation_enabled:
            proxy_access = self._check_proxy_access(user, sane_path)
            if proxy_access:
                return proxy_access

        return ""

    def _check_shared_access(self, user: str, sane_path: str) -> str:
        """
        Check if user has shared access to the collection at path.

        Args:
            user: Authenticated username
            sane_path: Sanitized path without leading/trailing slashes

        Returns:
            Permission string ("rw", "r") or empty string
        """
        # Extract collection path (e.g., "alice/calendar" from "alice/calendar/event.ics")
        path_parts = sane_path.split("/")
        if len(path_parts) < 2:
            return ""  # Principal path, not a calendar

        # For item paths (alice/calendar/item.ics), check the parent collection
        if len(path_parts) > 2:
            collection_path = "/".join(path_parts[:2])
        else:
            collection_path = sane_path

        # Get collection metadata
        # Note: We can't directly access storage here, so we rely on
        # the metadata being passed through or cached
        shares_data = self._get_shares_for_path(collection_path)
        if not shares_data:
            return ""

        # Check if user is in shares
        user_share = shares_data.get(user)
        if not user_share:
            return ""

        # Check invitation status
        status = user_share.get("status", "pending")
        if status != "accepted":
            logger.debug("Share for %s on %s not yet accepted (status=%s)",
                        user, collection_path, status)
            return ""

        # Return permissions based on access level
        access = user_share.get("access", "read")
        if access == "read-write":
            logger.debug("Shared read-write access for %s on %s",
                        user, collection_path)
            return "rw"
        else:
            logger.debug("Shared read-only access for %s on %s",
                        user, collection_path)
            return "r"

    def _check_proxy_access(self, user: str, sane_path: str) -> str:
        """
        Check if user has proxy access to another principal's collections.

        Proxy access allows a delegate to access all calendars of their principal.

        Args:
            user: Authenticated username
            sane_path: Sanitized path

        Returns:
            Permission string or empty
        """
        path_parts = sane_path.split("/")
        if not path_parts:
            return ""

        # Get the owner (first path component)
        owner = path_parts[0]
        if owner == user:
            return ""  # Already handled by owner_only

        # Check if user is a proxy for owner
        # This requires reading the owner's principal metadata
        proxy_level = self._get_proxy_level_for(user, owner)

        if proxy_level == "write":
            # Can read and write owner's collections
            if "/" not in sane_path:
                return "RW"  # Principal access
            elif sane_path.count("/") == 1:
                return "rw"  # Collection access
            return ""
        elif proxy_level == "read":
            # Can only read owner's collections
            if "/" not in sane_path:
                return "R"  # Principal access (read only)
            elif sane_path.count("/") == 1:
                return "r"  # Collection access (read only)
            return ""

        return ""

    def _get_shares_for_path(self, collection_path: str) -> dict:
        """
        Get shares data for a collection path.

        This method needs to be called after collection metadata is available.
        In practice, this is populated by the application layer.

        Args:
            collection_path: Path like "alice/calendar"

        Returns:
            Dictionary of shares or empty dict
        """
        # Check cache first
        if collection_path in self._meta_cache:
            return self._meta_cache[collection_path]

        # The shares data will be populated by set_collection_meta()
        return {}

    def _get_proxy_level_for(self, user: str, owner: str) -> str:
        """
        Get the proxy access level user has for owner.

        Args:
            user: The potential proxy
            owner: The principal to check

        Returns:
            "write", "read", or "" (none)
        """
        # Check cache for owner's principal metadata
        owner_meta = self._meta_cache.get(owner, {})

        # Check write proxy first (higher privilege)
        write_proxies = owner_meta.get(PROXY_WRITE_PROPERTY, "[]")
        try:
            if user in json.loads(write_proxies):
                return "write"
        except json.JSONDecodeError:
            pass

        # Check read proxy
        read_proxies = owner_meta.get(PROXY_READ_PROPERTY, "[]")
        try:
            if user in json.loads(read_proxies):
                return "read"
        except json.JSONDecodeError:
            pass

        return ""

    def set_collection_meta(self, path: str, meta: dict) -> None:
        """
        Cache collection metadata for rights checking.

        This method is called by the application layer to provide
        collection metadata for sharing checks.

        Args:
            path: Collection path (can be with or without leading/trailing slashes)
            meta: Collection metadata dictionary
        """
        # Normalize path - remove leading/trailing slashes
        sane_path = path.strip("/")

        # Extract and cache shares data
        shares_json = meta.get(SHARES_PROPERTY)
        if shares_json:
            try:
                self._meta_cache[sane_path] = json.loads(shares_json)
            except json.JSONDecodeError:
                pass

        # Cache principal metadata for proxy checks
        if "/" not in sane_path and sane_path:
            # This is a principal path
            self._meta_cache[sane_path] = meta

    def clear_meta_cache(self) -> None:
        """Clear the metadata cache."""
        self._meta_cache.clear()

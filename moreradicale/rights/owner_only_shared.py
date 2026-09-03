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
import time
from typing import TYPE_CHECKING, Optional

from moreradicale import pathutils
from moreradicale.log import logger
from moreradicale.rights import owner_only
from moreradicale.sharing import (PROXY_READ_PROPERTY, PROXY_WRITE_PROPERTY,
                                  SHARES_PROPERTY)

if TYPE_CHECKING:
    from moreradicale import config, storage


def _json_list_contains(raw: str, needle: str, prop_name: str,
                        owner: str) -> bool:
    """True if `raw` is a JSON array of strings containing `needle`.

    Deliberately strict about shape. The naive `needle in json.loads(raw)`
    is a **substring test** when the stored value happens to be a JSON
    string rather than an array: with `"mallory"` stored, `"mal"`, `"o"`
    and `"y"` all test true and would each be granted proxy rights.
    Anything that is not a list of strings denies and logs.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("rights: malformed %s on %s (not JSON), denying",
                       prop_name, owner)
        return False
    if not isinstance(parsed, list):
        logger.warning("rights: %s on %s is %s, expected a list - denying",
                       prop_name, owner, type(parsed).__name__)
        return False
    return any(entry == needle for entry in parsed if isinstance(entry, str))


class Rights(owner_only.Rights):
    """
    Rights backend with calendar sharing support.

    Extends owner_only to check for:
    1. Direct ownership (from parent class)
    2. Shared calendar access (read or read-write)
    3. Proxy access to another principal's calendars
    """

    # Short TTL on the per-path metadata cache. PROPFIND can call
    # authorization() many times for one request (parent + every child),
    # so a memoize is essential. But share state changes after MKCOL,
    # POST CS:share-resource, and POST CS:share-reply, so we need the
    # cache to expire quickly enough to pick up reactions to those.
    _CACHE_TTL_SECONDS = 5.0

    def __init__(self, configuration: "config.Configuration") -> None:
        super().__init__(configuration)
        self._sharing_enabled = configuration.get("sharing", "enabled")
        self._delegation_enabled = configuration.get("sharing", "delegation_enabled")
        # Storage handle, attached after construction by the application
        # layer. None during early-init paths (e.g. unit tests that don't
        # exercise sharing).
        self._storage: "Optional[storage.BaseStorage]" = None
        # path -> (expiry_unix, shares_dict | proxy_meta_dict)
        # Two distinct caches keyed by path:
        #   - "shares:{collection_path}" -> shares dict from RADICALE:shares
        #   - "proxy:{owner}"            -> {RADICALE:proxy-read, RADICALE:proxy-write}
        # We use a single dict to keep cleanup simple.
        self._meta_cache: dict = {}

    def attach_storage(self, storage_obj: "storage.BaseStorage") -> None:
        """Attach the storage backend so we can read shares/proxy metadata.

        Called once by ApplicationBase after both rights and storage are
        loaded. Without this, _check_shared_access falls through to its
        empty-cache path (returns "") and shared-calendar access never
        works - which is the gap this method exists to close.
        """
        self._storage = storage_obj

    def invalidate_share_cache(self, collection_path: str) -> None:
        """Drop cached shares/proxy metadata for a collection or principal.

        Called by the sharing handler after every state mutation (add,
        remove, accept, decline) so the next authorization() reads fresh
        data from storage. Without this hook, a 5s TTL cache window
        could let the rights backend grant or deny access based on
        pre-mutation state (e.g. just-accepted share still shown as
        pending; just-revoked share still shown as accepted).
        """
        normalized = collection_path.strip("/")
        # Drop the collection-scoped shares cache.
        self._meta_cache.pop("shares:" + normalized, None)
        # Also drop the proxy cache for the owner principal (in case the
        # caller is mutating proxy lists at the same time, e.g. delegation
        # changes flow through the same handler).
        owner = normalized.split("/", 1)[0] if normalized else ""
        if owner:
            self._meta_cache.pop("proxy:" + owner, None)

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

        # Special case: a user owns their entire notifications subtree at
        # any depth. owner_only's authorization() only grants up to depth-1
        # ("user/collection"), but the CalendarServer notifications design
        # stores each invite/reply/deleted as a NESTED collection at
        # /{user}/notifications/{uid}/, so PROPFIND depth=1 needs depth-2
        # rights to enumerate them.
        parts = sane_path.split("/")
        if len(parts) >= 3 and parts[0] == user and parts[1] == "notifications":
            return "rw"

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

        # Read the shares property from storage (TTL-cached).
        shares_data = self._get_shares_for_path(collection_path)
        if not shares_data:
            return ""

        # Check if user is in shares
        user_share = shares_data.get(user)
        if not user_share:
            return ""
        if not isinstance(user_share, dict):
            # A malformed entry (e.g. {"bob": "read-write"}) would otherwise
            # raise AttributeError out of authorization() and surface as a
            # 500 for every request on this collection. Deny instead.
            logger.warning("rights: share entry for %s on %s is %s, expected "
                           "an object - denying", user, collection_path,
                           type(user_share).__name__)
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

    def _cache_get(self, key: str):
        entry = self._meta_cache.get(key)
        if entry is None:
            return None
        expiry, value = entry
        if expiry < time.monotonic():
            # pop(), not del: two threads can both observe the same expired
            # entry and race to remove it, and `del` would raise KeyError in
            # the loser. The HTTP layer is genuinely multi-threaded now, and
            # this dict is shared by every worker.
            self._meta_cache.pop(key, None)
            return None
        return value

    def _cache_put(self, key: str, value) -> None:
        self._meta_cache[key] = (time.monotonic() + self._CACHE_TTL_SECONDS, value)

    def _load_collection_meta(self, collection_path: str) -> dict:
        """Read .Radicale.props for a collection path; return empty dict on miss.

        Deliberately does NOT take the storage lock. authorization() is called
        from handlers that already hold the global write lock - DELETE, MOVE
        and PUT all evaluate Access.parent_permissions inside
        acquire_lock("w"). The lock is a single global flock with no timeout
        and it is not re-entrant, so acquiring a read lock here would
        self-deadlock the worker *while it holds the exclusive lock*, wedging
        every subsequent request from every user until the process is killed.

        Reading unlocked is safe: set_meta writes via _atomic_write (temp file
        + rename), so a concurrent reader sees either the complete old file or
        the complete new one, never a torn read. The worst case is a slightly
        stale but well-formed value, which the TTL cache already tolerates.
        """
        if self._storage is None:
            return {}
        try:
            items = list(self._storage.discover(
                "/" + collection_path + "/", depth="0"))
            if not items:
                return {}
            coll = items[0]
            if not hasattr(coll, "get_meta"):
                return {}
            # get_meta() with no arg returns full property mapping.
            return dict(coll.get_meta())
        except Exception as e:
            # Fail closed - callers treat {} as "no shares", i.e. deny.
            logger.warning("rights: failed to read meta for %s: %s",
                           collection_path, e)
            return {}

    def _get_shares_for_path(self, collection_path: str) -> dict:
        """Get shares dict (sharee -> share-info) for a collection.

        Returns empty dict if no shares property, malformed JSON, or no
        storage attached. Cached for self._CACHE_TTL_SECONDS so a single
        PROPFIND that authorizes many children doesn't re-read the same
        .Radicale.props on every call.
        """
        cache_key = "shares:" + collection_path
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        meta = self._load_collection_meta(collection_path)
        shares: dict = {}
        shares_json = meta.get(SHARES_PROPERTY)
        if shares_json:
            try:
                parsed = json.loads(shares_json)
                if isinstance(parsed, dict):
                    shares = parsed
            except json.JSONDecodeError:
                logger.warning("rights: malformed %s on %s",
                               SHARES_PROPERTY, collection_path)

        self._cache_put(cache_key, shares)
        return shares

    def _load_principal_meta(self, owner: str) -> dict:
        """Read .Radicale.props for a principal (user-level) collection.

        Unlocked for the same reason as _load_collection_meta - see the
        explanation there.
        """
        if self._storage is None:
            return {}
        try:
            items = list(self._storage.discover("/" + owner + "/", depth="0"))
            if not items:
                return {}
            coll = items[0]
            if not hasattr(coll, "get_meta"):
                return {}
            return dict(coll.get_meta())
        except Exception as e:
            # Fail closed - callers treat {} as "no proxy rights", i.e. deny.
            logger.warning("rights: failed to read principal meta for %s: %s",
                           owner, e)
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
        cache_key = "proxy:" + owner
        cached = self._cache_get(cache_key)
        if cached is None:
            cached = self._load_principal_meta(owner)
            self._cache_put(cache_key, cached)
        owner_meta = cached

        # Check write proxy first (higher privilege)
        if _json_list_contains(owner_meta.get(PROXY_WRITE_PROPERTY, "[]"),
                               user, PROXY_WRITE_PROPERTY, owner):
            return "write"

        # Check read proxy
        if _json_list_contains(owner_meta.get(PROXY_READ_PROPERTY, "[]"),
                               user, PROXY_READ_PROPERTY, owner):
            return "read"
        return ""

    def set_collection_meta(self, path: str, meta: dict) -> None:
        """
        Pre-populate the metadata cache for rights checking.

        Kept for the unit-test path that exercises the rights backend in
        isolation without a real storage. In production, _load_collection_meta
        and _load_principal_meta read directly from storage on demand, so this
        method is rarely used.

        Args:
            path: Collection path (can be with or without leading/trailing slashes)
            meta: Collection metadata dictionary
        """
        sane_path = path.strip("/")

        # Collection-level path: parse and cache the SHARES_PROPERTY content.
        if "/" in sane_path:
            shares: dict = {}
            shares_json = meta.get(SHARES_PROPERTY)
            if shares_json:
                try:
                    parsed = json.loads(shares_json)
                    if isinstance(parsed, dict):
                        shares = parsed
                except json.JSONDecodeError:
                    pass
            self._cache_put("shares:" + sane_path, shares)
        elif sane_path:
            # Principal-level path: cache the full meta dict for proxy lookups.
            self._cache_put("proxy:" + sane_path, meta)

    def clear_meta_cache(self) -> None:
        """Clear the metadata cache."""
        self._meta_cache.clear()

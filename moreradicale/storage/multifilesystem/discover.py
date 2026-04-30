# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2014 Jean-Marc Martins
# Copyright © 2012-2017 Guillaume Ayoub
# Copyright © 2017-2018 Unrud <unrud@outlook.com>
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

import base64
import json
import os
import posixpath
from typing import Callable, ContextManager, Iterator, Optional, Set, cast

from moreradicale import pathutils, types
from moreradicale.log import logger
from moreradicale.sharing import SHARES_PROPERTY, InviteStatus
from moreradicale.storage import multifilesystem
from moreradicale.storage.multifilesystem.base import StorageBase


@types.contextmanager
def _null_child_context_manager(path: str,
                                href: Optional[str]) -> Iterator[None]:
    yield


class StoragePartDiscover(StorageBase):

    def discover(
            self, path: str, depth: str = "0",
            child_context_manager: Optional[
            Callable[[str, Optional[str]], ContextManager[None]]] = None,
            user_groups: Set[str] = set([])
            ) -> Iterator[types.CollectionOrItem]:
        # assert isinstance(self, multifilesystem.Storage)
        if child_context_manager is None:
            child_context_manager = _null_child_context_manager
        # Path should already be sanitized
        sane_path = pathutils.strip_path(path)
        attributes = sane_path.split("/") if sane_path else []

        folder = self._get_collection_root_folder()
        # Create the root collection
        self._makedirs_synced(folder)
        try:
            filesystem_path = pathutils.path_to_filesystem(folder, sane_path)
        except ValueError as e:
            # Path is unsafe
            logger.debug("Unsafe path %r requested from storage: %s",
                         sane_path, e, exc_info=True)
            return

        # Check if the path exists and if it leads to a collection or an item
        href: Optional[str]
        if not os.path.isdir(filesystem_path):
            if attributes and os.path.isfile(filesystem_path):
                href = attributes.pop()
            else:
                return
        else:
            href = None

        sane_path = "/".join(attributes)
        collection = self._collection_class(
            cast(multifilesystem.Storage, self),
            pathutils.unstrip_path(sane_path, True))

        if href:
            item = collection._get(href)
            if item is not None:
                yield item
            return

        yield collection

        # RFC 6638: Auto-create scheduling collections for principals
        logger.debug("Checking scheduling auto-create: is_principal=%s, depth=%s, path=%s",
                     collection.is_principal, depth, collection.path)
        if collection.is_principal:
            logger.debug("Principal discovered with depth=%s, calling _ensure_scheduling_collections", depth)
            self._ensure_scheduling_collections(collection, folder, sane_path)

        if depth == "0":
            return

        for href in collection._list():
            with child_context_manager(sane_path, href):
                item = collection._get(href)
                if item is not None:
                    yield item

        for entry in os.scandir(filesystem_path):
            if not entry.is_dir():
                continue
            href = entry.name
            if not pathutils.is_safe_filesystem_path_component(href):
                if not href.startswith(".Radicale"):
                    logger.debug("Skipping collection %r in %r",
                                 href, sane_path)
                continue
            sane_child_path = posixpath.join(sane_path, href)
            child_path = pathutils.unstrip_path(sane_child_path, True)
            with child_context_manager(sane_child_path, None):
                yield self._collection_class(
                    cast(multifilesystem.Storage, self), child_path)
        for group in user_groups:
            href = base64.b64encode(group.encode('utf-8')).decode('ascii')
            logger.debug(f"searching for group calendar {group} {href}")
            sane_child_path = f"GROUPS/{href}"
            if not os.path.isdir(pathutils.path_to_filesystem(folder, sane_child_path)):
                continue
            child_path = f"/GROUPS/{href}/"
            with child_context_manager(sane_child_path, None):
                yield self._collection_class(
                    cast(multifilesystem.Storage, self), child_path)

        # Discover shared calendars (if sharing is enabled and this is a principal)
        if collection.is_principal:
            sharing_enabled = self.configuration.get("sharing", "enabled")
            if sharing_enabled:
                # The username is the principal path (first component)
                username = sane_path.split("/")[0] if sane_path else ""
                if username:
                    for shared_collection in self._discover_shared_calendars(
                            username, folder, child_context_manager):
                        yield shared_collection

    def _ensure_scheduling_collections(self, principal_collection, folder: str,
                                       sane_path: str) -> None:
        """Ensure schedule-inbox and schedule-outbox exist for principal.

        RFC 6638 requires principals to have inbox and outbox collections
        for scheduling operations. This method creates them if they don't exist.
        """
        # assert isinstance(self, multifilesystem.Storage)

        # Only create scheduling collections if scheduling is enabled
        # Default to True since we have implemented RFC 6638 support
        scheduling_enabled = self.configuration.get("scheduling", "enabled")
        if scheduling_enabled is None:
            scheduling_enabled = True
        logger.debug("Scheduling enabled: %s", scheduling_enabled)
        if not scheduling_enabled:
            logger.debug("Scheduling not enabled, skipping collection creation")
            return

        for collection_name, tag in [("schedule-inbox", "SCHEDULING-INBOX"),
                                     ("schedule-outbox", "SCHEDULING-OUTBOX")]:
            sane_child_path = posixpath.join(sane_path, collection_name)
            child_path = pathutils.unstrip_path(sane_child_path, True)

            # Check if collection already exists
            try:
                existing = next(iter(self.discover(child_path, depth="0")), None)
                if existing:
                    logger.debug("Scheduling collection %s already exists", child_path)
                    continue
            except Exception:
                pass

            # Create collection with proper props
            try:
                props = {"tag": tag}
                cast(multifilesystem.Storage, self).create_collection(
                    child_path, props=props)
                logger.debug("Created scheduling collection: %s (tag=%s)",
                             child_path, tag)
            except Exception as e:
                logger.warning("Failed to create %s: %s", collection_name, e)

    def _discover_shared_calendars(
            self, username: str, folder: str,
            child_context_manager: Callable[[str, Optional[str]], ContextManager[None]]
            ) -> Iterator[types.CollectionOrItem]:
        """Discover calendars shared with the given user.

        This method scans all users' collections to find calendars that have
        been shared with the given username. It yields collection objects for
        any shared calendars where the user has accepted the share.

        Args:
            username: The user to find shared calendars for
            folder: The storage root folder
            child_context_manager: Context manager for child operations

        Yields:
            Collection objects for shared calendars
        """
        # Track which paths we've already yielded to avoid duplicates
        yielded_paths: Set[str] = set()

        logger.debug("Discovering shared calendars for user: %s", username)

        # Scan all user folders in collection-root
        try:
            for owner_entry in os.scandir(folder):
                if not owner_entry.is_dir():
                    continue

                owner = owner_entry.name
                # Skip the user's own collections (already discovered)
                if owner == username:
                    continue

                # Skip system folders and hidden folders
                if not pathutils.is_safe_filesystem_path_component(owner):
                    continue

                # Scan this owner's collections for shares
                owner_path = pathutils.path_to_filesystem(folder, owner)
                try:
                    for collection_entry in os.scandir(owner_path):
                        if not collection_entry.is_dir():
                            continue

                        collection_name = collection_entry.name
                        if not pathutils.is_safe_filesystem_path_component(collection_name):
                            continue

                        # Check if this collection is shared with username
                        sane_collection_path = f"{owner}/{collection_name}"
                        if sane_collection_path in yielded_paths:
                            continue

                        share_access = self._check_collection_shared_with(
                            folder, sane_collection_path, username)

                        if share_access:
                            logger.debug(
                                "Found shared calendar: %s shared with %s (access=%s)",
                                sane_collection_path, username, share_access)

                            child_path = pathutils.unstrip_path(
                                sane_collection_path, True)
                            yielded_paths.add(sane_collection_path)

                            with child_context_manager(sane_collection_path, None):
                                yield self._collection_class(
                                    cast(multifilesystem.Storage, self), child_path)

                except (OSError, PermissionError) as e:
                    logger.debug("Cannot scan owner folder %s: %s", owner, e)
                    continue

        except (OSError, PermissionError) as e:
            logger.warning("Cannot scan storage folder for shared calendars: %s", e)

    def _check_collection_shared_with(
            self, folder: str, sane_path: str, username: str) -> Optional[str]:
        """Check if a collection is shared with the given user.

        Args:
            folder: Storage root folder
            sane_path: Sanitized collection path (e.g., "alice/calendar")
            username: User to check for sharing

        Returns:
            Access level ("read" or "read-write") if shared and accepted, None otherwise
        """
        # Build path to .Radicale.props file
        collection_fs_path = pathutils.path_to_filesystem(folder, sane_path)
        props_path = os.path.join(collection_fs_path, ".Radicale.props")

        if not os.path.isfile(props_path):
            return None

        try:
            with open(props_path, encoding="utf-8") as f:
                props = json.load(f)

            shares_json = props.get(SHARES_PROPERTY)
            if not shares_json:
                return None

            shares = json.loads(shares_json)
            user_share = shares.get(username)
            if not user_share:
                return None

            # Only return access if share is accepted
            status = user_share.get("status", "pending")
            if status != InviteStatus.ACCEPTED.value:
                logger.debug(
                    "Share for %s on %s not accepted (status=%s)",
                    username, sane_path, status)
                return None

            return user_share.get("access", "read")

        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.debug("Error reading shares for %s: %s", sane_path, e)
            return None

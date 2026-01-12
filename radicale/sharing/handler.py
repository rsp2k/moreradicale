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
Handler for CalendarServer sharing XML requests.

This module processes POST requests for calendar sharing operations:
- CS:share-resource - Add, modify, or remove share invitations
- CS:share-reply - Accept or decline a share invitation

Reference: https://github.com/apple/ccs-calendarserver/blob/master/doc/Extensions/caldav-sharing.txt
"""

import xml.etree.ElementTree as ET
from http import client
from typing import TYPE_CHECKING, Optional, Tuple

from radicale import httputils, xmlutils
from radicale.log import logger
from radicale.sharing import (
    SharingManager, ShareAccess, InviteStatus,
    SHARES_PROPERTY
)
from radicale.sharing.notifications import NotificationManager

if TYPE_CHECKING:
    from radicale import config, storage


class SharingHandler:
    """Handles CalendarServer sharing POST requests."""

    def __init__(self, storage: "storage.BaseStorage",
                 configuration: "config.Configuration") -> None:
        self.storage = storage
        self.configuration = configuration
        self.sharing_manager = SharingManager(configuration)
        self.notification_manager = NotificationManager(configuration, storage)

    def handle_sharing_post(self, user: str, xml_content: ET.Element,
                           collection: "storage.BaseCollection",
                           base_prefix: str) -> Tuple[int, dict, str]:
        """
        Handle a sharing POST request.

        Args:
            user: Authenticated username
            xml_content: Parsed XML request body
            collection: Target collection
            base_prefix: URL base prefix

        Returns:
            Tuple of (status_code, headers, body)
        """
        # Determine the type of sharing request
        root_tag = xml_content.tag

        if root_tag == xmlutils.make_clark("CS:share-resource"):
            return self._handle_share_resource(user, xml_content, collection, base_prefix)
        elif root_tag == xmlutils.make_clark("CS:share-reply"):
            return self._handle_share_reply(user, xml_content, collection, base_prefix)
        else:
            logger.warning("Unknown sharing request type: %s", root_tag)
            return httputils.BAD_REQUEST

    def _handle_share_resource(self, user: str, xml_content: ET.Element,
                               collection: "storage.BaseCollection",
                               base_prefix: str) -> Tuple[int, dict, str]:
        """
        Handle CS:share-resource request to add/modify/remove shares.

        XML Structure:
        <CS:share-resource>
            <CS:set>
                <D:href>/bob/</D:href>
                <CS:common-name>Bob Smith</CS:common-name>
                <CS:summary>Sharing my work calendar</CS:summary>
                <CS:read-write/>  or  <CS:read/>
            </CS:set>
            <CS:remove>
                <D:href>/charlie/</D:href>
            </CS:remove>
        </CS:share-resource>
        """
        # Verify user is the owner
        if collection.owner != user:
            logger.warning("User %s attempted to modify shares on %s (owner: %s)",
                         user, collection.path, collection.owner)
            return httputils.FORBIDDEN

        # Process each child element
        for child in xml_content:
            if child.tag == xmlutils.make_clark("CS:set"):
                result = self._process_share_set(user, child, collection)
                if result[0] != client.OK:
                    return result
            elif child.tag == xmlutils.make_clark("CS:remove"):
                result = self._process_share_remove(user, child, collection)
                if result[0] != client.OK:
                    return result

        logger.info("Share resource request completed for %s by %s",
                   collection.path, user)

        # Return success
        return client.OK, {"Content-Type": "text/plain"}, ""

    def _process_share_set(self, user: str, set_elem: ET.Element,
                          collection: "storage.BaseCollection"
                          ) -> Tuple[int, dict, str]:
        """Process a CS:set element to add or update a share."""
        # Extract sharee from href
        href_elem = set_elem.find(xmlutils.make_clark("D:href"))
        if href_elem is None or not href_elem.text:
            logger.warning("Share set missing D:href")
            return httputils.BAD_REQUEST

        # Extract username from href (e.g., "/bob/" -> "bob")
        sharee = href_elem.text.strip("/")
        if "/" in sharee:
            # Handle URLs like /calendars/bob/ -> extract last component
            sharee = sharee.split("/")[-1]

        if not sharee:
            logger.warning("Empty sharee in share set")
            return httputils.BAD_REQUEST

        # Validate that the sharee's principal exists
        sharee_principal_path = f"/{sharee}/"
        try:
            with self.storage.acquire_lock("r", user):
                sharee_exists = any(self.storage.discover(sharee_principal_path, depth="0"))
        except Exception:
            sharee_exists = False

        if not sharee_exists:
            logger.warning("Cannot share with non-existent user: %s", sharee)
            return httputils.NOT_FOUND

        # Check access level
        if set_elem.find(xmlutils.make_clark("CS:read-write")) is not None:
            access = ShareAccess.READ_WRITE
        else:
            access = ShareAccess.READ

        # Get common name (optional)
        cn_elem = set_elem.find(xmlutils.make_clark("CS:common-name"))
        cn = cn_elem.text if cn_elem is not None else None

        # Get share comment/summary (optional message from sharer)
        comment_elem = set_elem.find(xmlutils.make_clark("CS:summary"))
        comment = comment_elem.text if comment_elem is not None else None

        # Add the share
        try:
            self.sharing_manager.add_share(collection, user, sharee, access, cn, comment)
            logger.info("Added share: %s -> %s (%s) on %s",
                       user, sharee, access.value, collection.path)

            # Create invite notification for sharee
            share = self.sharing_manager.get_shares(collection).get(sharee)
            if share:
                collection_name = collection.get_meta("D:displayname") or collection.path
                self.notification_manager.create_invite_notification(
                    sharee=sharee,
                    share=share,
                    collection_path=collection.path,
                    collection_name=collection_name,
                    sharer=user,
                    sharer_cn=None  # Could be enhanced to get user's display name
                )

        except PermissionError as e:
            logger.warning("Permission denied for share: %s", e)
            return httputils.FORBIDDEN
        except ValueError as e:
            logger.warning("Invalid share request: %s", e)
            return httputils.BAD_REQUEST

        return client.OK, {}, ""

    def _process_share_remove(self, user: str, remove_elem: ET.Element,
                             collection: "storage.BaseCollection"
                             ) -> Tuple[int, dict, str]:
        """Process a CS:remove element to remove a share."""
        # Extract sharee from href
        href_elem = remove_elem.find(xmlutils.make_clark("D:href"))
        if href_elem is None or not href_elem.text:
            logger.warning("Share remove missing D:href")
            return httputils.BAD_REQUEST

        # Extract username from href
        sharee = href_elem.text.strip("/")
        if "/" in sharee:
            sharee = sharee.split("/")[-1]

        if not sharee:
            logger.warning("Empty sharee in share remove")
            return httputils.BAD_REQUEST

        # Remove the share
        try:
            removed = self.sharing_manager.remove_share(collection, user, sharee)
            if removed:
                logger.info("Removed share: %s revoked from %s on %s",
                          user, sharee, collection.path)
                # Notify the sharee that their access was revoked
                collection_name = collection.get_meta("D:displayname") or collection.path
                self.notification_manager.create_revocation_notification(
                    sharee=sharee,
                    collection_path=collection.path,
                    collection_name=collection_name,
                    owner=user,
                    owner_cn=None
                )
            else:
                logger.debug("Share %s not found for removal on %s",
                           sharee, collection.path)
        except PermissionError as e:
            logger.warning("Permission denied for share removal: %s", e)
            return httputils.FORBIDDEN

        return client.OK, {}, ""

    def _handle_share_reply(self, user: str, xml_content: ET.Element,
                           collection: "storage.BaseCollection",
                           base_prefix: str) -> Tuple[int, dict, str]:
        """
        Handle CS:share-reply request to accept or decline an invitation.

        XML Structure:
        <CS:share-reply>
            <CS:href>/alice/calendar/</CS:href>  (shared calendar URL)
            <CS:in-reply-to>invite-uid</CS:in-reply-to>  (optional)
            <CS:invite-accepted/>  or  <CS:invite-declined/>
            <CS:hosturl>/alice/calendar/</CS:hosturl>  (optional)
        </CS:share-reply>
        """
        # Check if accepting or declining
        accept = xml_content.find(xmlutils.make_clark("CS:invite-accepted")) is not None
        decline = xml_content.find(xmlutils.make_clark("CS:invite-declined")) is not None

        if not accept and not decline:
            logger.warning("Share reply missing accept/decline element")
            return httputils.BAD_REQUEST

        # Verify user has a pending share on this collection
        shares = self.sharing_manager.get_shares(collection)
        if user not in shares:
            logger.warning("User %s has no share invitation for %s",
                         user, collection.path)
            return httputils.NOT_FOUND

        user_share = shares[user]
        if user_share.status != InviteStatus.PENDING:
            logger.warning("Share invitation for %s on %s already %s",
                         user, collection.path, user_share.status.value)
            return httputils.CONFLICT

        # Process the reply
        try:
            if accept:
                self.sharing_manager.accept_share(collection, user)
                logger.info("User %s accepted share of %s", user, collection.path)
            else:
                self.sharing_manager.decline_share(collection, user)
                logger.info("User %s declined share of %s", user, collection.path)

            # Notify the calendar owner of the response
            if collection.owner:
                self.notification_manager.create_reply_notification(
                    owner=collection.owner,
                    sharee=user,
                    collection_path=collection.path,
                    accepted=accept
                )

        except ValueError as e:
            logger.warning("Invalid share reply: %s", e)
            return httputils.BAD_REQUEST

        return client.OK, {"Content-Type": "text/plain"}, ""


def is_sharing_request(xml_content: Optional[ET.Element]) -> bool:
    """Check if XML content is a sharing request."""
    if xml_content is None:
        return False

    return xml_content.tag in (
        xmlutils.make_clark("CS:share-resource"),
        xmlutils.make_clark("CS:share-reply"),
    )

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
CalDAV Sharing Notifications.

Implements Apple CalendarServer notification collections and resources.
When a calendar is shared, a notification is created in the sharee's
notification collection alerting them of the pending invitation.

Notification Collection Structure:
    /user/notifications/
        invite-{uid}.xml  - Share invitation notification

Supported Notification Types:
    - CS:invite-notification - New share invitation
    - CS:invite-reply - Response to share (for owner)
    - CS:resource-change - Calendar was modified (future)
"""

import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from moreradicale import xmlutils
from moreradicale.log import logger

if TYPE_CHECKING:
    from moreradicale import config, storage
    from moreradicale.sharing import Share


# CalendarServer namespace for sharing
CS = "http://calendarserver.org/ns/"
DAV = "DAV:"

# Property for storing notifications
NOTIFICATIONS_PROPERTY = "RADICALE:notifications"

# Notification collection name
NOTIFICATIONS_COLLECTION = "notifications"


class NotificationType(Enum):
    """Types of CalDAV sharing notifications."""
    INVITE = "invite-notification"
    INVITE_REPLY = "invite-reply"
    INVITE_DELETED = "invite-deleted"  # Share was revoked
    RESOURCE_CHANGE = "resource-change"


@dataclass
class Notification:
    """Represents a sharing notification."""
    uid: str  # Unique identifier
    notification_type: NotificationType
    created_at: str  # ISO timestamp

    # For invite notifications
    shared_collection_path: Optional[str] = None  # Path to shared calendar
    shared_collection_name: Optional[str] = None  # Display name
    sharer_username: Optional[str] = None  # Who shared
    sharer_cn: Optional[str] = None  # Sharer display name
    access_level: Optional[str] = None  # "read" or "read-write"

    # For invite-reply notifications
    reply_from: Optional[str] = None  # Who replied
    reply_status: Optional[str] = None  # "accepted" or "declined"

    # Optional comment/message from sharer
    comment: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON storage."""
        return {
            "uid": self.uid,
            "type": self.notification_type.value,
            "created_at": self.created_at,
            "shared_collection_path": self.shared_collection_path,
            "shared_collection_name": self.shared_collection_name,
            "sharer_username": self.sharer_username,
            "sharer_cn": self.sharer_cn,
            "access_level": self.access_level,
            "reply_from": self.reply_from,
            "reply_status": self.reply_status,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Notification":
        """Create Notification from stored dictionary."""
        return cls(
            uid=data.get("uid", str(uuid.uuid4())),
            notification_type=NotificationType(data.get("type", "invite-notification")),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            shared_collection_path=data.get("shared_collection_path"),
            shared_collection_name=data.get("shared_collection_name"),
            sharer_username=data.get("sharer_username"),
            sharer_cn=data.get("sharer_cn"),
            access_level=data.get("access_level"),
            reply_from=data.get("reply_from"),
            reply_status=data.get("reply_status"),
            comment=data.get("comment"),
        )

    def to_xml(self) -> ET.Element:
        """
        Generate CalendarServer notification XML.

        Returns XML like:
        <CS:notification xmlns:CS="http://calendarserver.org/ns/"
                         xmlns:D="DAV:">
            <CS:dtstamp>2025-01-11T00:00:00Z</CS:dtstamp>
            <CS:invite-notification>
                <CS:uid>...</CS:uid>
                <D:href>/alice/calendar/</D:href>
                <CS:hosturl>
                    <D:href>/alice/calendar/</D:href>
                </CS:hosturl>
                <CS:organizer>
                    <D:href>/alice/</D:href>
                    <CS:common-name>Alice</CS:common-name>
                </CS:organizer>
                <CS:access>
                    <CS:read-write/>
                </CS:access>
            </CS:invite-notification>
        </CS:notification>
        """
        root = ET.Element(xmlutils.make_clark("CS:notification"))
        root.set("xmlns:CS", CS)
        root.set("xmlns:D", DAV)

        # Timestamp
        dtstamp = ET.SubElement(root, xmlutils.make_clark("CS:dtstamp"))
        dtstamp.text = self.created_at

        if self.notification_type == NotificationType.INVITE:
            self._build_invite_xml(root)
        elif self.notification_type == NotificationType.INVITE_REPLY:
            self._build_reply_xml(root)
        elif self.notification_type == NotificationType.INVITE_DELETED:
            self._build_deleted_xml(root)

        return root

    def _build_invite_xml(self, root: ET.Element) -> None:
        """Build invite-notification XML content."""
        invite = ET.SubElement(root, xmlutils.make_clark("CS:invite-notification"))

        # UID
        uid_elem = ET.SubElement(invite, xmlutils.make_clark("CS:uid"))
        uid_elem.text = self.uid

        # Shared resource href
        if self.shared_collection_path:
            href = ET.SubElement(invite, xmlutils.make_clark("D:href"))
            href.text = self.shared_collection_path

            # Hosturl (original calendar location)
            hosturl = ET.SubElement(invite, xmlutils.make_clark("CS:hosturl"))
            host_href = ET.SubElement(hosturl, xmlutils.make_clark("D:href"))
            host_href.text = self.shared_collection_path

        # Summary (calendar name)
        if self.shared_collection_name:
            summary = ET.SubElement(invite, xmlutils.make_clark("CS:summary"))
            summary.text = self.shared_collection_name

        # Organizer (sharer)
        if self.sharer_username:
            organizer = ET.SubElement(invite, xmlutils.make_clark("CS:organizer"))
            org_href = ET.SubElement(organizer, xmlutils.make_clark("D:href"))
            org_href.text = f"/{self.sharer_username}/"
            if self.sharer_cn:
                cn = ET.SubElement(organizer, xmlutils.make_clark("CS:common-name"))
                cn.text = self.sharer_cn

        # Access level
        if self.access_level:
            access = ET.SubElement(invite, xmlutils.make_clark("CS:access"))
            if self.access_level == "read-write":
                ET.SubElement(access, xmlutils.make_clark("CS:read-write"))
            else:
                ET.SubElement(access, xmlutils.make_clark("CS:read"))

        # Optional comment/message from sharer
        if self.comment:
            comment_elem = ET.SubElement(invite, xmlutils.make_clark("CS:comment"))
            comment_elem.text = self.comment

    def _build_reply_xml(self, root: ET.Element) -> None:
        """Build invite-reply XML content."""
        reply = ET.SubElement(root, xmlutils.make_clark("CS:invite-reply"))

        # UID
        uid_elem = ET.SubElement(reply, xmlutils.make_clark("CS:uid"))
        uid_elem.text = self.uid

        # Shared resource
        if self.shared_collection_path:
            hosturl = ET.SubElement(reply, xmlutils.make_clark("CS:hosturl"))
            href = ET.SubElement(hosturl, xmlutils.make_clark("D:href"))
            href.text = self.shared_collection_path

        # Who replied
        if self.reply_from:
            attendee = ET.SubElement(reply, xmlutils.make_clark("CS:attendee"))
            att_href = ET.SubElement(attendee, xmlutils.make_clark("D:href"))
            att_href.text = f"/{self.reply_from}/"

        # Status
        if self.reply_status:
            if self.reply_status == "accepted":
                ET.SubElement(reply, xmlutils.make_clark("CS:invite-accepted"))
            else:
                ET.SubElement(reply, xmlutils.make_clark("CS:invite-declined"))

    def _build_deleted_xml(self, root: ET.Element) -> None:
        """Build invite-deleted XML content for share revocation."""
        deleted = ET.SubElement(root, xmlutils.make_clark("CS:invite-deleted"))

        # UID
        uid_elem = ET.SubElement(deleted, xmlutils.make_clark("CS:uid"))
        uid_elem.text = self.uid

        # Shared resource that was unshared
        if self.shared_collection_path:
            hosturl = ET.SubElement(deleted, xmlutils.make_clark("CS:hosturl"))
            href = ET.SubElement(hosturl, xmlutils.make_clark("D:href"))
            href.text = self.shared_collection_path

        # Summary (calendar name)
        if self.shared_collection_name:
            summary = ET.SubElement(deleted, xmlutils.make_clark("CS:summary"))
            summary.text = self.shared_collection_name

        # Who revoked the share
        if self.sharer_username:
            organizer = ET.SubElement(deleted, xmlutils.make_clark("CS:organizer"))
            org_href = ET.SubElement(organizer, xmlutils.make_clark("D:href"))
            org_href.text = f"/{self.sharer_username}/"
            if self.sharer_cn:
                cn = ET.SubElement(organizer, xmlutils.make_clark("CS:common-name"))
                cn.text = self.sharer_cn

    def get_filename(self) -> str:
        """Get filename for this notification resource."""
        if self.notification_type == NotificationType.INVITE:
            return f"invite-{self.uid}.xml"
        elif self.notification_type == NotificationType.INVITE_REPLY:
            return f"reply-{self.uid}.xml"
        elif self.notification_type == NotificationType.INVITE_DELETED:
            return f"deleted-{self.uid}.xml"
        return f"notification-{self.uid}.xml"


class NotificationManager:
    """
    Manages sharing notification operations.

    Creates, stores, and retrieves notification resources in
    user notification collections.
    """

    def __init__(self, configuration: "config.Configuration",
                 storage: "storage.BaseStorage") -> None:
        """Initialize NotificationManager."""
        self.configuration = configuration
        self.storage = storage

    def is_enabled(self) -> bool:
        """Check if notifications are enabled."""
        return (self.configuration.get("sharing", "enabled") and
                self.configuration.get("sharing", "notifications_enabled"))

    def get_notification_collection_path(self, username: str) -> str:
        """Get path to user's notification collection."""
        return f"/{username}/{NOTIFICATIONS_COLLECTION}/"

    def ensure_notification_collection(self, username: str) -> bool:
        """
        Ensure user has a notification collection.

        Creates /{username}/notifications/ if it doesn't exist.

        Args:
            username: User to create collection for

        Returns:
            True if collection exists or was created
        """
        path = self.get_notification_collection_path(username)

        # Check if exists
        try:
            with self.storage.acquire_lock("r", username):
                items = list(self.storage.discover(path, depth="0"))
                if items:
                    return True
        except Exception as e:
            logger.debug("Notification collection check failed: %s", e)

        # Create collection
        try:
            with self.storage.acquire_lock("w", username):
                props = {
                    "tag": "",  # Not a calendar or addressbook
                    "D:displayname": "Notifications",
                    "D:resourcetype": "<D:collection/><CS:notification/>",
                }
                self.storage.create_collection(path, props=props)
                logger.info("Created notification collection for %s", username)
                return True
        except Exception as e:
            logger.warning("Failed to create notification collection for %s: %s",
                           username, e)
            return False

    def create_invite_notification(self, sharee: str, share: "Share",
                                   collection_path: str,
                                   collection_name: str,
                                   sharer: str,
                                   sharer_cn: Optional[str] = None) -> Optional[str]:
        """
        Create an invite notification for a share.

        Args:
            sharee: Username receiving the share
            share: Share object with access details
            collection_path: Path to shared calendar
            collection_name: Display name of calendar
            sharer: Username who shared
            sharer_cn: Display name of sharer

        Returns:
            Notification UID if created, None on failure
        """
        if not self.is_enabled():
            return None

        # Ensure notification collection exists
        if not self.ensure_notification_collection(sharee):
            return None

        # Create notification
        notification = Notification(
            uid=str(uuid.uuid4()),
            notification_type=NotificationType.INVITE,
            created_at=datetime.now(timezone.utc).isoformat(),
            shared_collection_path=collection_path,
            shared_collection_name=collection_name,
            sharer_username=sharer,
            sharer_cn=sharer_cn,
            access_level=share.access.value if hasattr(share, 'access') else "read",
            comment=share.comment if hasattr(share, 'comment') else None,
        )

        # Store notification
        if self._store_notification(sharee, notification):
            logger.info("Created invite notification for %s from %s",
                        sharee, sharer)
            return notification.uid

        return None

    def create_reply_notification(self, owner: str, sharee: str,
                                  collection_path: str,
                                  accepted: bool) -> Optional[str]:
        """
        Create a reply notification for the calendar owner.

        When a sharee accepts/declines, notify the owner.

        Args:
            owner: Calendar owner to notify
            sharee: User who replied
            collection_path: Path to shared calendar
            accepted: True if accepted, False if declined

        Returns:
            Notification UID if created, None on failure
        """
        if not self.is_enabled():
            return None

        # Ensure notification collection exists
        if not self.ensure_notification_collection(owner):
            return None

        # Create notification
        notification = Notification(
            uid=str(uuid.uuid4()),
            notification_type=NotificationType.INVITE_REPLY,
            created_at=datetime.now(timezone.utc).isoformat(),
            shared_collection_path=collection_path,
            reply_from=sharee,
            reply_status="accepted" if accepted else "declined",
        )

        # Store notification
        if self._store_notification(owner, notification):
            logger.info("Created reply notification for %s from %s (%s)",
                        owner, sharee, "accepted" if accepted else "declined")
            return notification.uid

        return None

    def create_revocation_notification(self, sharee: str,
                                       collection_path: str,
                                       collection_name: str,
                                       owner: str,
                                       owner_cn: Optional[str] = None) -> Optional[str]:
        """
        Create a revocation notification when a share is removed.

        Notifies the sharee that their access to a calendar has been revoked.

        Args:
            sharee: User who lost access
            collection_path: Path to the calendar
            collection_name: Display name of calendar
            owner: Calendar owner who revoked the share
            owner_cn: Display name of owner

        Returns:
            Notification UID if created, None on failure
        """
        if not self.is_enabled():
            return None

        # Ensure notification collection exists
        if not self.ensure_notification_collection(sharee):
            return None

        # Create notification
        notification = Notification(
            uid=str(uuid.uuid4()),
            notification_type=NotificationType.INVITE_DELETED,
            created_at=datetime.now(timezone.utc).isoformat(),
            shared_collection_path=collection_path,
            shared_collection_name=collection_name,
            sharer_username=owner,
            sharer_cn=owner_cn,
        )

        # Store notification
        if self._store_notification(sharee, notification):
            logger.info("Created revocation notification for %s from %s",
                        sharee, owner)
            return notification.uid

        return None

    def get_notifications(self, username: str) -> List[Notification]:
        """
        Get all notifications for a user.

        Args:
            username: User to get notifications for

        Returns:
            List of Notification objects
        """
        notifications = []
        path = self.get_notification_collection_path(username)

        try:
            with self.storage.acquire_lock("r", username):
                for item in self.storage.discover(path, depth="1"):
                    if hasattr(item, 'get_meta'):
                        notif_json = item.get_meta(NOTIFICATIONS_PROPERTY)
                        if notif_json:
                            import json
                            data = json.loads(notif_json)
                            notifications.append(Notification.from_dict(data))
        except Exception as e:
            logger.debug("Failed to get notifications for %s: %s", username, e)

        return notifications

    def delete_notification(self, username: str, uid: str) -> bool:
        """
        Delete a notification.

        Args:
            username: User who owns the notification
            uid: Notification UID to delete

        Returns:
            True if deleted
        """
        path = self.get_notification_collection_path(username)

        try:
            with self.storage.acquire_lock("w", username):
                for item in self.storage.discover(path, depth="1"):
                    if hasattr(item, 'get_meta'):
                        notif_json = item.get_meta(NOTIFICATIONS_PROPERTY)
                        if notif_json:
                            import json
                            data = json.loads(notif_json)
                            if data.get("uid") == uid:
                                # Delete the item
                                self.storage.delete_collection(item.path)
                                logger.info("Deleted notification %s for %s",
                                            uid, username)
                                return True
        except Exception as e:
            logger.warning("Failed to delete notification %s: %s", uid, e)

        return False

    def _store_notification(self, username: str,
                            notification: Notification) -> bool:
        """Store a notification as a resource."""
        import json

        path = (self.get_notification_collection_path(username) +
                notification.get_filename())

        try:
            with self.storage.acquire_lock("w", username):
                # Create notification as collection item
                props = {
                    "tag": "notification",
                    NOTIFICATIONS_PROPERTY: json.dumps(notification.to_dict()),
                }

                # Generate XML content
                ET.tostring(notification.to_xml(),
                                          encoding="unicode")

                # Store as item with XML content
                # Note: Using a simple approach - store metadata with props
                self.storage.create_collection(path, props=props)
                return True

        except Exception as e:
            logger.warning("Failed to store notification: %s", e)
            return False


def get_notification_manager(configuration: "config.Configuration",
                             storage: "storage.BaseStorage"
                             ) -> NotificationManager:
    """Get NotificationManager instance."""
    return NotificationManager(configuration, storage)

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
Tests for CalDAV sharing notification functionality.

Tests cover:
- Notification dataclass serialization
- NotificationType enum values
- NotificationManager operations
- XML notification generation
- CS:notification-URL PROPFIND property
"""

import json
import xml.etree.ElementTree as ET

from moreradicale import config, xmlutils
from moreradicale.sharing.notifications import (
    Notification, NotificationType, NotificationManager,
    NOTIFICATIONS_PROPERTY, NOTIFICATIONS_COLLECTION, CS, DAV
)
from moreradicale.sharing import Share, ShareAccess


class TestNotificationType:
    """Test NotificationType enum values."""

    def test_invite_value(self):
        """Test INVITE notification type value."""
        assert NotificationType.INVITE.value == "invite-notification"

    def test_invite_reply_value(self):
        """Test INVITE_REPLY notification type value."""
        assert NotificationType.INVITE_REPLY.value == "invite-reply"

    def test_resource_change_value(self):
        """Test RESOURCE_CHANGE notification type value."""
        assert NotificationType.RESOURCE_CHANGE.value == "resource-change"


class TestNotification:
    """Test Notification dataclass."""

    def test_notification_to_dict(self):
        """Test converting Notification to dictionary."""
        notification = Notification(
            uid="test-uid-123",
            notification_type=NotificationType.INVITE,
            created_at="2025-01-11T00:00:00Z",
            shared_collection_path="/alice/calendar/",
            shared_collection_name="Work Calendar",
            sharer_username="alice",
            sharer_cn="Alice Smith",
            access_level="read-write"
        )

        result = notification.to_dict()

        assert result["uid"] == "test-uid-123"
        assert result["type"] == "invite-notification"
        assert result["created_at"] == "2025-01-11T00:00:00Z"
        assert result["shared_collection_path"] == "/alice/calendar/"
        assert result["shared_collection_name"] == "Work Calendar"
        assert result["sharer_username"] == "alice"
        assert result["sharer_cn"] == "Alice Smith"
        assert result["access_level"] == "read-write"

    def test_notification_from_dict(self):
        """Test creating Notification from dictionary."""
        data = {
            "uid": "test-uid-456",
            "type": "invite-notification",
            "created_at": "2025-01-10T12:00:00Z",
            "shared_collection_path": "/bob/calendar/",
            "shared_collection_name": "Team Calendar",
            "sharer_username": "bob",
            "access_level": "read"
        }

        notification = Notification.from_dict(data)

        assert notification.uid == "test-uid-456"
        assert notification.notification_type == NotificationType.INVITE
        assert notification.created_at == "2025-01-10T12:00:00Z"
        assert notification.shared_collection_path == "/bob/calendar/"
        assert notification.shared_collection_name == "Team Calendar"
        assert notification.sharer_username == "bob"
        assert notification.access_level == "read"

    def test_notification_reply_to_dict(self):
        """Test converting reply Notification to dictionary."""
        notification = Notification(
            uid="reply-uid-789",
            notification_type=NotificationType.INVITE_REPLY,
            created_at="2025-01-11T06:00:00Z",
            shared_collection_path="/alice/calendar/",
            reply_from="charlie",
            reply_status="accepted"
        )

        result = notification.to_dict()

        assert result["uid"] == "reply-uid-789"
        assert result["type"] == "invite-reply"
        assert result["reply_from"] == "charlie"
        assert result["reply_status"] == "accepted"

    def test_get_filename_invite(self):
        """Test filename generation for invite notification."""
        notification = Notification(
            uid="abc123",
            notification_type=NotificationType.INVITE,
            created_at="2025-01-11T00:00:00Z"
        )

        assert notification.get_filename() == "invite-abc123.xml"

    def test_get_filename_reply(self):
        """Test filename generation for reply notification."""
        notification = Notification(
            uid="def456",
            notification_type=NotificationType.INVITE_REPLY,
            created_at="2025-01-11T00:00:00Z"
        )

        assert notification.get_filename() == "reply-def456.xml"


class TestNotificationXML:
    """Test Notification XML generation."""

    def test_invite_notification_xml(self):
        """Test generating XML for invite notification."""
        notification = Notification(
            uid="xml-test-1",
            notification_type=NotificationType.INVITE,
            created_at="2025-01-11T12:00:00Z",
            shared_collection_path="/alice/work-calendar/",
            shared_collection_name="Work Calendar",
            sharer_username="alice",
            sharer_cn="Alice Smith",
            access_level="read-write"
        )

        xml_elem = notification.to_xml()
        xml_str = ET.tostring(xml_elem, encoding="unicode")

        # Verify root element
        assert xml_elem.tag == xmlutils.make_clark("CS:notification")

        # Verify dtstamp
        dtstamp = xml_elem.find(xmlutils.make_clark("CS:dtstamp"))
        assert dtstamp is not None
        assert dtstamp.text == "2025-01-11T12:00:00Z"

        # Verify invite-notification element
        invite = xml_elem.find(xmlutils.make_clark("CS:invite-notification"))
        assert invite is not None

        # Verify UID
        uid = invite.find(xmlutils.make_clark("CS:uid"))
        assert uid is not None
        assert uid.text == "xml-test-1"

        # Verify href
        href = invite.find(xmlutils.make_clark("D:href"))
        assert href is not None
        assert href.text == "/alice/work-calendar/"

        # Verify organizer
        organizer = invite.find(xmlutils.make_clark("CS:organizer"))
        assert organizer is not None
        org_href = organizer.find(xmlutils.make_clark("D:href"))
        assert org_href is not None
        assert org_href.text == "/alice/"
        cn = organizer.find(xmlutils.make_clark("CS:common-name"))
        assert cn is not None
        assert cn.text == "Alice Smith"

        # Verify access level
        access = invite.find(xmlutils.make_clark("CS:access"))
        assert access is not None
        rw = access.find(xmlutils.make_clark("CS:read-write"))
        assert rw is not None

    def test_invite_notification_xml_read_only(self):
        """Test generating XML for read-only invite notification."""
        notification = Notification(
            uid="xml-test-2",
            notification_type=NotificationType.INVITE,
            created_at="2025-01-11T12:00:00Z",
            shared_collection_path="/alice/calendar/",
            sharer_username="alice",
            access_level="read"
        )

        xml_elem = notification.to_xml()

        invite = xml_elem.find(xmlutils.make_clark("CS:invite-notification"))
        access = invite.find(xmlutils.make_clark("CS:access"))
        assert access is not None
        read = access.find(xmlutils.make_clark("CS:read"))
        assert read is not None
        rw = access.find(xmlutils.make_clark("CS:read-write"))
        assert rw is None

    def test_reply_notification_xml_accepted(self):
        """Test generating XML for accepted reply notification."""
        notification = Notification(
            uid="reply-test-1",
            notification_type=NotificationType.INVITE_REPLY,
            created_at="2025-01-11T14:00:00Z",
            shared_collection_path="/alice/calendar/",
            reply_from="bob",
            reply_status="accepted"
        )

        xml_elem = notification.to_xml()
        xml_str = ET.tostring(xml_elem, encoding="unicode")

        # Verify root element
        assert xml_elem.tag == xmlutils.make_clark("CS:notification")

        # Verify invite-reply element
        reply = xml_elem.find(xmlutils.make_clark("CS:invite-reply"))
        assert reply is not None

        # Verify UID
        uid = reply.find(xmlutils.make_clark("CS:uid"))
        assert uid is not None
        assert uid.text == "reply-test-1"

        # Verify attendee (who replied)
        attendee = reply.find(xmlutils.make_clark("CS:attendee"))
        assert attendee is not None
        att_href = attendee.find(xmlutils.make_clark("D:href"))
        assert att_href is not None
        assert att_href.text == "/bob/"

        # Verify invite-accepted
        accepted = reply.find(xmlutils.make_clark("CS:invite-accepted"))
        assert accepted is not None

    def test_reply_notification_xml_declined(self):
        """Test generating XML for declined reply notification."""
        notification = Notification(
            uid="reply-test-2",
            notification_type=NotificationType.INVITE_REPLY,
            created_at="2025-01-11T14:00:00Z",
            shared_collection_path="/alice/calendar/",
            reply_from="charlie",
            reply_status="declined"
        )

        xml_elem = notification.to_xml()

        reply = xml_elem.find(xmlutils.make_clark("CS:invite-reply"))
        declined = reply.find(xmlutils.make_clark("CS:invite-declined"))
        assert declined is not None
        accepted = reply.find(xmlutils.make_clark("CS:invite-accepted"))
        assert accepted is None


class TestNotificationConstants:
    """Test notification module constants."""

    def test_notifications_property_name(self):
        """Test the property name for storing notifications."""
        assert NOTIFICATIONS_PROPERTY == "RADICALE:notifications"

    def test_notifications_collection_name(self):
        """Test the notification collection name."""
        assert NOTIFICATIONS_COLLECTION == "notifications"

    def test_cs_namespace(self):
        """Test the CalendarServer namespace."""
        assert CS == "http://calendarserver.org/ns/"

    def test_dav_namespace(self):
        """Test the DAV namespace."""
        assert DAV == "DAV:"


from moreradicale.tests import BaseTest


class TestNotificationManager(BaseTest):
    """Test NotificationManager operations."""

    def setup_method(self):
        """Set up test configuration."""
        super().setup_method()
        self.configure({
            "sharing": {
                "enabled": "True",
                "notifications_enabled": "True"
            }
        })

    def test_is_enabled(self):
        """Test checking if notifications are enabled."""
        manager = NotificationManager(self.configuration, self.application._storage)
        assert manager.is_enabled() is True

    def test_is_enabled_sharing_disabled(self):
        """Test notifications disabled when sharing is disabled."""
        self.configure({
            "sharing": {
                "enabled": "False",
                "notifications_enabled": "True"
            }
        })
        manager = NotificationManager(self.configuration, self.application._storage)
        assert manager.is_enabled() is False

    def test_is_enabled_notifications_disabled(self):
        """Test notifications explicitly disabled."""
        self.configure({
            "sharing": {
                "enabled": "True",
                "notifications_enabled": "False"
            }
        })
        manager = NotificationManager(self.configuration, self.application._storage)
        assert manager.is_enabled() is False

    def test_get_notification_collection_path(self):
        """Test notification collection path generation."""
        manager = NotificationManager(self.configuration, self.application._storage)
        path = manager.get_notification_collection_path("alice")
        assert path == "/alice/notifications/"

    def test_get_notification_collection_path_different_user(self):
        """Test notification collection path for different users."""
        manager = NotificationManager(self.configuration, self.application._storage)
        assert manager.get_notification_collection_path("bob") == "/bob/notifications/"
        assert manager.get_notification_collection_path("charlie") == "/charlie/notifications/"

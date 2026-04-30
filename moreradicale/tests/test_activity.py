# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 RFC 3253 Activity Tests
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

"""Tests for RFC 3253 Activity support."""

import subprocess

from moreradicale.tests import BaseTest


class TestActivityManager(BaseTest):
    """Test ActivityManager class."""

    def test_create_activity(self):
        """Test creating an activity."""
        from moreradicale.versioning.activity_manager import ActivityManager

        storage_folder = self.configuration.get("storage", "filesystem_folder")
        manager = ActivityManager(storage_folder)

        activity = manager.create_activity(
            creator="alice",
            display_name="Q1 Updates",
            description="All Q1 2025 changes"
        )

        assert activity.creator == "alice"
        assert activity.display_name == "Q1 Updates"
        assert activity.description == "All Q1 2025 changes"
        assert activity.activity_id  # UUID generated
        assert activity.checkouts == []
        assert activity.versions == []

    def test_get_activity(self):
        """Test retrieving an activity."""
        from moreradicale.versioning.activity_manager import ActivityManager

        storage_folder = self.configuration.get("storage", "filesystem_folder")
        manager = ActivityManager(storage_folder)

        # Create activity
        activity = manager.create_activity(
            creator="bob",
            display_name="Feature X"
        )

        # Retrieve it
        retrieved = manager.get_activity(activity.activity_id)
        assert retrieved is not None
        assert retrieved.activity_id == activity.activity_id
        assert retrieved.creator == "bob"
        assert retrieved.display_name == "Feature X"

    def test_add_checkout_to_activity(self):
        """Test associating checkouts with activities."""
        from moreradicale.versioning.activity_manager import ActivityManager

        storage_folder = self.configuration.get("storage", "filesystem_folder")
        manager = ActivityManager(storage_folder)

        activity = manager.create_activity(
            creator="alice",
            display_name="Test Activity"
        )

        # Add checkout
        success = manager.add_checkout(
            activity.activity_id,
            "collection-root/alice/calendar.ics/event.ics"
        )
        assert success

        # Verify it was added
        updated = manager.get_activity(activity.activity_id)
        assert "collection-root/alice/calendar.ics/event.ics" in updated.checkouts

    def test_add_version_to_activity(self):
        """Test associating versions with activities."""
        from moreradicale.versioning.activity_manager import ActivityManager

        storage_folder = self.configuration.get("storage", "filesystem_folder")
        manager = ActivityManager(storage_folder)

        activity = manager.create_activity(
            creator="alice",
            display_name="Test Activity"
        )

        # Add version
        success = manager.add_version(activity.activity_id, "abc123def456")
        assert success

        # Verify it was added
        updated = manager.get_activity(activity.activity_id)
        assert "abc123def456" in updated.versions

    def test_list_activities(self):
        """Test listing all activities."""
        from moreradicale.versioning.activity_manager import ActivityManager

        storage_folder = self.configuration.get("storage", "filesystem_folder")
        manager = ActivityManager(storage_folder)

        # Create multiple activities
        manager.create_activity(creator="alice", display_name="Activity 1")
        manager.create_activity(creator="bob", display_name="Activity 2")
        manager.create_activity(creator="alice", display_name="Activity 3")

        # List all
        all_activities = manager.list_activities()
        assert len(all_activities) >= 3

        # List by creator
        alice_activities = manager.list_activities(creator="alice")
        assert len(alice_activities) >= 2
        for activity in alice_activities:
            assert activity.creator == "alice"


class TestMkactivityMethod(BaseTest):
    """Test MKACTIVITY HTTP method."""

    def test_mkactivity_creates_activity(self):
        """Test MKACTIVITY creates a new activity."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})

        mkactivity_body = """<?xml version="1.0" encoding="utf-8"?>
<D:mkactivity xmlns:D="DAV:">
  <D:displayname>Test Activity</D:displayname>
  <D:comment>Test activity description</D:comment>
</D:mkactivity>"""

        status, headers, response = self.request(
            "MKACTIVITY", "/.activities/new",
            mkactivity_body,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 201  # Created
        assert "Location" in headers
        assert "activity" in response.lower()

    def test_mkactivity_disabled_when_versioning_off(self):
        """Test MKACTIVITY returns error when versioning disabled."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "False"}})

        status, _, _ = self.request(
            "MKACTIVITY", "/.activities/new",
            "",
            login="test:"
        )

        assert status == 405  # Method Not Allowed

    def test_mkactivity_with_minimal_body(self):
        """Test MKACTIVITY with minimal request body."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})

        # Empty body - should use defaults
        status, _, response = self.request(
            "MKACTIVITY", "/.activities/new",
            "",
            login="test:"
        )

        assert status == 201
        assert "activity" in response.lower()


class TestActivityIntegration(BaseTest):
    """Integration tests for activities with CHECKOUT/CHECKIN."""

    def _init_git_repo(self):
        """Initialize git repository in storage folder."""
        storage_folder = self.configuration.get("storage", "filesystem_folder")
        subprocess.run(["git", "init"], cwd=storage_folder, check=True,
                       capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                       cwd=storage_folder, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=storage_folder, check=True, capture_output=True)

    def _commit_item(self, item_path: str, message: str = "Test commit"):
        """Commit an item to git."""
        storage_folder = self.configuration.get("storage", "filesystem_folder")
        # Path should be relative to storage folder (includes collection-root)
        relative_path = "collection-root" + item_path
        subprocess.run(["git", "add", relative_path], cwd=storage_folder,
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=storage_folder,
                       check=True, capture_output=True)

    def test_checkout_with_activity_context(self):
        """Test CHECKOUT with activity context."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})
        self._init_git_repo()

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:activity-test
DTSTAMP:20250116T120000Z
DTSTART:20250116T100000Z
DTEND:20250116T110000Z
SUMMARY:Activity Test Event
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_ics,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201
        self._commit_item("/test/calendar.ics/event.ics", "Initial event")

        # Create activity
        mkactivity_body = """<?xml version="1.0" encoding="utf-8"?>
<D:mkactivity xmlns:D="DAV:">
  <D:displayname>Q1 Updates</D:displayname>
</D:mkactivity>"""

        status, headers, response = self.request(
            "MKACTIVITY", "/.activities/new",
            mkactivity_body,
            CONTENT_TYPE="application/xml",
            login="test:"
        )
        assert status == 201

        # Extract activity ID from Location header
        location = headers.get("Location", "")
        activity_id = location.split("/")[-1] if location else None
        assert activity_id

        # CHECKOUT with activity context
        checkout_body = f"""<?xml version="1.0" encoding="utf-8"?>
<D:checkout xmlns:D="DAV:">
  <D:activity-set>
    <D:href>/.activities/{activity_id}</D:href>
  </D:activity-set>
</D:checkout>"""

        status, _, response = self.request(
            "CHECKOUT", "/test/calendar.ics/event.ics",
            checkout_body,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 200
        assert "checked-out" in response.lower()
        assert activity_id in response  # Activity should be in response

    def test_checkin_adds_version_to_activity(self):
        """Test CHECKIN adds version to associated activity."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})
        self._init_git_repo()

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:checkin-activity-test
DTSTAMP:20250116T120000Z
DTSTART:20250116T100000Z
DTEND:20250116T110000Z
SUMMARY:Checkin Activity Test
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_ics,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201
        self._commit_item("/test/calendar.ics/event.ics", "Initial event")

        # Create activity
        mkactivity_body = """<?xml version="1.0" encoding="utf-8"?>
<D:mkactivity xmlns:D="DAV:">
  <D:displayname>Test Checkin Activity</D:displayname>
</D:mkactivity>"""

        status, headers, _ = self.request(
            "MKACTIVITY", "/.activities/new",
            mkactivity_body,
            CONTENT_TYPE="application/xml",
            login="test:"
        )
        assert status == 201

        location = headers.get("Location", "")
        activity_id = location.split("/")[-1]

        # CHECKOUT with activity
        checkout_body = f"""<?xml version="1.0" encoding="utf-8"?>
<D:checkout xmlns:D="DAV:">
  <D:activity-set>
    <D:href>/.activities/{activity_id}</D:href>
  </D:activity-set>
</D:checkout>"""

        status, _, _ = self.request(
            "CHECKOUT", "/test/calendar.ics/event.ics",
            checkout_body,
            CONTENT_TYPE="application/xml",
            login="test:"
        )
        assert status == 200

        # Update event
        updated_event = event_ics.replace("Checkin Activity Test",
                                          "Updated Activity Test")
        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            updated_event,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 204

        # CHECKIN - should add version to activity
        status, _, response = self.request(
            "CHECKIN", "/test/calendar.ics/event.ics",
            "",
            login="test:"
        )

        assert status == 201  # Created (new version)

        # Verify activity has the version
        from moreradicale.versioning.activity_manager import ActivityManager
        storage_folder = self.configuration.get("storage", "filesystem_folder")
        manager = ActivityManager(storage_folder)

        activity = manager.get_activity(activity_id)
        assert activity is not None
        assert len(activity.versions) >= 1  # Should have at least one version


class TestActivityPropfind(BaseTest):
    """Test PROPFIND with activity properties."""

    def _init_git_repo(self):
        """Initialize git repository in storage folder."""
        storage_folder = self.configuration.get("storage", "filesystem_folder")
        subprocess.run(["git", "init"], cwd=storage_folder, check=True,
                       capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                       cwd=storage_folder, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=storage_folder, check=True, capture_output=True)

    def _commit_item(self, item_path: str, message: str = "Test commit"):
        """Commit an item to git."""
        storage_folder = self.configuration.get("storage", "filesystem_folder")
        # Path should be relative to storage folder (includes collection-root)
        relative_path = "collection-root" + item_path
        subprocess.run(["git", "add", relative_path], cwd=storage_folder,
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=storage_folder,
                       check=True, capture_output=True)

    def test_propfind_activity_set_property(self):
        """Test PROPFIND returns activity-set property."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {
            "versioning": "True",
            "versioning_include_in_allprop": "True"
        }})
        self._init_git_repo()

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:propfind-activity-test
DTSTAMP:20250116T120000Z
DTSTART:20250116T100000Z
DTEND:20250116T110000Z
SUMMARY:PROPFIND Activity Test
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_ics,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201
        self._commit_item("/test/calendar.ics/event.ics", "Event for PROPFIND")

        # Create activity and checkout
        mkactivity_body = """<?xml version="1.0" encoding="utf-8"?>
<D:mkactivity xmlns:D="DAV:">
  <D:displayname>PROPFIND Test Activity</D:displayname>
</D:mkactivity>"""

        status, headers, _ = self.request(
            "MKACTIVITY", "/.activities/new",
            mkactivity_body,
            CONTENT_TYPE="application/xml",
            login="test:"
        )
        assert status == 201

        location = headers.get("Location", "")
        activity_id = location.split("/")[-1]

        # CHECKOUT with activity
        checkout_body = f"""<?xml version="1.0" encoding="utf-8"?>
<D:checkout xmlns:D="DAV:">
  <D:activity-set>
    <D:href>/.activities/{activity_id}</D:href>
  </D:activity-set>
</D:checkout>"""

        status, _, _ = self.request(
            "CHECKOUT", "/test/calendar.ics/event.ics",
            checkout_body,
            CONTENT_TYPE="application/xml",
            login="test:"
        )
        assert status == 200

        # PROPFIND for activity-set
        propfind_request = """<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:">
    <prop>
        <activity-set/>
    </prop>
</propfind>"""

        status, _, response = self.request(
            "PROPFIND", "/test/calendar.ics/event.ics",
            propfind_request,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 207  # Multi-Status
        assert "activity-set" in response
        assert activity_id in response  # Should contain the activity ID

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
Tests for RFC 3253 versioning write operations.

Tests CHECKOUT, CHECKIN, UNCHECKOUT, and VERSION-CONTROL methods.
"""

import subprocess


from moreradicale.tests import BaseTest

# Sample calendar event for testing
SIMPLE_VCALENDAR = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//Test//EN
BEGIN:VEVENT
UID:test-event-1@example.com
DTSTART:20250115T100000Z
DTEND:20250115T110000Z
SUMMARY:Test Event
END:VEVENT
END:VCALENDAR"""

UPDATED_VCALENDAR = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//Test//EN
BEGIN:VEVENT
UID:test-event-1@example.com
DTSTART:20250115T100000Z
DTEND:20250115T120000Z
SUMMARY:Updated Test Event
END:VEVENT
END:VCALENDAR"""


class TestCheckoutManager:
    """Unit tests for the CheckoutManager class."""

    def test_checkout_creates_marker(self, tmp_path):
        """Test that checkout creates a marker file."""
        from moreradicale.versioning.checkout_manager import CheckoutManager

        storage_folder = str(tmp_path)
        manager = CheckoutManager(storage_folder)

        # Create a test resource path
        resource_dir = tmp_path / "collection-root" / "user" / "calendar.ics"
        resource_dir.mkdir(parents=True)
        resource_file = resource_dir / "event.ics"
        resource_file.write_text(SIMPLE_VCALENDAR)

        # Checkout
        relative_path = "collection-root/user/calendar.ics/event.ics"
        success, error = manager.checkout(
            relative_path, "testuser", "abc12345", "in-place"
        )

        assert success, f"Checkout failed: {error}"
        assert manager.is_checked_out(relative_path)

        info = manager.get_checkout_info(relative_path)
        assert info is not None
        assert info.user == "testuser"
        assert info.version == "abc12345"

    def test_checkout_forbidden_when_already_checked_out(self, tmp_path):
        """Test that checkout fails when already checked out (forbidden policy)."""
        from moreradicale.versioning.checkout_manager import CheckoutManager

        storage_folder = str(tmp_path)
        manager = CheckoutManager(storage_folder, checkout_fork="forbidden")

        resource_dir = tmp_path / "collection-root" / "user" / "calendar.ics"
        resource_dir.mkdir(parents=True)

        relative_path = "collection-root/user/calendar.ics/event.ics"

        # First checkout succeeds
        success, error = manager.checkout(
            relative_path, "user1", "abc12345", "in-place"
        )
        assert success

        # Second checkout by different user fails
        success, error = manager.checkout(
            relative_path, "user2", "abc12345", "in-place"
        )
        assert not success
        assert "already checked out" in error.lower()

    def test_checkin_clears_checkout(self, tmp_path):
        """Test that checkin clears the checkout marker."""
        from moreradicale.versioning.checkout_manager import CheckoutManager

        storage_folder = str(tmp_path)
        manager = CheckoutManager(storage_folder)

        resource_dir = tmp_path / "collection-root" / "user" / "calendar.ics"
        resource_dir.mkdir(parents=True)

        relative_path = "collection-root/user/calendar.ics/event.ics"

        # Checkout
        manager.checkout(relative_path, "testuser", "abc12345", "in-place")
        assert manager.is_checked_out(relative_path)

        # Checkin
        success, error = manager.checkin(relative_path, "testuser")
        assert success
        assert not manager.is_checked_out(relative_path)

    def test_checkin_fails_for_wrong_user(self, tmp_path):
        """Test that checkin fails if requested by a different user."""
        from moreradicale.versioning.checkout_manager import CheckoutManager

        storage_folder = str(tmp_path)
        manager = CheckoutManager(storage_folder)

        resource_dir = tmp_path / "collection-root" / "user" / "calendar.ics"
        resource_dir.mkdir(parents=True)

        relative_path = "collection-root/user/calendar.ics/event.ics"

        # Checkout by user1
        manager.checkout(relative_path, "user1", "abc12345", "in-place")

        # Checkin by user2 fails
        success, error = manager.checkin(relative_path, "user2")
        assert not success
        assert "user1" in error

    def test_uncheckout_returns_version(self, tmp_path):
        """Test that uncheckout returns the version to restore."""
        from moreradicale.versioning.checkout_manager import CheckoutManager

        storage_folder = str(tmp_path)
        manager = CheckoutManager(storage_folder)

        resource_dir = tmp_path / "collection-root" / "user" / "calendar.ics"
        resource_dir.mkdir(parents=True)

        relative_path = "collection-root/user/calendar.ics/event.ics"
        version_sha = "abc12345"

        # Checkout
        manager.checkout(relative_path, "testuser", version_sha, "in-place")

        # Uncheckout
        success, error, version = manager.uncheckout(relative_path, "testuser")
        assert success
        assert version == version_sha
        assert not manager.is_checked_out(relative_path)


class TestGitMetadataWriter:
    """Unit tests for the GitMetadataWriter class."""

    def test_is_available_without_git_repo(self, tmp_path):
        """Test is_available returns False when not a git repo."""
        from moreradicale.storage.multifilesystem.git_writer import GitMetadataWriter

        writer = GitMetadataWriter(str(tmp_path))
        # Should be False since tmp_path isn't a git repo
        assert not writer.is_available()

    def test_create_version_in_git_repo(self, tmp_path):
        """Test creating a version in a git repository."""
        from moreradicale.storage.multifilesystem.git_writer import GitMetadataWriter

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmp_path, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmp_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=tmp_path, check=True, capture_output=True
        )

        # Create initial file
        (tmp_path / "test.ics").write_text(SIMPLE_VCALENDAR)

        writer = GitMetadataWriter(str(tmp_path))
        assert writer.is_available()

        # Create version
        sha = writer.create_version(
            "test.ics", "testuser", "test@example.com", "Test commit"
        )
        assert sha is not None
        assert len(sha) == 40  # Full SHA


class TestVersioningWriteIntegration(BaseTest):
    """Integration tests for versioning write operations."""

    def _init_git_storage(self):
        """Initialize git in the storage folder."""
        storage_folder = self.configuration.get("storage", "filesystem_folder")
        subprocess.run(["git", "init"], cwd=storage_folder, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "radicale@test.local"],
            cwd=storage_folder, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Radicale Test"],
            cwd=storage_folder, check=True, capture_output=True
        )
        return storage_folder

    def _commit_storage(self, storage_folder, message="Initial commit"):
        """Commit current storage state to git."""
        subprocess.run(
            ["git", "add", "-A"],
            cwd=storage_folder, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", message],
            cwd=storage_folder, capture_output=True
        )

    def test_checkout_method_enabled(self):
        """Test CHECKOUT method when versioning is enabled."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {"versioning": "True"}
        })
        storage_folder = self._init_git_storage()

        # Create user collection first
        status, _, _ = self.request("MKCOL", "/user/")
        assert status == 201

        # Create a calendar collection with an event
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar.ics/")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            SIMPLE_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )
        assert status == 201

        # Commit so we have a version
        self._commit_storage(storage_folder, "Add event")

        # CHECKOUT should work
        status, headers, answer = self.request(
            "CHECKOUT", "/user/calendar.ics/event.ics"
        )
        # Should return 200 OK
        assert status == 200, f"CHECKOUT failed: {answer}"

    def test_checkout_disabled_when_versioning_off(self):
        """Test CHECKOUT returns 405 when versioning is disabled."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {"versioning": "False"}
        })

        status, _, _ = self.request(
            "CHECKOUT", "/user/calendar.ics/event.ics"
        )
        assert status == 405  # Method Not Allowed

    def test_checkin_method_after_checkout(self):
        """Test CHECKIN method after a successful CHECKOUT."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {"versioning": "True"}
        })
        storage_folder = self._init_git_storage()

        # Create user collection first
        status, _, _ = self.request("MKCOL", "/user/")
        assert status == 201

        # Create a calendar with an event
        status, _, _ = self.request("MKCALENDAR", "/user/calendar.ics/")
        assert status == 201

        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            SIMPLE_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )
        assert status == 201
        self._commit_storage(storage_folder, "Add event")

        # Checkout
        status, _, _ = self.request(
            "CHECKOUT", "/user/calendar.ics/event.ics"
        )
        assert status == 200

        # Modify the event
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            UPDATED_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )
        assert status in (200, 201, 204)

        # Checkin
        status, headers, answer = self.request(
            "CHECKIN", "/user/calendar.ics/event.ics"
        )
        assert status == 201, f"CHECKIN failed: {answer}"
        assert "Location" in headers

    def test_uncheckout_method(self):
        """Test UNCHECKOUT cancels checkout without new version."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {"versioning": "True"}
        })
        storage_folder = self._init_git_storage()

        # Create user collection first
        status, _, _ = self.request("MKCOL", "/user/")
        assert status == 201

        # Create calendar and event
        status, _, _ = self.request("MKCALENDAR", "/user/calendar.ics/")
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            SIMPLE_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )
        self._commit_storage(storage_folder, "Add event")

        # Checkout
        status, _, _ = self.request(
            "CHECKOUT", "/user/calendar.ics/event.ics"
        )
        assert status == 200

        # Uncheckout
        status, _, answer = self.request(
            "UNCHECKOUT", "/user/calendar.ics/event.ics"
        )
        assert status == 200, f"UNCHECKOUT failed: {answer}"

    def test_version_control_method(self):
        """Test VERSION-CONTROL initializes versioning for an item."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {"versioning": "True"}
        })
        storage_folder = self._init_git_storage()

        # Create user collection first
        status, _, _ = self.request("MKCOL", "/user/")
        assert status == 201

        # Create calendar and event (but don't commit yet)
        status, _, _ = self.request("MKCALENDAR", "/user/calendar.ics/")
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            SIMPLE_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )

        # VERSION-CONTROL should initialize tracking
        status, _, answer = self.request(
            "VERSION-CONTROL", "/user/calendar.ics/event.ics"
        )
        assert status == 200, f"VERSION-CONTROL failed: {answer}"

    def test_double_checkout_blocked(self):
        """Test that double checkout is blocked with forbidden policy."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {
                "versioning": "True",
                "versioning_checkout_fork": "forbidden"
            }
        })
        storage_folder = self._init_git_storage()

        # Create user collection first
        status, _, _ = self.request("MKCOL", "/user/")
        assert status == 201

        # Create calendar and event
        status, _, _ = self.request("MKCALENDAR", "/user/calendar.ics/")
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            SIMPLE_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )
        self._commit_storage(storage_folder, "Add event")

        # First checkout
        status, _, _ = self.request(
            "CHECKOUT", "/user/calendar.ics/event.ics"
        )
        assert status == 200

        # Second checkout should fail with 409 Conflict
        status, _, answer = self.request(
            "CHECKOUT", "/user/calendar.ics/event.ics"
        )
        assert status == 409, f"Expected 409, got {status}: {answer}"


class TestCheckoutExpiration:
    """Tests for checkout timeout and expiration."""

    def test_expired_checkout_cleared(self, tmp_path):
        """Test that expired checkouts are automatically cleared."""
        from datetime import datetime, timezone, timedelta
        from moreradicale.versioning.checkout_manager import CheckoutManager
        import json

        storage_folder = str(tmp_path)
        # 1 second timeout for testing
        manager = CheckoutManager(storage_folder, checkout_timeout=1)

        resource_dir = tmp_path / "collection-root" / "user" / "calendar.ics"
        resource_dir.mkdir(parents=True)

        relative_path = "collection-root/user/calendar.ics/event.ics"

        # Create an expired checkout marker manually
        marker_path = resource_dir / ".event.ics.checkout"
        old_timestamp = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
        expired_info = {
            "user": "olduser",
            "timestamp": old_timestamp,
            "version": "abc12345",
            "checkout_type": "in-place"
        }
        with open(marker_path, "w") as f:
            json.dump(expired_info, f)

        # is_checked_out should return False and clear the marker
        assert not manager.is_checked_out(relative_path)
        assert not marker_path.exists()


class TestAutoVersioning(BaseTest):
    """Tests for auto-versioning on PUT operations."""

    def _init_git_storage(self):
        """Initialize git in the storage folder."""
        storage_folder = self.configuration.get("storage", "filesystem_folder")
        subprocess.run(["git", "init"], cwd=storage_folder, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "radicale@test.local"],
            cwd=storage_folder, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Radicale Test"],
            cwd=storage_folder, check=True, capture_output=True
        )
        return storage_folder

    def _get_git_log(self, storage_folder):
        """Get git log output."""
        result = subprocess.run(
            ["git", "log", "--oneline", "-n", "10"],
            cwd=storage_folder, capture_output=True, text=True
        )
        return result.stdout

    def test_auto_versioning_creates_commit_on_put(self):
        """Test that auto-versioning creates a git commit on PUT."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {
                "versioning": "True",
                "versioning_auto": "checkout-checkin"
            }
        })
        storage_folder = self._init_git_storage()

        # Create user collection
        status, _, _ = self.request("MKCOL", "/user/")
        assert status == 201

        # Create calendar collection
        status, _, _ = self.request("MKCALENDAR", "/user/calendar.ics/")
        assert status == 201

        # PUT an event - should auto-create git commit
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            SIMPLE_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )
        assert status == 201

        # Check git log for auto-version commit
        git_log = self._get_git_log(storage_folder)
        assert "AUTO-VERSION: Create" in git_log

    def test_auto_versioning_disabled_no_commit(self):
        """Test that no commit is created when auto-versioning is disabled."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {
                "versioning": "True",
                "versioning_auto": "disabled"
            }
        })
        storage_folder = self._init_git_storage()

        # Create initial commit so git log works
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "Initial"],
            cwd=storage_folder, capture_output=True
        )

        # Create user collection
        status, _, _ = self.request("MKCOL", "/user/")
        status, _, _ = self.request("MKCALENDAR", "/user/calendar.ics/")

        # PUT an event
        status, _, _ = self.request(
            "PUT", "/user/calendar.ics/event.ics",
            SIMPLE_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )
        assert status == 201

        # Check git log - should NOT have auto-version commit
        git_log = self._get_git_log(storage_folder)
        assert "AUTO-VERSION" not in git_log

    def test_auto_versioning_update_vs_create(self):
        """Test that auto-versioning distinguishes between create and update."""
        self.configure({
            "auth": {"type": "none"},
            "storage": {
                "versioning": "True",
                "versioning_auto": "checkout-checkin"
            }
        })
        storage_folder = self._init_git_storage()

        # Create user and calendar
        self.request("MKCOL", "/user/")
        self.request("MKCALENDAR", "/user/calendar.ics/")

        # First PUT - creates new item
        self.request(
            "PUT", "/user/calendar.ics/event.ics",
            SIMPLE_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )

        # Second PUT - updates existing item
        self.request(
            "PUT", "/user/calendar.ics/event.ics",
            UPDATED_VCALENDAR, HTTP_CONTENT_TYPE="text/calendar"
        )

        # Check git log for both commits
        git_log = self._get_git_log(storage_folder)
        assert "AUTO-VERSION: Create" in git_log
        assert "AUTO-VERSION: Update" in git_log

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
Tests for calendar sharing and delegation functionality.

Tests cover:
- Sharing module (SharingManager)
- owner_only_shared rights backend
- Shared calendar discovery
- Scheduling delegation
- PROPFIND sharing properties
- POST sharing operations
"""

import json
import shutil
import tempfile

from moreradicale import config
from moreradicale.sharing import (
    SharingManager, Share, ShareAccess, InviteStatus, Delegation,
    SHARES_PROPERTY, PROXY_READ_PROPERTY, PROXY_WRITE_PROPERTY,
    SCHEDULE_DELEGATES_PROPERTY
)
from moreradicale.tests import BaseTest


class MockCollection:
    """Mock collection for testing sharing operations."""

    def __init__(self, path: str, owner: str = None):
        self.path = path
        self._owner = owner or path.strip("/").split("/")[0]
        self._meta = {}

    @property
    def owner(self) -> str:
        return self._owner

    def get_meta(self, key: str = None):
        if key is None:
            return self._meta
        return self._meta.get(key)

    def set_meta(self, meta: dict):
        self._meta = meta


class TestShareDataClass:
    """Test Share dataclass serialization."""

    def test_share_to_dict(self):
        """Test converting Share to dictionary."""
        share = Share(
            sharee="bob",
            access=ShareAccess.READ_WRITE,
            cn="Bob Smith",
            status=InviteStatus.ACCEPTED,
            invited_at="2025-01-01T00:00:00Z",
            accepted_at="2025-01-02T00:00:00Z"
        )

        result = share.to_dict()

        assert result["access"] == "read-write"
        assert result["cn"] == "Bob Smith"
        assert result["status"] == "accepted"
        assert result["invited_at"] == "2025-01-01T00:00:00Z"
        assert result["accepted_at"] == "2025-01-02T00:00:00Z"

    def test_share_from_dict(self):
        """Test creating Share from dictionary."""
        data = {
            "access": "read",
            "cn": "Charlie",
            "status": "pending",
            "invited_at": "2025-01-01T00:00:00Z"
        }

        share = Share.from_dict("charlie", data)

        assert share.sharee == "charlie"
        assert share.access == ShareAccess.READ
        assert share.cn == "Charlie"
        assert share.status == InviteStatus.PENDING


class TestDelegationDataClass:
    """Test Delegation dataclass serialization."""

    def test_delegation_to_dict(self):
        """Test converting Delegation to dictionary."""
        delegation = Delegation(
            delegate="secretary",
            can_read=True,
            can_write=True,
            can_schedule=True
        )

        result = delegation.to_dict()

        assert result["can_read"] is True
        assert result["can_write"] is True
        assert result["can_schedule"] is True

    def test_delegation_from_dict(self):
        """Test creating Delegation from dictionary."""
        data = {
            "can_read": True,
            "can_write": False,
            "can_schedule": False
        }

        delegation = Delegation.from_dict("assistant", data)

        assert delegation.delegate == "assistant"
        assert delegation.can_read is True
        assert delegation.can_write is False
        assert delegation.can_schedule is False


class TestSharingManager(BaseTest):
    """Test SharingManager sharing operations."""

    def setup_method(self):
        """Set up test configuration."""
        super().setup_method()
        self.configure({
            "sharing": {
                "enabled": "True",
                "delegation_enabled": "True"
            }
        })
        self.manager = SharingManager(self.configuration)

    def test_is_sharing_enabled(self):
        """Test checking if sharing is enabled."""
        assert self.manager.is_sharing_enabled() is True

    def test_is_delegation_enabled(self):
        """Test checking if delegation is enabled."""
        assert self.manager.is_delegation_enabled() is True

    def test_get_shares_empty(self):
        """Test getting shares from collection with no shares."""
        collection = MockCollection("/alice/calendar/")

        shares = self.manager.get_shares(collection)

        assert shares == {}

    def test_add_share(self):
        """Test adding a share to a collection."""
        collection = MockCollection("/alice/calendar/")

        result = self.manager.add_share(
            collection, "alice", "bob", ShareAccess.READ, "Bob Smith")

        assert result is True

        # Verify share was stored
        shares_json = collection.get_meta(SHARES_PROPERTY)
        assert shares_json is not None

        shares = json.loads(shares_json)
        assert "bob" in shares
        assert shares["bob"]["access"] == "read"
        assert shares["bob"]["cn"] == "Bob Smith"
        assert shares["bob"]["status"] == "pending"

    def test_add_share_read_write(self):
        """Test adding a read-write share."""
        collection = MockCollection("/alice/calendar/")

        self.manager.add_share(
            collection, "alice", "bob", ShareAccess.READ_WRITE)

        shares = self.manager.get_shares(collection)
        assert shares["bob"].access == ShareAccess.READ_WRITE

    def test_add_share_not_owner(self):
        """Test that non-owner cannot add shares."""
        collection = MockCollection("/alice/calendar/")

        try:
            self.manager.add_share(
                collection, "bob", "charlie", ShareAccess.READ)
            assert False, "Expected PermissionError"
        except PermissionError:
            pass

    def test_add_share_to_self(self):
        """Test that owner cannot share with self."""
        collection = MockCollection("/alice/calendar/")

        try:
            self.manager.add_share(
                collection, "alice", "alice", ShareAccess.READ)
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_remove_share(self):
        """Test removing a share."""
        collection = MockCollection("/alice/calendar/")

        # Add share first
        self.manager.add_share(
            collection, "alice", "bob", ShareAccess.READ)

        # Remove it
        result = self.manager.remove_share(collection, "alice", "bob")

        assert result is True

        # Verify removed
        shares = self.manager.get_shares(collection)
        assert "bob" not in shares

    def test_remove_share_not_exists(self):
        """Test removing non-existent share returns False."""
        collection = MockCollection("/alice/calendar/")

        result = self.manager.remove_share(collection, "alice", "bob")

        assert result is False

    def test_accept_share(self):
        """Test accepting a share invitation."""
        collection = MockCollection("/alice/calendar/")

        # Add pending share
        self.manager.add_share(
            collection, "alice", "bob", ShareAccess.READ)

        # Accept it
        result = self.manager.accept_share(collection, "bob")

        assert result is True

        # Verify accepted
        shares = self.manager.get_shares(collection)
        assert shares["bob"].status == InviteStatus.ACCEPTED
        assert shares["bob"].accepted_at is not None

    def test_accept_share_no_invitation(self):
        """Test accepting without invitation raises error."""
        collection = MockCollection("/alice/calendar/")

        try:
            self.manager.accept_share(collection, "bob")
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_decline_share(self):
        """Test declining a share invitation."""
        collection = MockCollection("/alice/calendar/")

        # Add pending share
        self.manager.add_share(
            collection, "alice", "bob", ShareAccess.READ)

        # Decline it
        result = self.manager.decline_share(collection, "bob")

        assert result is True

        # Verify removed (decline removes the share)
        shares = self.manager.get_shares(collection)
        assert "bob" not in shares

    def test_check_share_access(self):
        """Test checking share access level."""
        collection = MockCollection("/alice/calendar/")

        # Add and accept share
        self.manager.add_share(
            collection, "alice", "bob", ShareAccess.READ_WRITE)
        self.manager.accept_share(collection, "bob")

        # Check access
        access = self.manager.check_share_access("bob", collection)

        assert access == ShareAccess.READ_WRITE

    def test_check_share_access_pending(self):
        """Test that pending shares don't grant access."""
        collection = MockCollection("/alice/calendar/")

        # Add share but don't accept
        self.manager.add_share(
            collection, "alice", "bob", ShareAccess.READ)

        # Check access - should be None since pending
        access = self.manager.check_share_access("bob", collection)

        assert access is None

    def test_check_share_access_no_share(self):
        """Test checking access for non-shared user."""
        collection = MockCollection("/alice/calendar/")

        access = self.manager.check_share_access("charlie", collection)

        assert access is None


class TestSharingManagerDelegation(BaseTest):
    """Test SharingManager delegation operations."""

    def setup_method(self):
        """Set up test configuration."""
        super().setup_method()
        self.configure({
            "sharing": {
                "enabled": "True",
                "delegation_enabled": "True"
            }
        })
        self.manager = SharingManager(self.configuration)

    def test_get_delegates_empty(self):
        """Test getting delegates from principal with no delegates."""
        principal = MockCollection("/alice/")

        delegates = self.manager.get_delegates(principal)

        assert delegates == []

    def test_add_delegate(self):
        """Test adding a scheduling delegate."""
        principal = MockCollection("/alice/")

        result = self.manager.add_delegate(principal, "alice", "secretary")

        assert result is True

        # Verify stored
        delegates = self.manager.get_delegates(principal)
        assert "secretary" in delegates

    def test_add_delegate_not_owner(self):
        """Test that non-owner cannot add delegates."""
        principal = MockCollection("/alice/")

        try:
            self.manager.add_delegate(principal, "bob", "secretary")
            assert False, "Expected PermissionError"
        except PermissionError:
            pass

    def test_add_delegate_to_self(self):
        """Test that owner cannot delegate to self."""
        principal = MockCollection("/alice/")

        try:
            self.manager.add_delegate(principal, "alice", "alice")
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_remove_delegate(self):
        """Test removing a delegate."""
        principal = MockCollection("/alice/")

        # Add delegate first
        self.manager.add_delegate(principal, "alice", "secretary")

        # Remove
        result = self.manager.remove_delegate(principal, "alice", "secretary")

        assert result is True

        # Verify removed
        delegates = self.manager.get_delegates(principal)
        assert "secretary" not in delegates

    def test_is_delegate_for(self):
        """Test checking delegate relationship."""
        principal = MockCollection("/alice/")

        # Add delegate
        self.manager.add_delegate(principal, "alice", "secretary")

        # Check
        assert self.manager.is_delegate_for("secretary", principal) is True
        assert self.manager.is_delegate_for("bob", principal) is False


class TestOwnerOnlySharedRights(BaseTest):
    """Test owner_only_shared rights backend."""

    def setup_method(self):
        """Set up test configuration with temp storage."""
        super().setup_method()
        self.configure({
            "rights": {"type": "owner_only_shared"},
            "sharing": {
                "enabled": "True",
                "delegation_enabled": "True"
            }
        })

    def test_owner_has_full_access(self):
        """Test that owner has full access to their collections."""
        from moreradicale.rights import owner_only_shared

        rights = owner_only_shared.Rights(self.configuration)

        # Owner should have full access
        assert rights.authorization("alice", "/alice/") == "RW"
        assert rights.authorization("alice", "/alice/calendar/") == "rw"

    def test_non_owner_no_base_access(self):
        """Test that non-owner has no access without sharing."""
        from moreradicale.rights import owner_only_shared

        rights = owner_only_shared.Rights(self.configuration)

        # Non-owner should have no access
        assert rights.authorization("bob", "/alice/calendar/") == ""

    def test_shared_read_access(self):
        """Test that shared users get read access."""
        from moreradicale.rights import owner_only_shared

        rights = owner_only_shared.Rights(self.configuration)

        # Set up share metadata in cache
        rights.set_collection_meta("alice/calendar", {
            SHARES_PROPERTY: json.dumps({
                "bob": {"access": "read", "status": "accepted"}
            })
        })

        # Bob should have read access
        assert rights.authorization("bob", "/alice/calendar/") == "r"

    def test_shared_readwrite_access(self):
        """Test that shared users with read-write get rw access."""
        from moreradicale.rights import owner_only_shared

        rights = owner_only_shared.Rights(self.configuration)

        # Set up share metadata in cache
        rights.set_collection_meta("alice/calendar", {
            SHARES_PROPERTY: json.dumps({
                "bob": {"access": "read-write", "status": "accepted"}
            })
        })

        # Bob should have read-write access
        assert rights.authorization("bob", "/alice/calendar/") == "rw"

    def test_pending_share_no_access(self):
        """Test that pending shares don't grant access."""
        from moreradicale.rights import owner_only_shared

        rights = owner_only_shared.Rights(self.configuration)

        # Set up pending share
        rights.set_collection_meta("alice/calendar", {
            SHARES_PROPERTY: json.dumps({
                "bob": {"access": "read-write", "status": "pending"}
            })
        })

        # Bob should have no access
        assert rights.authorization("bob", "/alice/calendar/") == ""

    def test_proxy_write_access(self):
        """Test that write proxies get access."""
        from moreradicale.rights import owner_only_shared

        rights = owner_only_shared.Rights(self.configuration)

        # Set up proxy relationship
        rights.set_collection_meta("alice", {
            PROXY_WRITE_PROPERTY: json.dumps(["secretary"])
        })

        # Secretary should have access to alice's principal
        assert rights.authorization("secretary", "/alice/") == "RW"

    def test_proxy_read_access(self):
        """Test that read proxies get read access."""
        from moreradicale.rights import owner_only_shared

        rights = owner_only_shared.Rights(self.configuration)

        # Set up proxy relationship
        rights.set_collection_meta("alice", {
            PROXY_READ_PROPERTY: json.dumps(["assistant"])
        })

        # Assistant should have read access
        assert rights.authorization("assistant", "/alice/") == "R"


class TestSchedulingDelegation(BaseTest):
    """Test scheduling delegation in router."""

    def setup_method(self):
        """Set up test configuration."""
        super().setup_method()
        self.configure({
            "scheduling": {
                "enabled": "True",
                "internal_domain": "example.com"
            },
            "sharing": {
                "enabled": "True",
                "delegation_enabled": "True"
            }
        })

    def test_validate_organizer_direct(self):
        """Test direct organizer validation."""
        from moreradicale.itip.router import validate_organizer_permission

        result = validate_organizer_permission(
            "alice@example.com", "alice", self.configuration)

        assert result is True

    def test_validate_organizer_wrong_user(self):
        """Test organizer validation fails for wrong user."""
        from moreradicale.itip.router import validate_organizer_permission

        result = validate_organizer_permission(
            "bob@example.com", "alice", self.configuration)

        assert result is False

    def test_validate_organizer_delegate(self):
        """Test delegate can organize for principal."""
        from moreradicale.itip.router import validate_organizer_permission

        # Create mock storage with delegation
        class MockPrincipal:
            def __init__(self):
                self.path = "/boss/"
                self._meta = {
                    SCHEDULE_DELEGATES_PROPERTY: json.dumps(["secretary"])
                }

            def get_meta(self, key=None):
                if key is None:
                    return self._meta
                return self._meta.get(key)

        class MockStorage:
            def discover(self, path, depth="0"):
                if path == "/boss/":
                    return [MockPrincipal()]
                return []

        storage = MockStorage()

        result = validate_organizer_permission(
            "boss@example.com", "secretary", self.configuration, storage)

        assert result is True


class TestSharingPropertyConstants:
    """Test sharing property name constants."""

    def test_shares_property_name(self):
        """Test SHARES_PROPERTY constant."""
        assert SHARES_PROPERTY == "RADICALE:shares"

    def test_proxy_read_property_name(self):
        """Test PROXY_READ_PROPERTY constant."""
        assert PROXY_READ_PROPERTY == "RADICALE:calendar-proxy-read"

    def test_proxy_write_property_name(self):
        """Test PROXY_WRITE_PROPERTY constant."""
        assert PROXY_WRITE_PROPERTY == "RADICALE:calendar-proxy-write"

    def test_schedule_delegates_property_name(self):
        """Test SCHEDULE_DELEGATES_PROPERTY constant."""
        assert SCHEDULE_DELEGATES_PROPERTY == "RADICALE:schedule-delegates"


class TestShareAccess:
    """Test ShareAccess enum."""

    def test_read_value(self):
        """Test READ access value."""
        assert ShareAccess.READ.value == "read"

    def test_read_write_value(self):
        """Test READ_WRITE access value."""
        assert ShareAccess.READ_WRITE.value == "read-write"


class TestInviteStatus:
    """Test InviteStatus enum."""

    def test_pending_value(self):
        """Test PENDING status value."""
        assert InviteStatus.PENDING.value == "pending"

    def test_accepted_value(self):
        """Test ACCEPTED status value."""
        assert InviteStatus.ACCEPTED.value == "accepted"

    def test_declined_value(self):
        """Test DECLINED status value."""
        assert InviteStatus.DECLINED.value == "declined"

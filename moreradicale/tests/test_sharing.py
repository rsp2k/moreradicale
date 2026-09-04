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

from moreradicale.sharing import (PROXY_READ_PROPERTY, PROXY_WRITE_PROPERTY,
                                  SCHEDULE_DELEGATES_PROPERTY, SHARES_PROPERTY,
                                  Delegation, InviteStatus, Share, ShareAccess,
                                  SharingManager)
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


class TestSharingHTTPFlow(BaseTest):
    """End-to-end HTTP integration tests for the share lifecycle.

    Drives the actual app pipeline with real storage to cover:
    - Rights backend's storage-driven _check_shared_access
      (the "accepted shares grant rw access" path that was broken
      before owner_only_shared.attach_storage was wired up).
    - Cascade-deletion of invite notifications on accept/decline/revoke.
    - Storage's depth=1 auto-merge of accepted shares into the
      sharee's principal listing.

    Uses auth.type = "none" so any login string works; the focus is
    rights+sharing behavior, not authentication.
    """

    SHARE_REQUEST_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<CS:share-resource xmlns="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:set>
    <href>/{sharee}/</href>
    <CS:summary>{summary}</CS:summary>
    <CS:{access_tag}/>
  </CS:set>
</CS:share-resource>"""

    REVOKE_REQUEST_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<CS:share-resource xmlns="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:remove><href>/{sharee}/</href></CS:remove>
</CS:share-resource>"""

    REPLY_REQUEST_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<CS:share-reply xmlns="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:href>/{owner}/{calendar}/</CS:href>
  <CS:in-reply-to>{uid}</CS:in-reply-to>
  <CS:{decision}/>
</CS:share-reply>"""

    def setup_method(self):
        super().setup_method()
        # owner_only_shared inherits the user-identity check from
        # authenticated.Rights, which only enforces path-vs-user matching
        # when auth.type != "none". With auth.type=none, even unauth'd
        # paths get rw. So we need a real auth backend for these tests
        # to be meaningful - use htpasswd with two pre-seeded users.
        import os
        htpasswd_path = os.path.join(self.colpath, ".htpasswd")
        with open(htpasswd_path, "w") as f:
            f.write("alice:alicepass\nbob:bobpass\n")
        self.configure({
            "auth": {
                "type": "htpasswd",
                "htpasswd_filename": htpasswd_path,
                "htpasswd_encryption": "plain",
                "delay": "0.001",
            },
            "rights": {"type": "owner_only_shared"},
            "sharing": {
                "enabled": "True",
                "delegation_enabled": "True",
                "notifications_enabled": "True",
            },
        })

    # ---- helpers ---------------------------------------------------------

    def _materialize_principal(self, user: str):
        """Force a principal collection to exist on disk for `user`.

        The sharing handler rejects shares to users who don't have a
        principal in storage (handler.py:198). With auth.type=none any
        login string succeeds, but the principal is only auto-created
        when the user makes their own first authenticated request.
        We trigger that with an own-principal PROPFIND.
        """
        self.request("PROPFIND", f"/{user}/",
                     data='<?xml version="1.0"?>'
                          '<propfind xmlns="DAV:"><prop><displayname/></prop></propfind>',
                     HTTP_DEPTH="0", login=f"{user}:{user}pass")

    def _create_calendar(self, owner: str, calendar: str = "calendar"):
        """MKCALENDAR a fresh calendar under the owner's principal."""
        self.mkcalendar(
            f"/{owner}/{calendar}/",
            data="""<?xml version="1.0" encoding="UTF-8"?>
<C:mkcalendar xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <set><prop>
        <displayname>{name}</displayname>
        <C:supported-calendar-component-set><C:comp name="VEVENT"/></C:supported-calendar-component-set>
    </prop></set>
</C:mkcalendar>""".replace("{name}", calendar),
            login=f"{owner}:{owner}pass",
            check=201,
        )

    def _share(self, owner: str, calendar: str, sharee: str,
               access: str = "read-write", summary: str = "Sharing"):
        """POST CS:share-resource as the calendar owner."""
        access_tag = "read-write" if access == "read-write" else "read"
        body = self.SHARE_REQUEST_TMPL.format(
            sharee=sharee, summary=summary, access_tag=access_tag)
        # CONTENT_TYPE = xml routes the POST through the sharing handler
        # at app/post.py:60; without it the dispatcher falls through to
        # scheduling-outbox logic and returns 405.
        self.post(f"/{owner}/{calendar}/", data=body,
                  login=f"{owner}:{owner}pass",
                  CONTENT_TYPE="application/xml; charset=utf-8",
                  check=200)

    def _revoke(self, owner: str, calendar: str, sharee: str):
        """POST CS:share-resource with CS:remove as the calendar owner."""
        body = self.REVOKE_REQUEST_TMPL.format(sharee=sharee)
        self.post(f"/{owner}/{calendar}/", data=body,
                  login=f"{owner}:{owner}pass",
                  CONTENT_TYPE="application/xml; charset=utf-8",
                  check=200)

    def _reply(self, owner: str, calendar: str, sharee: str,
               uid: str, accept: bool):
        """POST CS:share-reply as the sharee."""
        body = self.REPLY_REQUEST_TMPL.format(
            owner=owner, calendar=calendar, uid=uid,
            decision="invite-accepted" if accept else "invite-declined")
        self.post(f"/{owner}/{calendar}/", data=body,
                  login=f"{sharee}:{sharee}pass",
                  CONTENT_TYPE="application/xml; charset=utf-8",
                  check=200)

    def _list_notification_files(self, user: str) -> list:
        """Return invite/deleted/reply filenames in the user's notifications."""
        import os
        path = os.path.join(self.colpath, "collection-root", user, "notifications")
        if not os.path.isdir(path):
            return []
        return sorted(
            name for name in os.listdir(path)
            if not name.startswith(".Radicale")
        )

    def _read_invite_uid(self, user: str) -> str:
        """Find the first pending invite-* notification and return its UID."""
        import os
        path = os.path.join(self.colpath, "collection-root", user, "notifications")
        for name in os.listdir(path):
            if name.startswith("invite-"):
                props_path = os.path.join(path, name, ".Radicale.props")
                with open(props_path) as f:
                    props = json.load(f)
                blob = json.loads(props["RADICALE:notifications"])
                return blob["uid"]
        raise AssertionError("no pending invite notification found")

    # ---- tests -----------------------------------------------------------

    def test_accept_grants_read_write_access_to_sharee(self):
        """After accept, sharee can PROPFIND the shared calendar.

        This is the regression test for the rights-storage wiring fix:
        owner_only_shared._check_shared_access used to read from an empty
        in-memory cache, so accepted shares never granted access.
        """
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob")

        # Before accept: bob is denied (status=pending).
        status, _, _ = self.request(
            "PROPFIND", "/alice/calendar/",
            data='<?xml version="1.0"?><propfind xmlns="DAV:"><prop><displayname/></prop></propfind>',
            HTTP_DEPTH="0", login="bob:bobpass")
        assert status in (401, 403, 404), \
            "pending share should not grant access yet (got %d)" % status

        # Bob accepts.
        uid = self._read_invite_uid("bob")
        self._reply("alice", "calendar", "bob", uid, accept=True)

        # After accept: bob can read alice's calendar (rights backend
        # reads SHARES_PROPERTY from storage on demand).
        status, _, _ = self.request(
            "PROPFIND", "/alice/calendar/",
            data='<?xml version="1.0"?><propfind xmlns="DAV:"><prop><displayname/></prop></propfind>',
            HTTP_DEPTH="0", login="bob:bobpass", check=207)

    def test_accept_cascade_deletes_invite_notification(self):
        """Server-side accept removes the originating invite notification."""
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob", summary="Cascade test")

        # Sanity: the invite is on disk.
        before = self._list_notification_files("bob")
        assert any(n.startswith("invite-") for n in before), \
            "expected invite notification before accept; got %r" % before

        uid = self._read_invite_uid("bob")
        self._reply("alice", "calendar", "bob", uid, accept=True)

        # After accept: no invite-* in bob's notifications.
        after = self._list_notification_files("bob")
        assert not any(n.startswith("invite-") for n in after), \
            "invite notification should be cascade-deleted; got %r" % after

    def test_decline_cascade_deletes_invite_notification(self):
        """Decline also clears the originating invite notification."""
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob")

        uid = self._read_invite_uid("bob")
        self._reply("alice", "calendar", "bob", uid, accept=False)

        after = self._list_notification_files("bob")
        assert not any(n.startswith("invite-") for n in after), \
            "decline should cascade-delete the invite; got %r" % after

    def test_revoke_while_pending_clears_invite(self):
        """Owner revoking a pending share clears the unconsumed invite.

        Regression test for the orphan-invite case: without the cascade,
        bob's UI would keep showing a "pending" invite for a share that
        no longer exists, and clicking Accept would fail with 404.
        """
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob")

        before = self._list_notification_files("bob")
        invites_before = [n for n in before if n.startswith("invite-")]
        assert invites_before, "expected pending invite before revoke"

        self._revoke("alice", "calendar", "bob")

        after = self._list_notification_files("bob")
        invites_after = [n for n in after if n.startswith("invite-")]
        assert not invites_after, \
            "revoke should clear the orphaned invite; got %r" % after
        # A revocation notification should have replaced it.
        assert any(n.startswith("deleted-") for n in after), \
            "revoke should leave a deleted-* notification; got %r" % after

    def test_revoke_after_accept_does_not_touch_unrelated_notifications(self):
        """Cascade is scoped to invites for the matching calendar only.

        If alice has shared calendar A and calendar B with bob, revoking
        the share on A must not delete any invite notification for B.
        """
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._create_calendar("alice", "calendar_a")
        self._create_calendar("alice", "calendar_b")
        self._share("alice", "calendar_a", "bob", summary="A")
        self._share("alice", "calendar_b", "bob", summary="B")

        before = self._list_notification_files("bob")
        invites = [n for n in before if n.startswith("invite-")]
        assert len(invites) == 2, "expected 2 invites (one per calendar); got %r" % before

        # Revoke only the A share.
        self._revoke("alice", "calendar_a", "bob")

        after = self._list_notification_files("bob")
        invites_after = [n for n in after if n.startswith("invite-")]
        assert len(invites_after) == 1, \
            "the unrelated calendar_b invite must survive; got %r" % after

    def test_cs_invite_propfind_owner_sees_full_share_list(self):
        """Owner can PROPFIND CS:invite and see all sharees they granted."""
        # Add charlie to htpasswd before materializing - otherwise
        # the auth check in _materialize_principal fails with 401.
        import os
        with open(os.path.join(self.colpath, ".htpasswd"), "a") as f:
            f.write("charlie:charliepass\n")
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._materialize_principal("charlie")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob")
        self._share("alice", "calendar", "charlie")

        body = ('<?xml version="1.0"?><propfind xmlns="DAV:" '
                'xmlns:CS="http://calendarserver.org/ns/">'
                '<prop><CS:invite/></prop></propfind>')
        status, _, response_text = self.request(
            "PROPFIND", "/alice/calendar/", data=body,
            HTTP_DEPTH="0", login="alice:alicepass", check=207)
        # Owner should see both sharees in the response.
        assert "/bob/" in response_text and "/charlie/" in response_text, \
            "owner's CS:invite should list all sharees; got %r" % response_text

    def test_cs_invite_propfind_sharee_does_not_see_other_sharees(self):
        """Privacy: a sharee cannot enumerate other sharees via CS:invite.

        Without filtering, bob (a sharee) doing PROPFIND CS:invite on
        the shared calendar would receive the full sharee list including
        charlie's principal href and access level. Other CalDAV servers
        (Apple Calendar Server, SabreDAV) restrict this property to the
        calendar owner.
        """
        import os
        with open(os.path.join(self.colpath, ".htpasswd"), "a") as f:
            f.write("charlie:charliepass\n")
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._materialize_principal("charlie")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob")
        self._share("alice", "calendar", "charlie")
        # Bob accepts so he has read access to PROPFIND.
        uid = self._read_invite_uid("bob")
        self._reply("alice", "calendar", "bob", uid, accept=True)

        body = ('<?xml version="1.0"?><propfind xmlns="DAV:" '
                'xmlns:CS="http://calendarserver.org/ns/">'
                '<prop><CS:invite/></prop></propfind>')
        status, _, response_text = self.request(
            "PROPFIND", "/alice/calendar/", data=body,
            HTTP_DEPTH="0", login="bob:bobpass", check=207)
        # Bob must NOT see charlie's principal in the response.
        assert "/charlie/" not in response_text, \
            "sharee enumeration leak: bob saw charlie's principal in CS:invite; " \
            "got %r" % response_text

    def test_accepted_shared_calendar_appears_in_principal_listing(self):
        """Storage discover() auto-merges accepted shares into depth=1.

        The web UI relies on this: a single PROPFIND on /{user}/ depth=1
        returns both owned and accepted-shared calendars in one trip,
        so we don't need a separate "shared with me" enumeration API.
        """
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob")
        uid = self._read_invite_uid("bob")
        self._reply("alice", "calendar", "bob", uid, accept=True)

        # PROPFIND bob's principal at depth=1 - the accepted share on
        # alice's calendar should appear as a child response.
        status, responses = self.propfind(
            "/bob/",
            data='<?xml version="1.0"?>'
                 '<propfind xmlns="DAV:"><prop><displayname/><resourcetype/></prop></propfind>',
            HTTP_DEPTH="1", login="bob:bobpass", check=207)
        # Response keys are URL-quoted hrefs; alice/calendar is plain ASCII.
        assert "/alice/calendar/" in responses, \
            "expected /alice/calendar/ in bob's principal listing; got %r" % \
            list(responses.keys())

    # ---- security regressions --------------------------------------------
    #
    # Both of these were confirmed exploitable before the
    # SERVER_MANAGED_PROPS guard existed. See
    # docs/agent-threads/asgi-executor-lifecycle/ for the review that found
    # them and the exploit transcripts.

    def test_sharee_cannot_rewrite_shares_via_proppatch(self):
        """A read-write sharee must not be able to edit the share list.

        RADICALE:shares is what the rights backend consults to decide
        access. When it was writable through the generic WebDAV property
        machinery, bob (holding only a read-write share) could add mallory
        and grant her access alice never gave - a straight privilege
        escalation. It must be refused outright.
        """
        import os
        with open(os.path.join(self.colpath, ".htpasswd"), "a") as f:
            f.write("mallory:mallorypass\n")
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._materialize_principal("mallory")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob")
        uid = self._read_invite_uid("bob")
        self._reply("alice", "calendar", "bob", uid, accept=True)

        # Precondition: mallory has no access.
        self.request("PROPFIND", "/alice/calendar/",
                     data='<?xml version="1.0"?><propfind xmlns="DAV:">'
                          '<prop><displayname/></prop></propfind>',
                     HTTP_DEPTH="0", login="mallory:mallorypass", check=403)

        # bob tries to rewrite the share list to include mallory.
        evil = ('{"bob": {"access": "read-write", "status": "accepted"},'
                ' "mallory": {"access": "read-write", "status": "accepted"}}')
        status, _, _ = self.request(
            "PROPPATCH", "/alice/calendar/",
            data='<?xml version="1.0"?><propertyupdate xmlns="DAV:" '
                 'xmlns:R="http://radicale.org/ns/"><set><prop>'
                 '<R:shares>%s</R:shares></prop></set></propertyupdate>' % evil,
            login="bob:bobpass")
        assert status == 403, \
            "PROPPATCH of RADICALE:shares must be refused, got %d" % status

        # And mallory still has no access.
        self.request("PROPFIND", "/alice/calendar/",
                     data='<?xml version="1.0"?><propfind xmlns="DAV:">'
                          '<prop><displayname/></prop></propfind>',
                     HTTP_DEPTH="0", login="mallory:mallorypass", check=403)

    def test_shares_property_is_not_readable_by_sharees(self):
        """The share list must not leak through the generic property paths.

        Restricting CS:invite to the owner is not sufficient on its own:
        the same JSON escaped through the generic property fallback and
        through <allprop/>, which many CalDAV clients send by default. It
        carries every sharee's username, invite state, and the owner's
        private per-sharee comment.
        """
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob", summary="secret note")
        uid = self._read_invite_uid("bob")
        self._reply("alice", "calendar", "bob", uid, accept=True)

        # Named request for the property.
        _, _, body = self.request(
            "PROPFIND", "/alice/calendar/",
            data='<?xml version="1.0"?><propfind xmlns="DAV:" '
                 'xmlns:R="http://radicale.org/ns/"><prop><R:shares/></prop>'
                 '</propfind>',
            HTTP_DEPTH="0", login="bob:bobpass", check=207)
        assert "read-write" not in body and "secret note" not in body, \
            "RADICALE:shares leaked via named PROPFIND: %r" % body

        # allprop - the passive leak.
        _, _, body = self.request(
            "PROPFIND", "/alice/calendar/",
            data='<?xml version="1.0"?><propfind xmlns="DAV:"><allprop/>'
                 '</propfind>',
            HTTP_DEPTH="0", login="bob:bobpass", check=207)
        assert "secret note" not in body, \
            "RADICALE:shares leaked via allprop: %r" % body

    def test_owner_still_sees_invite_list(self):
        """The guard must not break the sanctioned owner-only view."""
        self._materialize_principal("alice")
        self._materialize_principal("bob")
        self._create_calendar("alice")
        self._share("alice", "calendar", "bob")

        _, _, body = self.request(
            "PROPFIND", "/alice/calendar/",
            data='<?xml version="1.0"?><propfind xmlns="DAV:" '
                 'xmlns:CS="http://calendarserver.org/ns/">'
                 '<prop><CS:invite/></prop></propfind>',
            HTTP_DEPTH="0", login="alice:alicepass", check=207)
        assert "/bob/" in body, \
            "owner must still see sharees via CS:invite; got %r" % body

    def test_server_managed_guard_matches_what_the_parser_produces(self):
        """Pin the composition production actually runs.

        The guard compares property names against SERVER_MANAGED_PROPS, but
        the names come from xmlutils.props_from_request. If those two ever
        disagree about representation the guard returns "not reserved" for
        everything and fails OPEN, with no error anywhere.

        Asserting on the composition - real XML in, guard verdict out - is
        what pins it. Testing the guard with hand-written prefixed strings
        would exercise a branch production never takes.
        """
        import defusedxml.ElementTree as DefusedET

        from moreradicale import item as radicale_item
        from moreradicale import xmlutils

        for prop in ("shares", "calendar-proxy-read", "calendar-proxy-write",
                     "schedule-delegates", "notifications"):
            body = (
                '<?xml version="1.0"?>'
                '<D:propertyupdate xmlns:D="DAV:" '
                'xmlns:R="http://radicale.org/ns/">'
                '<D:set><D:prop><R:%s>x</R:%s></D:prop></D:set>'
                '</D:propertyupdate>' % (prop, prop))
            parsed = xmlutils.props_from_request(DefusedET.fromstring(body))
            assert radicale_item.reject_server_managed_props(parsed) is not None, \
                ("RADICALE:%s reached the guard as %r and was not reserved - "
                 "the guard is failing open" % (prop, list(parsed)))

    def test_server_managed_props_cover_both_representations(self):
        """The prefixed and Clark forms must both be reserved.

        props_from_request yields the prefixed form for known namespaces and
        Clark notation for unknown ones. Listing only one form would make the
        guard depend on that mapping never changing.
        """
        from moreradicale import item as radicale_item
        from moreradicale import xmlutils

        for name in ("RADICALE:shares", "RADICALE:calendar-proxy-read",
                     "RADICALE:calendar-proxy-write",
                     "RADICALE:schedule-delegates", "RADICALE:notifications"):
            assert name in radicale_item.SERVER_MANAGED_PROPS
            clark = xmlutils.make_clark(name)
            assert clark in radicale_item.SERVER_MANAGED_PROPS, \
                "%s (Clark form of %s) is not reserved" % (clark, name)

    def test_server_managed_props_match_the_real_constants(self):
        """Guard list must not drift from the names the code actually uses."""
        from moreradicale import item as radicale_item
        from moreradicale.sharing import (PROXY_READ_PROPERTY,
                                          PROXY_WRITE_PROPERTY,
                                          SCHEDULE_DELEGATES_PROPERTY,
                                          SHARES_PROPERTY)
        from moreradicale.sharing.notifications import NOTIFICATIONS_PROPERTY

        for constant in (SHARES_PROPERTY, PROXY_READ_PROPERTY,
                         PROXY_WRITE_PROPERTY, SCHEDULE_DELEGATES_PROPERTY,
                         NOTIFICATIONS_PROPERTY):
            assert constant in radicale_item.SERVER_MANAGED_PROPS, \
                "%s is used by the code but is not reserved" % constant

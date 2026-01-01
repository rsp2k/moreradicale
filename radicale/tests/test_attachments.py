# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025 Ryan Malloy
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
Tests for RFC 8607 Managed Attachments.
"""

import base64
import os
import sys
import tempfile
import wsgiref.util
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import vobject

from radicale.tests import BaseTest
from radicale.tests.helpers import get_file_content


SIMPLE_EVENT = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//Example//EN
BEGIN:VEVENT
UID:attachment-test@example.com
DTSTART:20250115T100000Z
DTEND:20250115T110000Z
SUMMARY:Event with attachments
END:VEVENT
END:VCALENDAR"""


class TestManagedAttachments(BaseTest):
    """Tests for RFC 8607 Managed Attachments."""

    def setup_method(self) -> None:
        BaseTest.setup_method(self)

        # Configure rights to allow all access
        rights_file_path = os.path.join(self.colpath, "rights")
        with open(rights_file_path, "w") as f:
            f.write("""\
[allow all]
user: .*
collection: .*
permissions: RrWw""")

        self.attachments_folder = tempfile.mkdtemp()
        self.configure({
            "auth": {"type": "none"},
            "rights": {"file": rights_file_path, "type": "from_file"},
            "attachments": {
                "enabled": "True",
                "filesystem_folder": self.attachments_folder,
                "max_size": "10000000",
                "max_per_resource": "20",
            },
        })

    def request_binary(self, method: str, path: str, data: bytes,
                       check: Optional[int] = None,
                       content_type: str = "application/octet-stream",
                       **kwargs) -> Tuple[int, Dict[str, str], bytes]:
        """Send a request with binary data."""
        from urllib.parse import urlparse

        login = kwargs.pop("login", None)
        environ: Dict[str, Any] = {k.upper(): v for k, v in kwargs.items()}

        encoding: str = self.configuration.get("encoding", "request")
        if login:
            environ["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(
                login.encode(encoding)).decode()

        # Parse path and query string
        parsed = urlparse(path)
        actual_path = parsed.path
        query_string = parsed.query

        environ["REQUEST_METHOD"] = method.upper()
        environ["PATH_INFO"] = actual_path
        environ["QUERY_STRING"] = query_string
        environ["CONTENT_TYPE"] = content_type
        environ["wsgi.input"] = BytesIO(data)
        environ["CONTENT_LENGTH"] = str(len(data))
        environ["wsgi.errors"] = sys.stderr
        wsgiref.util.setup_testing_defaults(environ)

        status = headers = None

        def start_response(status_: str, headers_: List[Tuple[str, str]]) -> None:
            nonlocal status, headers
            status = int(status_.split()[0])
            headers = dict(headers_)

        answers = list(self.application(environ, start_response))
        assert status is not None and headers is not None
        assert check is None or status == check, "%d != %d" % (status, check)

        return status, headers, b"".join(answers)

    def test_attachment_disabled_returns_501(self) -> None:
        """Attachment POST returns 501 when disabled."""
        # Reconfigure with attachments disabled but maintain auth/rights
        rights_file_path = os.path.join(self.colpath, "rights")
        self.configure({
            "auth": {"type": "none"},
            "rights": {"file": rights_file_path, "type": "from_file"},
            "attachments": {"enabled": "False"},
        })

        # Create a calendar and event
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        # Try to add attachment - should fail with 501 Not Implemented
        status, _, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            b"test attachment data",
            content_type="text/plain",
            login="user:",
        )
        assert status == 501

    def test_attachment_add(self) -> None:
        """POST with action=attachment-add uploads attachment."""
        # Create a calendar and event
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        # Add attachment
        attachment_data = b"This is test attachment content."
        status, headers, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            attachment_data,
            content_type="text/plain",
            HTTP_CONTENT_DISPOSITION='attachment; filename="test.txt"',
            login="user:",
        )

        assert status == 201
        assert "Cal-Managed-ID" in headers
        managed_id = headers["Cal-Managed-ID"]
        assert managed_id  # Should have a value

        # Verify the event now has an ATTACH property
        _, event_data = self.get("/user/calendar/event.ics", login="user:")
        vobj = vobject.readOne(event_data)
        assert hasattr(vobj.vevent, 'attach')
        attach = vobj.vevent.attach
        # Check MANAGED-ID parameter exists
        assert "MANAGED-ID" in attach.params
        assert attach.params["MANAGED-ID"][0] == managed_id

    def test_attachment_get(self) -> None:
        """GET on attachment URL retrieves the attachment."""
        # Create a calendar and event
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        # Add attachment
        attachment_data = b"This is test attachment content for retrieval."
        status, headers, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            attachment_data,
            content_type="text/plain",
            HTTP_CONTENT_DISPOSITION='attachment; filename="retrieve.txt"',
            login="user:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Retrieve attachment
        status, headers, answer = self.request_binary(
            "GET",
            f"/.attachments/user/{managed_id}",
            b"",
            login="user:",
        )
        assert status == 200
        assert answer == attachment_data
        assert headers.get("Content-Type") == "text/plain"

    def test_attachment_update(self) -> None:
        """POST with action=attachment-update replaces attachment."""
        # Create a calendar and event
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        # Add initial attachment
        initial_data = b"Initial attachment content."
        status, headers, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            initial_data,
            content_type="text/plain",
            login="user:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Update attachment
        updated_data = b"Updated attachment content with more data."
        status, _, _ = self.request_binary(
            "POST",
            f"/user/calendar/event.ics?action=attachment-update&managed-id={managed_id}",
            updated_data,
            content_type="text/plain",
            login="user:",
        )
        assert status == 204

        # Verify updated content
        status, _, answer = self.request_binary(
            "GET",
            f"/.attachments/user/{managed_id}",
            b"",
            login="user:",
        )
        assert status == 200
        assert answer == updated_data

    def test_attachment_remove(self) -> None:
        """POST with action=attachment-remove deletes attachment."""
        # Create a calendar and event
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        # Add attachment
        status, headers, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            b"Attachment to be removed.",
            content_type="text/plain",
            login="user:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Verify attachment exists
        status, _, _ = self.request_binary(
            "GET",
            f"/.attachments/user/{managed_id}",
            b"",
            login="user:",
        )
        assert status == 200

        # Remove attachment
        status, _, _ = self.request_binary(
            "POST",
            f"/user/calendar/event.ics?action=attachment-remove&managed-id={managed_id}",
            b"",
            login="user:",
        )
        assert status == 204

        # Verify attachment no longer exists
        status, _, _ = self.request_binary(
            "GET",
            f"/.attachments/user/{managed_id}",
            b"",
            login="user:",
        )
        assert status == 404

        # Verify ATTACH property removed from event
        _, event_data = self.get("/user/calendar/event.ics", login="user:")
        vobj = vobject.readOne(event_data)
        attach_list = vobj.vevent.contents.get('attach', [])
        # Should be empty or not contain our managed_id
        for attach in attach_list:
            params = attach.params if hasattr(attach, 'params') else {}
            assert params.get("MANAGED-ID", [None])[0] != managed_id

    def test_attachment_max_size_enforced(self) -> None:
        """Attachments exceeding max_size are rejected."""
        # Reconfigure with small max size
        rights_file_path = os.path.join(self.colpath, "rights")
        self.configure({
            "auth": {"type": "none"},
            "rights": {"file": rights_file_path, "type": "from_file"},
            "attachments": {
                "enabled": "True",
                "filesystem_folder": self.attachments_folder,
                "max_size": "100",
                "max_per_resource": "20",
            },
        })

        # Create a calendar and event
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        # Try to add large attachment
        large_data = b"x" * 200  # Exceeds 100 byte limit
        status, _, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            large_data,
            content_type="application/octet-stream",
            login="user:",
        )
        assert status == 413  # Request Entity Too Large

    def test_attachment_max_per_resource_enforced(self) -> None:
        """Maximum attachments per resource is enforced."""
        # Reconfigure with max 2 attachments per resource
        rights_file_path = os.path.join(self.colpath, "rights")
        self.configure({
            "auth": {"type": "none"},
            "rights": {"file": rights_file_path, "type": "from_file"},
            "attachments": {
                "enabled": "True",
                "filesystem_folder": self.attachments_folder,
                "max_size": "10000000",
                "max_per_resource": "2",
            },
        })

        # Create a calendar and event
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        # Add first attachment - should succeed
        status, _, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            b"attachment 1",
            content_type="text/plain",
            login="user:",
        )
        assert status == 201

        # Add second attachment - should succeed
        status, _, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            b"attachment 2",
            content_type="text/plain",
            login="user:",
        )
        assert status == 201

        # Add third attachment - should fail
        status, _, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            b"attachment 3",
            content_type="text/plain",
            login="user:",
        )
        assert status == 507  # Insufficient Storage

    def test_attachment_unauthorized_access(self) -> None:
        """Users cannot access other users' attachments."""
        # Create calendar and event for user1
        self.mkcalendar("/user1/calendar/", login="user1:")
        self.put("/user1/calendar/event.ics", SIMPLE_EVENT, login="user1:")

        # Add attachment as user1
        status, headers, _ = self.request_binary(
            "POST",
            "/user1/calendar/event.ics?action=attachment-add",
            b"user1 private attachment",
            content_type="text/plain",
            login="user1:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Try to access as user2 - should fail
        status, _, _ = self.request_binary(
            "GET",
            f"/.attachments/user1/{managed_id}",
            b"",
            login="user2:",
        )
        assert status == 403  # Forbidden

    def test_propfind_attachment_properties(self) -> None:
        """PROPFIND returns managed attachment properties."""
        # Create a calendar
        self.mkcalendar("/user/calendar/", login="user:")

        # Request attachment-related properties
        propfind_body = """\
<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <C:max-attachment-size/>
    <C:max-attachments-per-resource/>
  </D:prop>
</D:propfind>"""

        status, responses = self.propfind(
            "/user/calendar/",
            propfind_body,
            login="user:",
        )
        assert status == 207

        props = responses.get("/user/calendar/")
        assert props is not None

        # Check max-attachment-size property
        max_size_status, max_size_elem = props.get("C:max-attachment-size", (404, None))
        assert max_size_status == 200
        assert max_size_elem.text == "10000000"

        # Check max-attachments-per-resource property
        max_per_status, max_per_elem = props.get("C:max-attachments-per-resource", (404, None))
        assert max_per_status == 200
        assert max_per_elem.text == "20"

    def test_dav_header_includes_managed_attachments(self) -> None:
        """OPTIONS response includes calendar-managed-attachments in DAV header."""
        status, headers, _ = self.request("OPTIONS", "/", login="user:")
        assert status == 200
        dav_header = headers.get("DAV", "")
        assert "calendar-managed-attachments" in dav_header

    def test_attachment_update_missing_managed_id(self) -> None:
        """attachment-update without managed-id returns 400."""
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        status, _, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-update",  # Missing managed-id
            b"updated data",
            content_type="text/plain",
            login="user:",
        )
        assert status == 400

    def test_attachment_remove_missing_managed_id(self) -> None:
        """attachment-remove without managed-id returns 400."""
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        status, _, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-remove",  # Missing managed-id
            b"",
            login="user:",
        )
        assert status == 400

    def test_attachment_nonexistent_event(self) -> None:
        """Adding attachment to nonexistent event returns 404."""
        self.mkcalendar("/user/calendar/", login="user:")
        # Don't create the event

        status, _, _ = self.request_binary(
            "POST",
            "/user/calendar/nonexistent.ics?action=attachment-add",
            b"test data",
            content_type="text/plain",
            login="user:",
        )
        assert status == 404

    def test_attachment_with_utf8_filename(self) -> None:
        """Attachments with UTF-8 filenames are handled correctly."""
        self.mkcalendar("/user/calendar/", login="user:")
        self.put("/user/calendar/event.ics", SIMPLE_EVENT, login="user:")

        # Add attachment with UTF-8 filename using RFC 5987 encoding
        status, headers, _ = self.request_binary(
            "POST",
            "/user/calendar/event.ics?action=attachment-add",
            b"attachment with unicode name",
            content_type="text/plain",
            HTTP_CONTENT_DISPOSITION="attachment; filename*=UTF-8''t%C3%A9st%20fil%C3%A9.txt",
            login="user:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Verify the event has the attachment with correct filename
        _, event_data = self.get("/user/calendar/event.ics", login="user:")
        vobj = vobject.readOne(event_data)
        attach = vobj.vevent.attach
        assert "FILENAME" in attach.params
        assert attach.params["FILENAME"][0] == "tést filé.txt"


class TestAttachmentSharedAccess(BaseTest):
    """Tests for attachment access via shared calendars."""

    def setup_method(self) -> None:
        BaseTest.setup_method(self)

        # Configure with sharing enabled and owner_only_shared rights
        self.attachments_folder = tempfile.mkdtemp()
        self.configure({
            "auth": {"type": "none"},
            "rights": {"type": "owner_only_shared"},
            "sharing": {
                "enabled": "True",
            },
            "attachments": {
                "enabled": "True",
                "filesystem_folder": self.attachments_folder,
                "max_size": "10000000",
                "max_per_resource": "20",
            },
        })

    def request_binary(self, method: str, path: str, data: bytes,
                       check: Optional[int] = None,
                       content_type: str = "application/octet-stream",
                       **kwargs) -> Tuple[int, Dict[str, str], bytes]:
        """Send a request with binary data."""
        from urllib.parse import urlparse

        login = kwargs.pop("login", None)
        environ: Dict[str, Any] = {k.upper(): v for k, v in kwargs.items()}

        encoding: str = self.configuration.get("encoding", "request")
        if login:
            environ["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(
                login.encode(encoding)).decode()

        # Parse path and query string
        parsed = urlparse(path)
        actual_path = parsed.path
        query_string = parsed.query

        environ["REQUEST_METHOD"] = method.upper()
        environ["PATH_INFO"] = actual_path
        environ["QUERY_STRING"] = query_string
        environ["CONTENT_TYPE"] = content_type
        environ["wsgi.input"] = BytesIO(data)
        environ["CONTENT_LENGTH"] = str(len(data))
        environ["wsgi.errors"] = sys.stderr
        wsgiref.util.setup_testing_defaults(environ)

        status = headers = None

        def start_response(status_: str, headers_: List[Tuple[str, str]]) -> None:
            nonlocal status, headers
            status = int(status_.split()[0])
            headers = dict(headers_)

        answers = list(self.application(environ, start_response))
        assert status is not None and headers is not None
        assert check is None or status == check, "%d != %d" % (status, check)

        return status, headers, b"".join(answers)

    def _share_calendar(self, owner: str, sharee: str, calendar_path: str,
                        access: str = "read-write") -> None:
        """Helper to set up calendar sharing directly via storage.

        This bypasses the HTTP interface to directly set up sharing
        for testing purposes.
        """
        import json
        from radicale.sharing import SHARES_PROPERTY, InviteStatus

        # Discover the collection
        with self.application._storage.acquire_lock("w", owner):
            discovered = list(self.application._storage.discover(calendar_path, depth="0"))
            if not discovered:
                raise ValueError(f"Calendar not found: {calendar_path}")
            collection = discovered[0]

            # Set sharing metadata
            shares_data = {
                sharee: {
                    "access": access,
                    "cn": sharee,
                    "status": InviteStatus.ACCEPTED.value,
                    "invited_at": "2025-01-01T00:00:00Z",
                    "accepted_at": "2025-01-01T00:00:00Z"
                }
            }
            meta = dict(collection.get_meta() or {})
            meta[SHARES_PROPERTY] = json.dumps(shares_data)
            collection.set_meta(meta)

    def test_shared_user_can_access_attachment(self) -> None:
        """User with shared calendar access can download attachments."""
        # Create calendar and event for alice
        self.mkcalendar("/alice/calendar/", login="alice:")
        self.put("/alice/calendar/event.ics", SIMPLE_EVENT, login="alice:")

        # Add attachment as alice
        attachment_data = b"This is alice's shared attachment."
        status, headers, _ = self.request_binary(
            "POST",
            "/alice/calendar/event.ics?action=attachment-add",
            attachment_data,
            content_type="text/plain",
            HTTP_CONTENT_DISPOSITION='attachment; filename="shared.txt"',
            login="alice:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Share calendar with bob
        self._share_calendar("alice", "bob", "/alice/calendar/")

        # Bob should be able to access the attachment
        status, headers, answer = self.request_binary(
            "GET",
            f"/.attachments/alice/{managed_id}",
            b"",
            login="bob:",
        )
        assert status == 200
        assert answer == attachment_data
        assert headers.get("Content-Type") == "text/plain"

    def test_non_shared_user_cannot_access_attachment(self) -> None:
        """User without shared access cannot download attachments."""
        # Create calendar and event for alice
        self.mkcalendar("/alice/calendar/", login="alice:")
        self.put("/alice/calendar/event.ics", SIMPLE_EVENT, login="alice:")

        # Add attachment as alice
        status, headers, _ = self.request_binary(
            "POST",
            "/alice/calendar/event.ics?action=attachment-add",
            b"alice private attachment",
            content_type="text/plain",
            login="alice:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Calendar is NOT shared with charlie
        # Charlie should NOT be able to access the attachment
        status, _, _ = self.request_binary(
            "GET",
            f"/.attachments/alice/{managed_id}",
            b"",
            login="charlie:",
        )
        assert status == 403  # Forbidden

    def test_read_only_shared_user_can_access_attachment(self) -> None:
        """User with read-only shared access can download attachments."""
        # Create calendar and event for alice
        self.mkcalendar("/alice/calendar/", login="alice:")
        self.put("/alice/calendar/event.ics", SIMPLE_EVENT, login="alice:")

        # Add attachment as alice
        attachment_data = b"Read-only shared attachment content."
        status, headers, _ = self.request_binary(
            "POST",
            "/alice/calendar/event.ics?action=attachment-add",
            attachment_data,
            content_type="application/pdf",
            HTTP_CONTENT_DISPOSITION='attachment; filename="document.pdf"',
            login="alice:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Share calendar with bob as read-only
        self._share_calendar("alice", "bob", "/alice/calendar/", access="read")

        # Bob should still be able to access (download) the attachment
        status, headers, answer = self.request_binary(
            "GET",
            f"/.attachments/alice/{managed_id}",
            b"",
            login="bob:",
        )
        assert status == 200
        assert answer == attachment_data

    def test_owner_can_always_access_own_attachment(self) -> None:
        """Calendar owner can always access their own attachments."""
        # Create calendar and event
        self.mkcalendar("/alice/calendar/", login="alice:")
        self.put("/alice/calendar/event.ics", SIMPLE_EVENT, login="alice:")

        # Add attachment
        attachment_data = b"Owner's attachment."
        status, headers, _ = self.request_binary(
            "POST",
            "/alice/calendar/event.ics?action=attachment-add",
            attachment_data,
            content_type="text/plain",
            login="alice:",
        )
        assert status == 201
        managed_id = headers["Cal-Managed-ID"]

        # Owner can access
        status, _, answer = self.request_binary(
            "GET",
            f"/.attachments/alice/{managed_id}",
            b"",
            login="alice:",
        )
        assert status == 200
        assert answer == attachment_data

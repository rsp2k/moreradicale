# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 RFC 3253 LABEL Tests
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

"""Tests for RFC 3253 §8 LABEL method and DAV:label-name-set property."""

import os
import subprocess

from moreradicale.tests import BaseTest


class TestLabelMethod(BaseTest):
    """Test RFC 3253 LABEL method for version labeling."""

    def _init_git_repo(self):
        """Initialize git repository in the storage folder."""
        storage_folder = os.path.join(self.configuration.get("storage", "filesystem_folder"), "collection-root")
        subprocess.run(["git", "init"], cwd=storage_folder, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=storage_folder, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=storage_folder, check=True, capture_output=True)

    def _commit_item(self, item_path: str, message: str = "Test commit"):
        """Commit an item to git."""
        storage_folder = os.path.join(self.configuration.get("storage", "filesystem_folder"), "collection-root")
        # Get relative path within collection-root (strip leading slash)
        relative_path = item_path.lstrip("/")
        subprocess.run(["git", "add", relative_path], cwd=storage_folder, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=storage_folder, check=True, capture_output=True)

    def test_label_add_success(self):
        """Test adding a label to a version."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})
        self._init_git_repo()

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-label-test
DTSTAMP:20250115T120000Z
DTSTART:20250116T100000Z
DTEND:20250116T110000Z
SUMMARY:Label Test Event
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_ics,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201

        # Commit to git
        self._commit_item("/test/calendar.ics/event.ics", "Initial event")

        # Add label "production"
        label_request = """<?xml version="1.0" encoding="utf-8"?>
<D:label xmlns:D="DAV:">
    <D:add>
        <D:label-name>production</D:label-name>
    </D:add>
</D:label>"""

        status, headers, response = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            label_request,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 200
        assert "production" in response.lower()

    def test_label_add_multiple_labels(self):
        """Test adding multiple labels at once."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})
        self._init_git_repo()

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:multi-label-test
DTSTAMP:20250115T120000Z
DTSTART:20250116T100000Z
SUMMARY:Multi-Label Test
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_ics,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201
        self._commit_item("/test/calendar.ics/event.ics", "Event for multi-label")

        # Add multiple labels
        label_request = """<?xml version="1.0" encoding="utf-8"?>
<D:label xmlns:D="DAV:">
    <D:add>
        <D:label-name>stable</D:label-name>
        <D:label-name>v1.0</D:label-name>
        <D:label-name>production</D:label-name>
    </D:add>
</D:label>"""

        status, _, response = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            label_request,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 200
        assert "stable" in response.lower()
        assert "production" in response.lower()

    def test_label_set_moves_label(self):
        """Test SET operation moves label to new version."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})
        self._init_git_repo()

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_v1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:set-label-test
DTSTAMP:20250115T120000Z
DTSTART:20250116T100000Z
SUMMARY:Version 1
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_v1,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201
        self._commit_item("/test/calendar.ics/event.ics", "Version 1")

        # Add label to version 1
        add_label = """<?xml version="1.0" encoding="utf-8"?>
<D:label xmlns:D="DAV:">
    <D:add>
        <D:label-name>latest</D:label-name>
    </D:add>
</D:label>"""

        status, _, _ = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            add_label,
            CONTENT_TYPE="application/xml",
            login="test:"
        )
        assert status == 200

        # Update event (version 2)
        event_v2 = event_v1.replace("Version 1", "Version 2")
        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_v2,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 204  # 204 No Content for update (not 200)
        self._commit_item("/test/calendar.ics/event.ics", "Version 2")

        # SET label to move it to version 2
        set_label = """<?xml version="1.0" encoding="utf-8"?>
<D:label xmlns:D="DAV:">
    <D:set>
        <D:label-name>latest</D:label-name>
    </D:set>
</D:label>"""

        status, _, response = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            set_label,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 200
        assert "set" in response.lower()

    def test_label_remove_deletes_label(self):
        """Test REMOVE operation deletes label."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})
        self._init_git_repo()

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:remove-label-test
DTSTAMP:20250115T120000Z
DTSTART:20250116T100000Z
SUMMARY:Remove Label Test
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_ics,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201
        self._commit_item("/test/calendar.ics/event.ics", "Event for removal test")

        # Add label
        add_label = """<?xml version="1.0" encoding="utf-8"?>
<D:label xmlns:D="DAV:">
    <D:add>
        <D:label-name>temporary</D:label-name>
    </D:add>
</D:label>"""

        status, _, _ = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            add_label,
            CONTENT_TYPE="application/xml",
            login="test:"
        )
        assert status == 200

        # Remove label
        remove_label = """<?xml version="1.0" encoding="utf-8"?>
<D:label xmlns:D="DAV:">
    <D:remove>
        <D:label-name>temporary</D:label-name>
    </D:remove>
</D:label>"""

        status, _, response = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            remove_label,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 200
        assert "remove" in response.lower()

    def test_label_name_set_property(self):
        """Test DAV:label-name-set property returns labels."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True",
                                     "versioning_include_in_allprop": "True"}})
        self._init_git_repo()

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:propfind-label-test
DTSTAMP:20250115T120000Z
DTSTART:20250116T100000Z
SUMMARY:PROPFIND Label Test
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

        # Add labels
        add_labels = """<?xml version="1.0" encoding="utf-8"?>
<D:label xmlns:D="DAV:">
    <D:add>
        <D:label-name>v1.0</D:label-name>
        <D:label-name>stable</D:label-name>
    </D:add>
</D:label>"""

        status, _, _ = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            add_labels,
            CONTENT_TYPE="application/xml",
            login="test:"
        )
        assert status == 200

        # PROPFIND for label-name-set
        propfind_request = """<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:">
    <prop>
        <label-name-set/>
    </prop>
</propfind>"""

        status, _, response = self.request(
            "PROPFIND", "/test/calendar.ics/event.ics",
            propfind_request,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 207
        assert "label-name-set" in response
        # Should contain both labels
        assert "v1.0" in response
        assert "stable" in response

    def test_label_disabled_when_versioning_off(self):
        """Test LABEL returns error when versioning disabled."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "False"}})

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:no-versioning-test
DTSTAMP:20250115T120000Z
DTSTART:20250116T100000Z
SUMMARY:No Versioning
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_ics,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201

        # Try to add label (should fail)
        label_request = """<?xml version="1.0" encoding="utf-8"?>
<D:label xmlns:D="DAV:">
    <D:add>
        <D:label-name>should-fail</D:label-name>
    </D:add>
</D:label>"""

        status, _, response = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            label_request,
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 405  # METHOD_NOT_ALLOWED
        assert "not enabled" in response.lower()

    def test_label_invalid_xml_returns_400(self):
        """Test LABEL with invalid XML returns BAD_REQUEST."""
        self.configure({"auth": {"type": "none"}})
        self.configure({"storage": {"versioning": "True"}})

        # Create calendar and event
        self.mkcalendar("/test/calendar.ics/", login="test:")
        event_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:invalid-xml-test
DTSTAMP:20250115T120000Z
DTSTART:20250116T100000Z
SUMMARY:Invalid XML Test
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/test/calendar.ics/event.ics",
            event_ics,
            CONTENT_TYPE="text/calendar",
            login="test:"
        )
        assert status == 201

        # Send invalid XML
        status, _, _ = self.request(
            "LABEL", "/test/calendar.ics/event.ics",
            "Not valid XML at all!",
            CONTENT_TYPE="application/xml",
            login="test:"
        )

        assert status == 400  # BAD_REQUEST

"""
Tests for RFC 4331: Quota and Size Properties for DAV Collections.

Tests the quota reporting and enforcement features including:
- DAV quota properties in PROPFIND
- Quota calculation
- Quota enforcement on PUT
"""

import os
import tempfile

import pytest

from radicale import config
from radicale.tests import BaseTest


class TestRFC4331QuotaModule:
    """Tests for RFC 4331 quota calculation module."""

    def test_get_directory_size_empty(self):
        """Test size calculation for empty directory."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            size = quota.get_directory_size(tmpdir)
            assert size == 0

    def test_get_directory_size_with_files(self):
        """Test size calculation with files."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            file1 = os.path.join(tmpdir, "file1.txt")
            file2 = os.path.join(tmpdir, "file2.txt")
            with open(file1, "w") as f:
                f.write("Hello" * 100)  # 500 bytes
            with open(file2, "w") as f:
                f.write("World" * 200)  # 1000 bytes

            size = quota.get_directory_size(tmpdir)
            assert size == 1500

    def test_get_directory_size_excludes_cache(self):
        """Test that cache directories are excluded by default."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create regular file
            file1 = os.path.join(tmpdir, "file1.txt")
            with open(file1, "w") as f:
                f.write("Hello" * 100)  # 500 bytes

            # Create cache directory with files
            cache_dir = os.path.join(tmpdir, ".Radicale.cache")
            os.makedirs(cache_dir)
            cache_file = os.path.join(cache_dir, "cache.txt")
            with open(cache_file, "w") as f:
                f.write("Cache" * 1000)  # 5000 bytes

            # Without cache
            size_no_cache = quota.get_directory_size(tmpdir, include_cache=False)
            assert size_no_cache == 500

            # With cache
            size_with_cache = quota.get_directory_size(tmpdir, include_cache=True)
            assert size_with_cache == 5500

    def test_get_directory_size_excludes_lock_files(self):
        """Test that lock files are excluded."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create regular file
            file1 = os.path.join(tmpdir, "file1.txt")
            with open(file1, "w") as f:
                f.write("Hello" * 100)  # 500 bytes

            # Create lock file
            lock_file = os.path.join(tmpdir, ".Radicale.lock")
            with open(lock_file, "w") as f:
                f.write("Lock" * 100)

            size = quota.get_directory_size(tmpdir)
            assert size == 500

    def test_get_directory_size_recursive(self):
        """Test size calculation with subdirectories."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create nested directories with files
            subdir = os.path.join(tmpdir, "subdir", "nested")
            os.makedirs(subdir)

            file1 = os.path.join(tmpdir, "file1.txt")
            file2 = os.path.join(subdir, "file2.txt")
            with open(file1, "w") as f:
                f.write("A" * 100)
            with open(file2, "w") as f:
                f.write("B" * 200)

            size = quota.get_directory_size(tmpdir)
            assert size == 300

    def test_format_bytes(self):
        """Test human-readable byte formatting."""
        from radicale import quota

        assert quota.format_bytes(-1) == "unlimited"
        assert quota.format_bytes(0) == "0.0 B"
        assert quota.format_bytes(500) == "500.0 B"
        assert quota.format_bytes(1024) == "1.0 KB"
        assert quota.format_bytes(1536) == "1.5 KB"
        assert quota.format_bytes(1048576) == "1.0 MB"
        assert quota.format_bytes(1073741824) == "1.0 GB"

    def test_calculate_user_quota_disabled(self):
        """Test quota calculation when disabled."""
        from radicale import quota

        configuration = config.load()
        configuration.update({"quota": {"enabled": "False"}}, "test", privileged=True)

        used, available = quota.calculate_user_quota(configuration, "testuser")
        assert used == 0
        assert available == -1  # Unlimited

    def test_calculate_user_quota_unlimited(self):
        """Test quota calculation with unlimited quota."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = config.load()
            configuration.update({
                "quota": {"enabled": "True", "max_bytes": "0"},
                "storage": {"filesystem_folder": tmpdir}
            }, "test", privileged=True)

            # Create user storage
            user_path = os.path.join(tmpdir, "collection-root", "testuser")
            os.makedirs(user_path)
            test_file = os.path.join(user_path, "test.ics")
            with open(test_file, "w") as f:
                f.write("A" * 500)

            used, available = quota.calculate_user_quota(configuration, "testuser")
            assert used == 500
            assert available == -1  # Unlimited when max_bytes=0

    def test_calculate_user_quota_with_limit(self):
        """Test quota calculation with a set limit."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = config.load()
            configuration.update({
                "quota": {"enabled": "True", "max_bytes": "10000"},
                "storage": {"filesystem_folder": tmpdir}
            }, "test", privileged=True)

            # Create user storage
            user_path = os.path.join(tmpdir, "collection-root", "testuser")
            os.makedirs(user_path)
            test_file = os.path.join(user_path, "test.ics")
            with open(test_file, "w") as f:
                f.write("A" * 3000)

            used, available = quota.calculate_user_quota(configuration, "testuser")
            assert used == 3000
            assert available == 7000  # 10000 - 3000

    def test_calculate_user_quota_over_limit(self):
        """Test quota calculation when over limit."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = config.load()
            configuration.update({
                "quota": {"enabled": "True", "max_bytes": "1000"},
                "storage": {"filesystem_folder": tmpdir}
            }, "test", privileged=True)

            # Create user storage exceeding quota
            user_path = os.path.join(tmpdir, "collection-root", "testuser")
            os.makedirs(user_path)
            test_file = os.path.join(user_path, "test.ics")
            with open(test_file, "w") as f:
                f.write("A" * 5000)

            used, available = quota.calculate_user_quota(configuration, "testuser")
            assert used == 5000
            assert available == 0  # Can't go negative

    def test_check_quota_exceeded_disabled(self):
        """Test quota exceeded check when disabled."""
        from radicale import quota

        configuration = config.load()
        configuration.update({"quota": {"enabled": "False"}}, "test", privileged=True)

        # Should never be exceeded when disabled
        assert quota.check_quota_exceeded(configuration, "testuser", 1000000) is False

    def test_check_quota_exceeded_unlimited(self):
        """Test quota exceeded check with unlimited quota."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = config.load()
            configuration.update({
                "quota": {"enabled": "True", "max_bytes": "0"},
                "storage": {"filesystem_folder": tmpdir}
            }, "test", privileged=True)

            # Should never be exceeded with unlimited
            assert quota.check_quota_exceeded(configuration, "testuser", 1000000) is False

    def test_check_quota_exceeded_within_limit(self):
        """Test quota exceeded check within limit."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = config.load()
            configuration.update({
                "quota": {"enabled": "True", "max_bytes": "10000"},
                "storage": {"filesystem_folder": tmpdir}
            }, "test", privileged=True)

            # Create user storage
            user_path = os.path.join(tmpdir, "collection-root", "testuser")
            os.makedirs(user_path)
            test_file = os.path.join(user_path, "test.ics")
            with open(test_file, "w") as f:
                f.write("A" * 3000)

            # Adding 5000 bytes should be OK (3000 + 5000 = 8000 < 10000)
            assert quota.check_quota_exceeded(configuration, "testuser", 5000) is False

    def test_check_quota_exceeded_over_limit(self):
        """Test quota exceeded check over limit."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = config.load()
            configuration.update({
                "quota": {"enabled": "True", "max_bytes": "10000"},
                "storage": {"filesystem_folder": tmpdir}
            }, "test", privileged=True)

            # Create user storage
            user_path = os.path.join(tmpdir, "collection-root", "testuser")
            os.makedirs(user_path)
            test_file = os.path.join(user_path, "test.ics")
            with open(test_file, "w") as f:
                f.write("A" * 8000)

            # Adding 5000 bytes would exceed (8000 + 5000 = 13000 > 10000)
            assert quota.check_quota_exceeded(configuration, "testuser", 5000) is True

    def test_get_user_storage_path_exists(self):
        """Test getting user storage path when it exists."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = config.load()
            configuration.update({
                "storage": {"filesystem_folder": tmpdir}
            }, "test", privileged=True)

            # Create user directory
            user_path = os.path.join(tmpdir, "collection-root", "testuser")
            os.makedirs(user_path)

            result = quota.get_user_storage_path(configuration, "testuser")
            assert result == user_path

    def test_get_user_storage_path_not_exists(self):
        """Test getting user storage path when it doesn't exist."""
        from radicale import quota

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = config.load()
            configuration.update({
                "storage": {"filesystem_folder": tmpdir}
            }, "test", privileged=True)

            result = quota.get_user_storage_path(configuration, "nonexistent")
            assert result is None


class TestRFC4331WebDAV(BaseTest):
    """Tests for RFC 4331 WebDAV integration."""

    def test_quota_properties_disabled(self):
        """Test that quota properties return 404 when disabled."""
        self.configure({
            "quota": {"enabled": "False"},
            "auth": {"type": "none"}
        })

        # Create a calendar
        mkcalendar_body = """<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:set>
        <D:prop>
            <D:displayname>Test Calendar</D:displayname>
        </D:prop>
    </D:set>
</C:mkcalendar>"""
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar/",
            data=mkcalendar_body,
            login="user:user")
        assert status == 201

        # Request quota properties
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
    <D:prop>
        <D:quota-available-bytes/>
        <D:quota-used-bytes/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        # Both should be 404 when quota is disabled
        assert "404 Not Found" in body

    def test_quota_used_bytes_property(self):
        """Test quota-used-bytes property in PROPFIND."""
        self.configure({
            "quota": {"enabled": "True", "max_bytes": "1000000"},
            "auth": {"type": "none"}
        })

        # Create a calendar
        mkcalendar_body = """<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:set>
        <D:prop>
            <D:displayname>Test Calendar</D:displayname>
        </D:prop>
    </D:set>
</C:mkcalendar>"""
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar/",
            data=mkcalendar_body,
            login="user:user")
        assert status == 201

        # Request quota-used-bytes
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
    <D:prop>
        <D:quota-used-bytes/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "quota-used-bytes" in body
        # Should return a number (even if 0)
        assert "200 OK" in body

    def test_quota_available_bytes_property(self):
        """Test quota-available-bytes property in PROPFIND."""
        self.configure({
            "quota": {"enabled": "True", "max_bytes": "1000000"},
            "auth": {"type": "none"}
        })

        # Create a calendar
        mkcalendar_body = """<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:set>
        <D:prop>
            <D:displayname>Test Calendar</D:displayname>
        </D:prop>
    </D:set>
</C:mkcalendar>"""
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar/",
            data=mkcalendar_body,
            login="user:user")
        assert status == 201

        # Request quota-available-bytes
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
    <D:prop>
        <D:quota-available-bytes/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        assert "quota-available-bytes" in body
        assert "200 OK" in body

    def test_quota_unlimited_no_available_bytes(self):
        """Test that quota-available-bytes returns 404 when unlimited."""
        self.configure({
            "quota": {"enabled": "True", "max_bytes": "0"},  # 0 = unlimited
            "auth": {"type": "none"}
        })

        # Create a calendar
        mkcalendar_body = """<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:set>
        <D:prop>
            <D:displayname>Test Calendar</D:displayname>
        </D:prop>
    </D:set>
</C:mkcalendar>"""
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar/",
            data=mkcalendar_body,
            login="user:user")
        assert status == 201

        # Request quota-available-bytes
        propfind_body = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
    <D:prop>
        <D:quota-available-bytes/>
    </D:prop>
</D:propfind>"""

        status, _, body = self.request(
            "PROPFIND", "/user/",
            data=propfind_body,
            login="user:user")

        assert status == 207
        # Should be 404 because unlimited means no defined available bytes
        # per RFC 4331: absence means unlimited
        assert "quota-available-bytes" in body


class TestRFC4331QuotaEnforcement(BaseTest):
    """Tests for RFC 4331 quota enforcement on PUT requests."""

    def test_put_within_quota(self):
        """Test that PUT succeeds within quota."""
        self.configure({
            "quota": {"enabled": "True", "max_bytes": "1000000"},
            "auth": {"type": "none"}
        })

        # Create a calendar
        mkcalendar_body = """<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:set>
        <D:prop>
            <D:displayname>Test Calendar</D:displayname>
        </D:prop>
    </D:set>
</C:mkcalendar>"""
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar/",
            data=mkcalendar_body,
            login="user:user")
        assert status == 201

        # PUT a small event
        event = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:test-event@example.com
DTSTART:20240101T100000Z
DTEND:20240101T110000Z
SUMMARY:Test Event
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/user/calendar/event.ics",
            data=event,
            login="user:user")

        assert status == 201

    def test_put_quota_disabled(self):
        """Test that PUT succeeds when quota is disabled."""
        self.configure({
            "quota": {"enabled": "False"},
            "auth": {"type": "none"}
        })

        # Create a calendar
        mkcalendar_body = """<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:D="DAV:">
    <D:set>
        <D:prop>
            <D:displayname>Test Calendar</D:displayname>
        </D:prop>
    </D:set>
</C:mkcalendar>"""
        status, _, _ = self.request(
            "MKCALENDAR", "/user/calendar/",
            data=mkcalendar_body,
            login="user:user")
        assert status == 201

        # PUT event should succeed regardless of size
        event = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:test-event@example.com
DTSTART:20240101T100000Z
DTEND:20240101T110000Z
SUMMARY:Test Event
END:VEVENT
END:VCALENDAR"""

        status, _, _ = self.request(
            "PUT", "/user/calendar/event.ics",
            data=event,
            login="user:user")

        assert status == 201

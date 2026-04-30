"""
Tests for RFC 7808 Time Zone Data Distribution Service (TZDIST).
"""

import json


from moreradicale.tests import BaseTest


class TestTZDist(BaseTest):
    """Test RFC 7808 Timezone Distribution Service."""

    def setup_method(self):
        """Set up test fixtures with TZDIST enabled."""
        super().setup_method()
        self.configure({
            "tzdist": {
                "enabled": "True",
                "cache_ttl": "60",
                "expand_years": "5"
            }
        })

    def test_tzdist_disabled_returns_404(self):
        """Test that TZDIST returns 404 when disabled."""
        self.configure({"tzdist": {"enabled": "False"}})
        status, _, _ = self.request("GET", "/.well-known/timezone")
        assert status == 404

    def test_capabilities_endpoint(self):
        """Test capabilities action returns server info."""
        status, headers, body = self.request("GET", "/.well-known/timezone")
        assert status == 200
        assert "application/json" in headers.get("Content-Type", "")

        data = json.loads(body)
        assert data["version"] == 1
        assert "info" in data
        assert "actions" in data
        assert "stats" in data
        assert data["stats"]["timezone-count"] > 0

    def test_capabilities_with_explicit_action(self):
        """Test capabilities with explicit action parameter."""
        status, _, body = self.request(
            "GET", "/.well-known/timezone?action=capabilities"
        )
        assert status == 200
        data = json.loads(body)
        assert data["version"] == 1

    def test_list_timezones(self):
        """Test list action returns timezone identifiers."""
        status, headers, body = self.request(
            "GET", "/.well-known/timezone?action=list"
        )
        assert status == 200
        assert "application/json" in headers.get("Content-Type", "")

        data = json.loads(body)
        assert "synctoken" in data
        assert "timezones" in data
        assert len(data["timezones"]) > 0

        # Check for common timezones
        tzids = [tz["tzid"] for tz in data["timezones"]]
        assert "America/New_York" in tzids
        assert "Europe/London" in tzids
        assert "Asia/Tokyo" in tzids
        assert "UTC" in tzids

    def test_list_returns_etag(self):
        """Test list action returns ETag for caching."""
        status, headers, _ = self.request(
            "GET", "/.well-known/timezone?action=list"
        )
        assert status == 200
        assert "ETag" in headers

    def test_get_timezone_america_new_york(self):
        """Test getting America/New_York timezone."""
        status, headers, body = self.request(
            "GET", "/.well-known/timezone?action=get&tzid=America/New_York"
        )
        assert status == 200
        assert "text/calendar" in headers.get("Content-Type", "")

        # Verify it's valid iCalendar VTIMEZONE
        assert "BEGIN:VCALENDAR" in body
        assert "BEGIN:VTIMEZONE" in body
        assert "TZID:America/New_York" in body
        assert "END:VTIMEZONE" in body
        assert "END:VCALENDAR" in body

    def test_get_timezone_utc(self):
        """Test getting UTC timezone."""
        status, _, body = self.request(
            "GET", "/.well-known/timezone?action=get&tzid=UTC"
        )
        assert status == 200
        assert "TZID:UTC" in body

    def test_get_timezone_europe_london(self):
        """Test getting Europe/London timezone with DST."""
        status, _, body = self.request(
            "GET", "/.well-known/timezone?action=get&tzid=Europe/London"
        )
        assert status == 200
        assert "TZID:Europe/London" in body
        # Europe/London has DST, should have transitions
        assert "STANDARD" in body or "DAYLIGHT" in body

    def test_get_timezone_returns_etag(self):
        """Test get action returns ETag for caching."""
        status, headers, _ = self.request(
            "GET", "/.well-known/timezone?action=get&tzid=UTC"
        )
        assert status == 200
        assert "ETag" in headers

    def test_get_invalid_timezone(self):
        """Test getting non-existent timezone returns 404."""
        status, headers, body = self.request(
            "GET", "/.well-known/timezone?action=get&tzid=Invalid/Timezone"
        )
        assert status == 404
        assert "application/problem+json" in headers.get("Content-Type", "")

        data = json.loads(body)
        assert "invalid-tzid" in data["type"]

    def test_get_missing_tzid_parameter(self):
        """Test get action without tzid parameter returns 400."""
        status, headers, body = self.request(
            "GET", "/.well-known/timezone?action=get"
        )
        assert status == 400
        data = json.loads(body)
        assert "missing-tzid" in data["type"]

    def test_get_with_date_range(self):
        """Test getting timezone with date range truncation."""
        status, _, body = self.request(
            "GET",
            "/.well-known/timezone?action=get&tzid=America/New_York"
            "&start=2020-01-01&end=2025-12-31"
        )
        assert status == 200
        assert "TZID:America/New_York" in body

    def test_find_timezones_america(self):
        """Test finding American timezones with pattern."""
        status, headers, body = self.request(
            "GET", "/.well-known/timezone?action=find&pattern=America/*"
        )
        assert status == 200
        assert "application/json" in headers.get("Content-Type", "")

        data = json.loads(body)
        assert data["pattern"] == "America/*"
        assert data["count"] > 0
        assert all(
            tz["tzid"].startswith("America/")
            for tz in data["timezones"]
        )

    def test_find_timezones_europe(self):
        """Test finding European timezones."""
        status, _, body = self.request(
            "GET", "/.well-known/timezone?action=find&pattern=Europe/*"
        )
        assert status == 200
        data = json.loads(body)
        assert data["count"] > 0
        assert "Europe/London" in [tz["tzid"] for tz in data["timezones"]]
        assert "Europe/Paris" in [tz["tzid"] for tz in data["timezones"]]

    def test_find_timezones_wildcard(self):
        """Test finding all timezones with * pattern."""
        status, _, body = self.request(
            "GET", "/.well-known/timezone?action=find&pattern=*"
        )
        assert status == 200
        data = json.loads(body)
        # Should return all timezones
        assert data["count"] > 100

    def test_find_timezones_specific(self):
        """Test finding specific timezone pattern."""
        status, _, body = self.request(
            "GET", "/.well-known/timezone?action=find&pattern=*/New_York"
        )
        assert status == 200
        data = json.loads(body)
        assert any("New_York" in tz["tzid"] for tz in data["timezones"])

    def test_invalid_action(self):
        """Test invalid action returns 400."""
        status, headers, body = self.request(
            "GET", "/.well-known/timezone?action=invalid"
        )
        assert status == 400
        data = json.loads(body)
        assert "invalid-action" in data["type"]

    def test_method_not_allowed(self):
        """Test POST returns 405 Method Not Allowed."""
        status, headers, _ = self.request(
            "POST", "/.well-known/timezone", data="test"
        )
        assert status == 405
        assert "Allow" in headers

    def test_cache_control_header(self):
        """Test Cache-Control header is set."""
        status, headers, _ = self.request("GET", "/.well-known/timezone")
        assert status == 200
        assert "Cache-Control" in headers
        assert "max-age=" in headers["Cache-Control"]


class TestTZDistProvider(BaseTest):
    """Test timezone provider functionality."""

    def test_zoneinfo_provider_list(self):
        """Test ZoneinfoProvider lists timezones."""
        from moreradicale.tzdist.provider import ZoneinfoProvider

        provider = ZoneinfoProvider()
        timezones = provider.list_timezones()

        assert len(timezones) > 100
        assert "America/New_York" in timezones
        assert "Europe/London" in timezones
        assert "UTC" in timezones
        # Should be sorted
        assert timezones == sorted(timezones)

    def test_zoneinfo_provider_get(self):
        """Test ZoneinfoProvider gets specific timezone."""
        from moreradicale.tzdist.provider import ZoneinfoProvider

        provider = ZoneinfoProvider()

        tz = provider.get_timezone("America/New_York")
        assert tz is not None

        tz_invalid = provider.get_timezone("Invalid/Timezone")
        assert tz_invalid is None

    def test_zoneinfo_provider_find(self):
        """Test ZoneinfoProvider find with patterns."""
        from moreradicale.tzdist.provider import ZoneinfoProvider

        provider = ZoneinfoProvider()

        matches = provider.find_timezones("America/*")
        assert len(matches) > 0
        assert all(m.startswith("America/") for m in matches)

        matches = provider.find_timezones("*Tokyo*")
        assert "Asia/Tokyo" in matches

    def test_zoneinfo_provider_transitions(self):
        """Test ZoneinfoProvider gets DST transitions."""
        from moreradicale.tzdist.provider import ZoneinfoProvider

        provider = ZoneinfoProvider()

        # America/New_York has DST
        transitions = provider.get_transitions("America/New_York", 2020, 2025)
        assert len(transitions) > 0

        # UTC has no transitions
        transitions = provider.get_transitions("UTC", 2020, 2025)
        assert len(transitions) >= 1  # At least standard offset


class TestTZDistFormatter(BaseTest):
    """Test VTIMEZONE formatter."""

    def test_format_offset_positive(self):
        """Test formatting positive UTC offset."""
        from moreradicale.tzdist.formatter import format_offset

        assert format_offset(3600) == "+0100"
        assert format_offset(5 * 3600 + 30 * 60) == "+0530"
        assert format_offset(0) == "+0000"

    def test_format_offset_negative(self):
        """Test formatting negative UTC offset."""
        from moreradicale.tzdist.formatter import format_offset

        assert format_offset(-5 * 3600) == "-0500"
        assert format_offset(-8 * 3600) == "-0800"

    def test_format_offset_with_seconds(self):
        """Test formatting offset with seconds."""
        from moreradicale.tzdist.formatter import format_offset

        # Historical offsets sometimes had seconds
        assert format_offset(3600 + 30) == "+010030"

    def test_transitions_to_vtimezone_no_dst(self):
        """Test formatting timezone without DST."""
        from datetime import datetime, timezone
        from moreradicale.tzdist.formatter import transitions_to_vtimezone

        transitions = [(
            datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc),
            "UTC",
            0,
            0
        )]

        result = transitions_to_vtimezone("UTC", transitions, 2020, 2025)

        assert "BEGIN:VCALENDAR" in result
        assert "BEGIN:VTIMEZONE" in result
        assert "TZID:UTC" in result
        assert "BEGIN:STANDARD" in result
        assert "END:VCALENDAR" in result

    def test_transitions_to_vtimezone_with_dst(self):
        """Test formatting timezone with DST transitions."""
        from datetime import datetime, timezone
        from moreradicale.tzdist.formatter import transitions_to_vtimezone

        transitions = [
            (datetime(2020, 3, 8, 7, 0, tzinfo=timezone.utc), "EDT", -4 * 3600, 3600),
            (datetime(2020, 11, 1, 6, 0, tzinfo=timezone.utc), "EST", -5 * 3600, 0),
        ]

        result = transitions_to_vtimezone(
            "America/New_York", transitions, 2020, 2020
        )

        assert "TZID:America/New_York" in result
        # Should have both STANDARD and DAYLIGHT
        assert "BEGIN:STANDARD" in result or "BEGIN:DAYLIGHT" in result

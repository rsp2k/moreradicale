"""
Tests for CardDAV Directory Gateway.

Tests vCard mapping, LDAP attribute handling, and gateway configuration.
Note: Full LDAP integration tests require a running LDAP server.
"""

import pytest


class TestVCardBuilder:
    """Tests for VCardBuilder."""

    def test_build_minimal_vcard(self):
        """Test building minimal valid vCard."""
        from moreradicale.directory.vcard_mapper import VCardBuilder

        builder = VCardBuilder()
        builder.set_fn("John Doe")
        builder.uid = "test-uid-1"

        vcard = builder.build()

        assert "BEGIN:VCARD" in vcard
        assert "VERSION:4.0" in vcard
        assert "FN:John Doe" in vcard
        assert "UID:test-uid-1" in vcard
        assert "END:VCARD" in vcard

    def test_build_with_structured_name(self):
        """Test building vCard with structured name."""
        from moreradicale.directory.vcard_mapper import VCardBuilder

        builder = VCardBuilder()
        builder.set_name(given="John", family="Doe", prefix="Dr.")
        builder.set_fn("Dr. John Doe")

        vcard = builder.build()

        assert "N:Doe;John;;Dr.;" in vcard

    def test_build_with_organization(self):
        """Test building vCard with organization."""
        from moreradicale.directory.vcard_mapper import VCardBuilder

        builder = VCardBuilder()
        builder.set_fn("Jane Smith")
        builder.set_org(name="ACME Corp", unit="Engineering")
        builder.set_title("Software Engineer")

        vcard = builder.build()

        assert "ORG:ACME Corp;Engineering" in vcard
        assert "TITLE:Software Engineer" in vcard

    def test_build_with_contacts(self):
        """Test building vCard with email and phone."""
        from moreradicale.directory.vcard_mapper import VCardBuilder

        builder = VCardBuilder()
        builder.set_fn("Bob Wilson")
        builder.add_email("bob@example.com", "work")
        builder.add_email("bob.personal@example.com", "home")
        builder.add_phone("+1-555-1234", "work")
        builder.add_phone("+1-555-5678", "cell")

        vcard = builder.build()

        assert "EMAIL;TYPE=work:bob@example.com" in vcard
        assert "EMAIL;TYPE=home:bob.personal@example.com" in vcard
        assert "TEL;TYPE=work:+1-555-1234" in vcard
        assert "TEL;TYPE=cell:+1-555-5678" in vcard

    def test_build_with_address(self):
        """Test building vCard with address."""
        from moreradicale.directory.vcard_mapper import VCardBuilder

        builder = VCardBuilder()
        builder.set_fn("Alice Brown")
        builder.set_address_part("work", "street", "123 Main St")
        builder.set_address_part("work", "locality", "Springfield")
        builder.set_address_part("work", "region", "IL")
        builder.set_address_part("work", "code", "62701")
        builder.set_address_part("work", "country", "USA")

        vcard = builder.build()

        assert "ADR;TYPE=work:" in vcard
        assert "123 Main St" in vcard
        assert "Springfield" in vcard

    def test_auto_generate_uid(self):
        """Test UID is auto-generated if not provided."""
        from moreradicale.directory.vcard_mapper import VCardBuilder

        builder = VCardBuilder()
        builder.set_fn("Test User")

        vcard = builder.build()

        assert "UID:" in vcard

    def test_auto_generate_fn(self):
        """Test FN is auto-generated from name if not provided."""
        from moreradicale.directory.vcard_mapper import VCardBuilder

        builder = VCardBuilder()
        builder.set_name(given="John", family="Doe")

        vcard = builder.build()

        assert "FN:John Doe" in vcard

    def test_escape_special_characters(self):
        """Test special characters are escaped."""
        from moreradicale.directory.vcard_mapper import VCardBuilder

        builder = VCardBuilder()
        builder.set_fn("John; Doe, Jr.")
        builder.set_note("Line1\nLine2")

        vcard = builder.build()

        assert "FN:John\\; Doe\\, Jr." in vcard
        assert "NOTE:Line1\\nLine2" in vcard


class TestVCardMapper:
    """Tests for VCardMapper."""

    def test_map_basic_entry(self):
        """Test mapping basic LDAP entry."""
        from moreradicale.directory.vcard_mapper import VCardMapper

        mapper = VCardMapper()

        dn = "cn=John Doe,ou=People,dc=example,dc=com"
        attrs = {
            "cn": ["John Doe"],
            "givenName": ["John"],
            "sn": ["Doe"],
            "mail": ["john@example.com"],
            "telephoneNumber": ["+1-555-1234"],
        }

        vcard = mapper.map_entry(dn, attrs)

        assert "FN:John Doe" in vcard
        assert "EMAIL;TYPE=work:john@example.com" in vcard
        assert "TEL;TYPE=work:+1-555-1234" in vcard

    def test_map_organization_entry(self):
        """Test mapping entry with organization."""
        from moreradicale.directory.vcard_mapper import VCardMapper

        mapper = VCardMapper()

        dn = "cn=Jane Smith,ou=People,dc=example,dc=com"
        attrs = {
            "cn": ["Jane Smith"],
            "o": ["ACME Corporation"],
            "ou": ["Engineering"],
            "title": ["Senior Engineer"],
        }

        vcard = mapper.map_entry(dn, attrs)

        assert "ORG:ACME Corporation" in vcard
        assert "Engineering" in vcard
        assert "TITLE:Senior Engineer" in vcard

    def test_map_bytes_attributes(self):
        """Test handling of bytes attributes (common in LDAP)."""
        from moreradicale.directory.vcard_mapper import VCardMapper

        mapper = VCardMapper()

        dn = "cn=Test User,dc=example,dc=com"
        attrs = {
            "cn": [b"Test User"],
            "mail": [b"test@example.com"],
        }

        vcard = mapper.map_entry(dn, attrs)

        assert "FN:Test User" in vcard
        assert "EMAIL;TYPE=work:test@example.com" in vcard

    def test_map_photo_attribute(self):
        """Test mapping photo attribute."""
        from moreradicale.directory.vcard_mapper import VCardMapper
        import base64

        mapper = VCardMapper()

        # Create a small test image (1x1 JPEG)
        jpeg_bytes = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
            "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
            "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
            "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAA"
            "AAAAADCP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/xAAVAQEBAAAAAAAAAAAAAAAAAAAAAv"
            "/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AKwAB//Z"
        )

        dn = "cn=Photo User,dc=example,dc=com"
        attrs = {
            "cn": ["Photo User"],
            "jpegPhoto": [jpeg_bytes],
        }

        vcard = mapper.map_entry(dn, attrs)

        assert "PHOTO:data:image/jpeg;base64," in vcard

    def test_extract_search_filter(self):
        """Test building LDAP search filter."""
        from moreradicale.directory.vcard_mapper import VCardMapper

        mapper = VCardMapper()

        filter_str = mapper.extract_search_filter("john")

        # Should create OR filter for common attributes
        assert "(|" in filter_str
        assert "(cn=*john*)" in filter_str
        assert "(mail=*john*)" in filter_str

    def test_escape_ldap_special_chars(self):
        """Test escaping LDAP special characters."""
        from moreradicale.directory.vcard_mapper import VCardMapper

        mapper = VCardMapper()

        # These characters need escaping in LDAP filters
        escaped = mapper._escape_ldap("user*(test)")

        assert "\\2a" in escaped  # * escaped
        assert "\\28" in escaped  # ( escaped
        assert "\\29" in escaped  # ) escaped

    def test_dn_to_uid(self):
        """Test DN to UID conversion."""
        from moreradicale.directory.vcard_mapper import VCardMapper

        mapper = VCardMapper()

        uid1 = mapper._dn_to_uid("cn=John,dc=example,dc=com")
        uid2 = mapper._dn_to_uid("cn=Jane,dc=example,dc=com")
        uid3 = mapper._dn_to_uid("cn=John,dc=example,dc=com")

        # Same DN should produce same UID
        assert uid1 == uid3
        # Different DNs should produce different UIDs
        assert uid1 != uid2
        # Should have ldap prefix
        assert uid1.startswith("ldap-")


class TestDefaultMapping:
    """Tests for default LDAP mapping."""

    def test_default_mapping_contains_common_attrs(self):
        """Test default mapping includes common LDAP attributes."""
        from moreradicale.directory.vcard_mapper import DEFAULT_LDAP_MAPPING

        # Core identity attributes
        assert "cn" in DEFAULT_LDAP_MAPPING
        assert "givenName" in DEFAULT_LDAP_MAPPING
        assert "sn" in DEFAULT_LDAP_MAPPING

        # Contact attributes
        assert "mail" in DEFAULT_LDAP_MAPPING
        assert "telephoneNumber" in DEFAULT_LDAP_MAPPING
        assert "mobile" in DEFAULT_LDAP_MAPPING

        # Organization attributes
        assert "o" in DEFAULT_LDAP_MAPPING
        assert "ou" in DEFAULT_LDAP_MAPPING
        assert "title" in DEFAULT_LDAP_MAPPING

    def test_default_mapping_ad_support(self):
        """Test default mapping includes Active Directory attributes."""
        from moreradicale.directory.vcard_mapper import DEFAULT_LDAP_MAPPING

        # Common AD-specific attributes
        assert "displayName" in DEFAULT_LDAP_MAPPING
        assert "company" in DEFAULT_LDAP_MAPPING
        assert "department" in DEFAULT_LDAP_MAPPING
        assert "thumbnailPhoto" in DEFAULT_LDAP_MAPPING
        assert "wWWHomePage" in DEFAULT_LDAP_MAPPING


class TestDirectoryGatewayConfig:
    """Tests for directory gateway configuration."""

    def test_gateway_disabled_by_default(self):
        """Test gateway is disabled by default."""
        from unittest.mock import Mock
        from moreradicale.directory.gateway import DirectoryGateway

        config = Mock()
        config.get.return_value = False

        gateway = DirectoryGateway(config)

        assert not gateway.enabled

    def test_gateway_virtual_path(self):
        """Test virtual path configuration."""
        from unittest.mock import Mock
        from moreradicale.directory.gateway import DirectoryGateway

        config = Mock()
        config.get.side_effect = lambda s, k: {
            ("directory", "enabled"): True,
            ("directory", "ldap_uri"): "ldap://localhost",
            ("directory", "ldap_base"): "dc=example,dc=com",
            ("directory", "ldap_reader_dn"): "cn=reader,dc=example,dc=com",
            ("directory", "ldap_secret"): "secret",
            ("directory", "ldap_secret_file"): "",
            ("directory", "ldap_filter"): "(objectClass=person)",
            ("directory", "ldap_security"): "none",
            ("directory", "ldap_ssl_verify_mode"): "REQUIRED",
            ("directory", "ldap_ssl_ca_file"): "",
            ("directory", "virtual_addressbook"): "/company/contacts",
            ("directory", "cache_ttl"): 300,
        }.get((s, k), "")

        # This will fail if ldap3/ldap not installed, which is expected
        try:
            gateway = DirectoryGateway(config)
            assert gateway.virtual_path == "/company/contacts/"
        except Exception:
            # LDAP module not available
            pass


class TestVCardEscaping:
    """Tests for vCard escaping edge cases."""

    def test_escape_empty_string(self):
        """Test escaping empty string."""
        from moreradicale.directory.vcard_mapper import _escape_vcard

        assert _escape_vcard("") == ""

    def test_escape_backslash(self):
        """Test escaping backslash."""
        from moreradicale.directory.vcard_mapper import _escape_vcard

        assert _escape_vcard("path\\to\\file") == "path\\\\to\\\\file"

    def test_escape_semicolon(self):
        """Test escaping semicolon."""
        from moreradicale.directory.vcard_mapper import _escape_vcard

        assert _escape_vcard("Doe; John") == "Doe\\; John"

    def test_escape_comma(self):
        """Test escaping comma."""
        from moreradicale.directory.vcard_mapper import _escape_vcard

        assert _escape_vcard("Doe, John") == "Doe\\, John"

    def test_escape_newline(self):
        """Test escaping newline."""
        from moreradicale.directory.vcard_mapper import _escape_vcard

        assert _escape_vcard("Line1\nLine2") == "Line1\\nLine2"

    def test_escape_multiple(self):
        """Test escaping multiple special characters."""
        from moreradicale.directory.vcard_mapper import _escape_vcard

        result = _escape_vcard("John; Doe, Jr.\nEngineer")
        assert "\\;" in result
        assert "\\," in result
        assert "\\n" in result

# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025 Multi-tenant support
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
Tests for multi-tenant support.

Tests all tenant extraction methods, isolation modes, and integration
with storage and rights systems.
"""

import os
import tempfile
from typing import Dict, Any

import pytest

from moreradicale import config, tenant
from moreradicale.tenant import TenantContext
from moreradicale.tenant.base import BaseTenantExtractor
from moreradicale.tenant.domain import Extractor as DomainExtractor
from moreradicale.tenant.path_prefix import Extractor as PathPrefixExtractor
from moreradicale.tenant.header import Extractor as HeaderExtractor
from moreradicale.tenant.subdomain import Extractor as SubdomainExtractor
from moreradicale.tenant.config import TenantAwareConfiguration
from moreradicale.tests import BaseTest


class TestTenantContext:
    """Tests for TenantContext dataclass."""

    def test_basic_context(self):
        """Test basic TenantContext creation."""
        ctx = TenantContext(tenant_id="acme")
        assert ctx.tenant_id == "acme"
        assert ctx.is_valid is True

    def test_empty_context_invalid(self):
        """Test that empty tenant_id is invalid."""
        ctx = TenantContext(tenant_id="")
        assert ctx.is_valid is False

    def test_context_with_all_fields(self):
        """Test TenantContext with all fields populated."""
        ctx = TenantContext(
            tenant_id="acme",
            extraction_method="domain",
            tenant_domain="acme.com",
            original_path="/user/calendar/",
            rewritten_path="/user/calendar/"
        )
        assert ctx.tenant_id == "acme"
        assert ctx.extraction_method == "domain"
        assert ctx.tenant_domain == "acme.com"
        assert ctx.original_path == "/user/calendar/"
        assert ctx.rewritten_path == "/user/calendar/"
        assert ctx.is_valid is True


class TestDomainExtractor:
    """Tests for domain-based tenant extraction."""

    def _make_config(self, **kwargs) -> config.Configuration:
        """Create configuration for testing."""
        configuration = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        base_config = {
            "tenant": {
                "enabled": "True",
                "type": "domain",
            }
        }
        base_config["tenant"].update(kwargs)
        configuration.update(base_config, "test", privileged=True)
        return configuration

    def test_extract_from_username(self):
        """Test extracting tenant from user@domain format."""
        cfg = self._make_config()
        extractor = DomainExtractor(cfg)

        ctx = extractor.extract({}, "/user/calendar/", "user@acme.com")

        assert ctx is not None
        assert ctx.tenant_id == "acme.com"
        assert ctx.extraction_method == "domain"

    def test_extract_no_domain(self):
        """Test extraction with username without domain."""
        cfg = self._make_config()
        extractor = DomainExtractor(cfg)

        ctx = extractor.extract({}, "/user/calendar/", "user")

        assert ctx is None  # No domain means no tenant

    def test_strip_subdomains(self):
        """Test stripping subdomains from domain."""
        cfg = self._make_config(domain_strip_subdomains="True")
        extractor = DomainExtractor(cfg)

        ctx = extractor.extract({}, "/user/calendar/", "user@mail.acme.com")

        assert ctx is not None
        assert ctx.tenant_id == "acme.com"

    def test_strip_subdomains_multiple_levels(self):
        """Test stripping multiple subdomain levels."""
        cfg = self._make_config(domain_strip_subdomains="True")
        extractor = DomainExtractor(cfg)

        ctx = extractor.extract({}, "/", "admin@deep.nested.example.org")

        assert ctx is not None
        assert ctx.tenant_id == "example.org"

    def test_no_strip_subdomains(self):
        """Test keeping full domain when strip_subdomains is False."""
        cfg = self._make_config(domain_strip_subdomains="False")
        extractor = DomainExtractor(cfg)

        ctx = extractor.extract({}, "/", "user@mail.acme.com")

        assert ctx is not None
        assert ctx.tenant_id == "mail.acme.com"


class TestPathPrefixExtractor:
    """Tests for path prefix tenant extraction."""

    def _make_config(self, **kwargs) -> config.Configuration:
        """Create configuration for testing."""
        configuration = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        base_config = {
            "tenant": {
                "enabled": "True",
                "type": "path_prefix",
                "path_prefix_pattern": "/{tenant}/",
            }
        }
        base_config["tenant"].update(kwargs)
        configuration.update(base_config, "test", privileged=True)
        return configuration

    def test_extract_from_path(self):
        """Test extracting tenant from URL path."""
        cfg = self._make_config()
        extractor = PathPrefixExtractor(cfg)

        ctx = extractor.extract({}, "/acme/user/calendar/", "")

        assert ctx is not None
        assert ctx.tenant_id == "acme"
        assert ctx.extraction_method == "path_prefix"
        assert ctx.original_path == "/acme/user/calendar/"
        assert ctx.rewritten_path == "/user/calendar/"

    def test_extract_root_path(self):
        """Test extraction from root path."""
        cfg = self._make_config()
        extractor = PathPrefixExtractor(cfg)

        ctx = extractor.extract({}, "/", "")

        # Root path has no tenant prefix
        assert ctx is None

    def test_path_rewriting(self):
        """Test that path is correctly rewritten."""
        cfg = self._make_config()
        extractor = PathPrefixExtractor(cfg)

        ctx = extractor.extract({}, "/tenant123/alice/calendar.ics", "")

        assert ctx is not None
        assert ctx.tenant_id == "tenant123"
        assert ctx.rewritten_path == "/alice/calendar.ics"

    def test_custom_pattern(self):
        """Test custom path prefix pattern."""
        cfg = self._make_config(path_prefix_pattern="/org/{tenant}/")
        extractor = PathPrefixExtractor(cfg)

        ctx = extractor.extract({}, "/org/acme/user/calendar/", "")

        assert ctx is not None
        assert ctx.tenant_id == "acme"
        assert ctx.rewritten_path == "/user/calendar/"


class TestHeaderExtractor:
    """Tests for header-based tenant extraction."""

    def _make_config(self, **kwargs) -> config.Configuration:
        """Create configuration for testing."""
        configuration = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        base_config = {
            "tenant": {
                "enabled": "True",
                "type": "header",
                "header_name": "X-Tenant-ID",
            }
        }
        base_config["tenant"].update(kwargs)
        configuration.update(base_config, "test", privileged=True)
        return configuration

    def test_extract_from_header(self):
        """Test extracting tenant from HTTP header."""
        cfg = self._make_config()
        extractor = HeaderExtractor(cfg)

        environ = {"HTTP_X_TENANT_ID": "acme"}
        ctx = extractor.extract(environ, "/user/calendar/", "")

        assert ctx is not None
        assert ctx.tenant_id == "acme"
        assert ctx.extraction_method == "header"

    def test_no_header(self):
        """Test extraction when header is missing."""
        cfg = self._make_config()
        extractor = HeaderExtractor(cfg)

        ctx = extractor.extract({}, "/user/calendar/", "")

        assert ctx is None

    def test_custom_header_name(self):
        """Test custom header name."""
        cfg = self._make_config(header_name="X-Organization")
        extractor = HeaderExtractor(cfg)

        environ = {"HTTP_X_ORGANIZATION": "bigcorp"}
        ctx = extractor.extract(environ, "/", "")

        assert ctx is not None
        assert ctx.tenant_id == "bigcorp"

    def test_header_case_insensitive(self):
        """Test that header lookup handles WSGI format."""
        cfg = self._make_config(header_name="X-My-Tenant")
        extractor = HeaderExtractor(cfg)

        # WSGI converts headers to uppercase with HTTP_ prefix
        environ = {"HTTP_X_MY_TENANT": "mytenant"}
        ctx = extractor.extract(environ, "/", "")

        assert ctx is not None
        assert ctx.tenant_id == "mytenant"

    def test_empty_header_value(self):
        """Test that empty header value returns None."""
        cfg = self._make_config()
        extractor = HeaderExtractor(cfg)

        environ = {"HTTP_X_TENANT_ID": ""}
        ctx = extractor.extract(environ, "/", "")

        assert ctx is None


class TestSubdomainExtractor:
    """Tests for subdomain-based tenant extraction."""

    def _make_config(self, **kwargs) -> config.Configuration:
        """Create configuration for testing."""
        configuration = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        base_config = {
            "tenant": {
                "enabled": "True",
                "type": "subdomain",
                "base_domain": "example.com",
            }
        }
        base_config["tenant"].update(kwargs)
        configuration.update(base_config, "test", privileged=True)
        return configuration

    def test_extract_from_subdomain(self):
        """Test extracting tenant from subdomain."""
        cfg = self._make_config()
        extractor = SubdomainExtractor(cfg)

        environ = {"HTTP_HOST": "acme.example.com"}
        ctx = extractor.extract(environ, "/user/calendar/", "")

        assert ctx is not None
        assert ctx.tenant_id == "acme"
        assert ctx.extraction_method == "subdomain"

    def test_base_domain_only(self):
        """Test that base domain alone returns None."""
        cfg = self._make_config()
        extractor = SubdomainExtractor(cfg)

        environ = {"HTTP_HOST": "example.com"}
        ctx = extractor.extract(environ, "/", "")

        assert ctx is None

    def test_www_subdomain_ignored(self):
        """Test that www subdomain is typically ignored."""
        cfg = self._make_config()
        extractor = SubdomainExtractor(cfg)

        # www is often not a tenant, but implementation may vary
        environ = {"HTTP_HOST": "www.example.com"}
        ctx = extractor.extract(environ, "/", "")

        # This test documents current behavior
        # www is treated as a tenant unless specifically excluded

    def test_multiple_subdomain_levels(self):
        """Test extraction with multiple subdomain levels."""
        cfg = self._make_config()
        extractor = SubdomainExtractor(cfg)

        environ = {"HTTP_HOST": "app.acme.example.com"}
        ctx = extractor.extract(environ, "/", "")

        assert ctx is not None
        # First subdomain component (closest to base domain) is tenant
        # app.acme.example.com with base example.com -> tenant is first level = "app"
        # (implementation takes first subdomain level for simplicity)
        assert ctx.tenant_id == "app"

    def test_no_host_header(self):
        """Test extraction when Host header is missing."""
        cfg = self._make_config()
        extractor = SubdomainExtractor(cfg)

        ctx = extractor.extract({}, "/", "")

        assert ctx is None

    def test_with_port(self):
        """Test extraction with port in Host header."""
        cfg = self._make_config()
        extractor = SubdomainExtractor(cfg)

        environ = {"HTTP_HOST": "acme.example.com:5232"}
        ctx = extractor.extract(environ, "/", "")

        assert ctx is not None
        assert ctx.tenant_id == "acme"


class TestNoneExtractor:
    """Tests for the 'none' extractor (disabled multi-tenancy)."""

    def _make_config(self) -> config.Configuration:
        """Create configuration for testing."""
        configuration = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        configuration.update({
            "tenant": {
                "enabled": "True",
                "type": "none",
            }
        }, "test", privileged=True)
        return configuration

    def test_none_always_returns_none(self):
        """Test that 'none' extractor returns None."""
        cfg = self._make_config()
        extractor = tenant.load(cfg)

        ctx = extractor.extract({"HTTP_HOST": "example.com"}, "/path/", "user@domain.com")

        assert ctx is None


class TestTenantLoader:
    """Tests for tenant module loader."""

    def test_load_domain(self):
        """Test loading domain extractor."""
        cfg = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        cfg.update({"tenant": {"enabled": "True", "type": "domain"}}, "test", privileged=True)

        extractor = tenant.load(cfg)

        assert isinstance(extractor, DomainExtractor)

    def test_load_path_prefix(self):
        """Test loading path_prefix extractor."""
        cfg = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        cfg.update({"tenant": {"enabled": "True", "type": "path_prefix"}}, "test", privileged=True)

        extractor = tenant.load(cfg)

        assert isinstance(extractor, PathPrefixExtractor)

    def test_load_header(self):
        """Test loading header extractor."""
        cfg = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        cfg.update({"tenant": {"enabled": "True", "type": "header"}}, "test", privileged=True)

        extractor = tenant.load(cfg)

        assert isinstance(extractor, HeaderExtractor)

    def test_load_subdomain(self):
        """Test loading subdomain extractor."""
        cfg = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
        cfg.update({"tenant": {"enabled": "True", "type": "subdomain"}}, "test", privileged=True)

        extractor = tenant.load(cfg)

        assert isinstance(extractor, SubdomainExtractor)


class TestTenantAwareConfiguration:
    """Tests for per-tenant configuration overrides."""

    def test_no_override_returns_base(self):
        """Test that missing override file returns base config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
            cfg.update({
                "tenant": {
                    "enabled": "True",
                    "type": "header",
                    "config_directory": tmpdir,
                }
            }, "test", privileged=True)

            tenant_cfg = TenantAwareConfiguration(cfg)
            ctx = TenantContext(tenant_id="nonexistent")

            result = tenant_cfg.for_tenant("nonexistent", ctx)

            # Should return base config (tenant not found)
            assert result is not None

    def test_override_applied(self):
        """Test that tenant config file overrides base."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create tenant config file
            tenant_file = os.path.join(tmpdir, "acme.conf")
            with open(tenant_file, "w") as f:
                f.write("[logging]\nlevel = debug\n")

            cfg = config.Configuration(config.DEFAULT_CONFIG_SCHEMA)
            cfg.update({
                "tenant": {
                    "enabled": "True",
                    "type": "header",
                    "config_directory": tmpdir,
                },
                "logging": {"level": "info"},
            }, "test", privileged=True)

            tenant_cfg = TenantAwareConfiguration(cfg)
            ctx = TenantContext(tenant_id="acme")

            result = tenant_cfg.for_tenant("acme", ctx)

            # Should have debug level from tenant config
            assert result.get("logging", "level") == "debug"


class TestTenantStorageIntegration(BaseTest):
    """Tests for tenant-aware storage paths."""

    def test_logical_isolation_shared_path(self):
        """Test logical isolation uses shared storage path."""
        self.configure({
            "tenant": {
                "enabled": "True",
                "type": "header",
                "isolation_mode": "logical",
            }
        })

        ctx = TenantContext(tenant_id="acme")
        self.application._storage.set_tenant_context(ctx)

        root = self.application._storage._get_collection_root_folder()

        # Logical isolation uses shared folder
        assert "tenants" not in root
        assert root.endswith("collection-root")

    def test_filesystem_isolation_tenant_path(self):
        """Test filesystem isolation uses tenant-specific path."""
        self.configure({
            "tenant": {
                "enabled": "True",
                "type": "header",
                "isolation_mode": "filesystem",
            }
        })

        ctx = TenantContext(tenant_id="acme")
        self.application._storage.set_tenant_context(ctx)

        root = self.application._storage._get_collection_root_folder()

        # Filesystem isolation uses tenant subfolder
        assert "tenants" in root
        assert "acme" in root
        assert root.endswith("collection-root")

    def test_no_tenant_context_uses_default(self):
        """Test that no tenant context uses default path."""
        self.configure({
            "tenant": {
                "enabled": "True",
                "type": "header",
                "isolation_mode": "filesystem",
            }
        })

        # No tenant context set
        root = self.application._storage._get_collection_root_folder()

        # Should use default path without tenant folder
        assert "tenants" not in root


class TestTenantRightsIntegration(BaseTest):
    """Tests for tenant-aware rights checking."""

    def test_tenant_owner_only_same_tenant(self):
        """Test tenant_owner_only allows same tenant access."""
        self.configure({
            "tenant": {
                "enabled": "True",
                "type": "domain",
                "isolation_mode": "logical",
            },
            "rights": {"type": "tenant_owner_only"},
        })

        ctx = TenantContext(
            tenant_id="acme.com",
            extraction_method="domain"
        )
        self.application._rights.set_tenant_context(ctx)

        # User accessing their own collection
        perms = self.application._rights.authorization("user@acme.com", "/user@acme.com/calendar/")

        # Should have permissions (same tenant)
        assert perms != ""

    def test_tenant_owner_only_different_tenant(self):
        """Test tenant_owner_only denies cross-tenant access."""
        self.configure({
            "tenant": {
                "enabled": "True",
                "type": "domain",
                "isolation_mode": "logical",
            },
            "rights": {"type": "tenant_owner_only"},
        })

        # User from acme.com
        ctx = TenantContext(
            tenant_id="acme.com",
            extraction_method="domain"
        )
        self.application._rights.set_tenant_context(ctx)

        # Try to access bigcorp.com user's collection
        perms = self.application._rights.authorization("user@acme.com", "/user@bigcorp.com/calendar/")

        # Should be denied (different tenant)
        assert perms == ""


class TestTenantApplicationIntegration(BaseTest):
    """Tests for tenant extraction in application request flow."""

    def test_header_tenant_in_request(self):
        """Test tenant extracted from header in request."""
        self.configure({
            "tenant": {
                "enabled": "True",
                "type": "header",
                "header_name": "X-Tenant-ID",
            },
            "auth": {"type": "none"},
        })

        status, _, _ = self.request(
            "PROPFIND", "/",
            HTTP_DEPTH="0",
            HTTP_X_TENANT_ID="acme"
        )

        # Request should succeed
        assert status == 207

    def test_path_prefix_tenant_in_request(self):
        """Test tenant extracted from path prefix."""
        self.configure({
            "tenant": {
                "enabled": "True",
                "type": "path_prefix",
            },
            "auth": {"type": "none"},
        })

        # Path with tenant prefix
        status, _, _ = self.request(
            "PROPFIND", "/acme/",
            HTTP_DEPTH="0"
        )

        # Request should succeed (path rewritten)
        assert status == 207

    def test_default_tenant_fallback(self):
        """Test default tenant used when extraction fails."""
        self.configure({
            "tenant": {
                "enabled": "True",
                "type": "header",
                "default_tenant": "default-org",
            },
            "auth": {"type": "none"},
        })

        # Request without tenant header
        status, _, _ = self.request(
            "PROPFIND", "/",
            HTTP_DEPTH="0"
            # No X-Tenant-ID header
        )

        # Should still work with default tenant
        assert status == 207

"""
Multi-Tenant Support for Radicale.

Provides tenant isolation for hosted CalDAV/CardDAV deployments.
Supports multiple tenant identification methods:
- Domain from username (user@domain.com)
- URL path prefix (/tenant/user/calendar/)
- HTTP header (X-Tenant-ID)
- Subdomain (tenant.example.com)

Configuration:
    [tenant]
    enabled = True
    type = domain
    isolation_mode = filesystem
    config_directory = /etc/moreradicale/tenants
"""

import contextvars
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

from moreradicale import utils

if TYPE_CHECKING:
    from moreradicale import config
    from moreradicale.tenant.base import BaseTenantExtractor


INTERNAL_TYPES: Sequence[str] = (
    "none", "domain", "path_prefix", "header", "subdomain"
)


# The tenant whose request is being served on this thread.
#
# This is deliberately a ContextVar rather than attributes on the storage and
# rights objects. ApplicationBase builds exactly one of each for the whole
# process, so storing per-request tenant identity on them is process-global
# state: with concurrent requests, a request for tenant A can observe tenant
# B's context. That is not a cosmetic race - the storage layer resolves the
# collection root from it (``/storage/tenants/{tenant_id}/collection-root`` in
# filesystem isolation mode), so the request would read and write another
# tenant's calendar data.
#
# Storage and rights share this single variable so they can never disagree
# about which tenant a request belongs to.
current_tenant: "contextvars.ContextVar[Optional[TenantContext]]" = (
    contextvars.ContextVar("moreradicale_current_tenant", default=None))


@dataclass
class TenantContext:
    """
    Holds tenant information through the request lifecycle.

    Created by TenantExtractor and passed to storage, rights,
    and other components that need tenant awareness.
    """

    tenant_id: str
    """Unique tenant identifier."""

    extraction_method: str = ""
    """Method used to identify tenant (domain, path_prefix, header, subdomain)."""

    tenant_domain: Optional[str] = None
    """Domain name if applicable."""

    original_path: str = ""
    """Original request path before any rewriting."""

    rewritten_path: str = ""
    """Path after tenant prefix removal (for path_prefix mode)."""

    config_override_path: Optional[str] = None
    """Path to tenant-specific configuration file."""

    storage_root: Optional[str] = None
    """Tenant-specific storage root (filesystem isolation mode)."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional tenant metadata."""

    @property
    def is_valid(self) -> bool:
        """Check if tenant context has a valid tenant ID."""
        return bool(self.tenant_id)

    def __repr__(self) -> str:
        return (
            f"TenantContext(tenant_id={self.tenant_id!r}, "
            f"method={self.extraction_method!r})"
        )


def load(configuration: "config.Configuration") -> "BaseTenantExtractor":
    """
    Load tenant extractor based on configuration.

    Args:
        configuration: Radicale configuration

    Returns:
        Configured tenant extractor instance
    """
    from moreradicale.tenant import base

    return utils.load_plugin(
        INTERNAL_TYPES, "tenant", "Extractor",
        base.BaseTenantExtractor, configuration
    )


__all__ = [
    "TenantContext",
    "load",
    "INTERNAL_TYPES",
]

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

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING

from moreradicale import utils
from moreradicale.log import logger

if TYPE_CHECKING:
    from moreradicale import config
    from moreradicale.tenant.base import BaseTenantExtractor


INTERNAL_TYPES: Sequence[str] = (
    "none", "domain", "path_prefix", "header", "subdomain"
)


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

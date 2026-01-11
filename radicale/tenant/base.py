"""
Base class for tenant extraction strategies.

All tenant extractors inherit from BaseTenantExtractor and implement
the extract() method to identify tenants from requests.
"""

from typing import Optional, TYPE_CHECKING

from radicale.log import logger

if TYPE_CHECKING:
    from radicale import config, types
    from radicale.tenant import TenantContext


class BaseTenantExtractor:
    """
    Base class for tenant extraction.

    Subclasses implement extract() to identify tenant from
    request environment, path, or user information.
    """

    def __init__(self, configuration: "config.Configuration") -> None:
        """
        Initialize the tenant extractor.

        Args:
            configuration: Radicale configuration
        """
        self.configuration = configuration
        self._default_tenant = configuration.get("tenant", "default_tenant")

    def extract(
        self,
        environ: "types.WSGIEnviron",
        path: str,
        user: str = ""
    ) -> Optional["TenantContext"]:
        """
        Extract tenant from request.

        Args:
            environ: WSGI environment dictionary
            path: Sanitized request path
            user: Authenticated username (may be empty if called pre-auth)

        Returns:
            TenantContext if tenant identified, None otherwise
        """
        raise NotImplementedError

    def rewrite_path(self, path: str, context: "TenantContext") -> str:
        """
        Rewrite path to remove tenant prefix if applicable.

        Default implementation returns path unchanged.
        Override in extractors that modify path structure.

        Args:
            path: Original request path
            context: Extracted tenant context

        Returns:
            Rewritten path (may be same as input)
        """
        return path

    def get_default_context(self) -> Optional["TenantContext"]:
        """
        Get default tenant context when extraction fails.

        Returns:
            TenantContext with default tenant, or None if no default
        """
        if self._default_tenant:
            from radicale.tenant import TenantContext
            return TenantContext(
                tenant_id=self._default_tenant,
                extraction_method="default"
            )
        return None


class Extractor(BaseTenantExtractor):
    """
    No-op extractor for single-tenant mode.

    Used when tenant.type = none or multi-tenancy is disabled.
    """

    def extract(
        self,
        environ: "types.WSGIEnviron",
        path: str,
        user: str = ""
    ) -> Optional["TenantContext"]:
        """Return None (no tenant) for single-tenant mode."""
        return None

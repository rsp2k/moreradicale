"""
Subdomain tenant extraction.

Extracts tenant from request subdomain: tenant.example.com -> tenant "tenant"

Configuration:
    [tenant]
    type = subdomain
    base_domain = example.com
"""

from typing import Optional, TYPE_CHECKING

from moreradicale.log import logger
from moreradicale.tenant import TenantContext
from moreradicale.tenant.base import BaseTenantExtractor

if TYPE_CHECKING:
    from moreradicale import config, types


class Extractor(BaseTenantExtractor):
    """
    Extract tenant from request subdomain.

    Requires base_domain configuration. Subdomain before base_domain
    is used as tenant ID.
    """

    def __init__(self, configuration: "config.Configuration") -> None:
        super().__init__(configuration)
        self._base_domain = configuration.get("tenant", "base_domain").lower()
        self._skip_subdomains = {"www", "mail", "api"}

    def extract(
        self,
        environ: "types.WSGIEnviron",
        path: str,
        user: str = ""
    ) -> Optional[TenantContext]:
        """
        Extract tenant from subdomain.

        Args:
            environ: WSGI environment containing HTTP_HOST
            path: Request path
            user: Username (not used for subdomain extraction)

        Returns:
            TenantContext with subdomain as tenant_id
        """
        if not self._base_domain:
            logger.warning(
                "Subdomain extraction: base_domain not configured"
            )
            return self.get_default_context()

        # Get host from environ (strip port if present)
        host = environ.get("HTTP_HOST", "").lower()
        if ":" in host:
            host = host.split(":")[0]

        if not host:
            logger.debug("Subdomain extraction: no HTTP_HOST")
            return self.get_default_context()

        # Check if host ends with base domain
        if not host.endswith(self._base_domain):
            logger.debug(
                "Subdomain extraction: %r doesn't end with %r",
                host, self._base_domain
            )
            return self.get_default_context()

        # Extract subdomain
        if host == self._base_domain:
            # No subdomain - just base domain
            logger.debug(
                "Subdomain extraction: host is base domain, no tenant"
            )
            return self.get_default_context()

        # Remove base domain and trailing dot
        subdomain = host[:-len(self._base_domain)].rstrip(".")

        if not subdomain:
            logger.debug("Subdomain extraction: empty subdomain")
            return self.get_default_context()

        # Handle multi-level subdomains: take first part only
        # a.b.example.com -> tenant "a"
        if "." in subdomain:
            subdomain = subdomain.split(".")[0]

        # Skip reserved subdomains
        if subdomain in self._skip_subdomains:
            logger.debug(
                "Subdomain extraction: %r is reserved",
                subdomain
            )
            return self.get_default_context()

        logger.debug(
            "Subdomain extraction: %r -> tenant=%r",
            host, subdomain
        )

        return TenantContext(
            tenant_id=subdomain,
            tenant_domain=host,
            extraction_method="subdomain",
            original_path=path,
            rewritten_path=path
        )

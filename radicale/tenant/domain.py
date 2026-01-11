"""
Domain-based tenant extraction.

Extracts tenant from username domain: user@example.com -> tenant "example.com"

Configuration:
    [tenant]
    type = domain
    domain_strip_subdomains = False
"""

from typing import Optional, TYPE_CHECKING

from radicale.log import logger
from radicale.tenant import TenantContext
from radicale.tenant.base import BaseTenantExtractor

if TYPE_CHECKING:
    from radicale import config, types


class Extractor(BaseTenantExtractor):
    """
    Extract tenant from username domain.

    Parses user@domain.com format and uses domain as tenant ID.
    """

    def __init__(self, configuration: "config.Configuration") -> None:
        super().__init__(configuration)
        self._strip_subdomains = configuration.get(
            "tenant", "domain_strip_subdomains"
        )

    def extract(
        self,
        environ: "types.WSGIEnviron",
        path: str,
        user: str = ""
    ) -> Optional[TenantContext]:
        """
        Extract tenant from username domain.

        Args:
            environ: WSGI environment
            path: Request path
            user: Username in user@domain format

        Returns:
            TenantContext with domain as tenant_id, or None
        """
        if not user or "@" not in user:
            logger.debug("Domain extraction: no @ in username %r", user)
            return self.get_default_context()

        # Extract domain part
        _, domain = user.rsplit("@", 1)

        if not domain:
            logger.debug("Domain extraction: empty domain in %r", user)
            return self.get_default_context()

        # Optionally strip subdomains
        tenant_id = self._normalize_domain(domain)

        logger.debug(
            "Domain extraction: user=%r -> tenant=%r",
            user, tenant_id
        )

        return TenantContext(
            tenant_id=tenant_id,
            tenant_domain=domain,
            extraction_method="domain",
            original_path=path,
            rewritten_path=path
        )

    def _normalize_domain(self, domain: str) -> str:
        """
        Normalize domain for tenant ID.

        Args:
            domain: Raw domain from username

        Returns:
            Normalized tenant ID
        """
        domain = domain.lower().strip()

        if self._strip_subdomains:
            # Keep only last two parts: sub.example.com -> example.com
            parts = domain.split(".")
            if len(parts) > 2:
                domain = ".".join(parts[-2:])

        return domain

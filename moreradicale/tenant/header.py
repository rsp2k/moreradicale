"""
HTTP header tenant extraction.

Extracts tenant from HTTP header set by reverse proxy.

Configuration:
    [tenant]
    type = header
    header_name = X-Tenant-ID
"""

from typing import Optional, TYPE_CHECKING

from moreradicale.log import logger
from moreradicale.tenant import TenantContext
from moreradicale.tenant.base import BaseTenantExtractor

if TYPE_CHECKING:
    from moreradicale import config, types


class Extractor(BaseTenantExtractor):
    """
    Extract tenant from HTTP header.

    Useful when reverse proxy (Caddy, nginx) handles tenant routing
    and sets a header for downstream services.
    """

    def __init__(self, configuration: "config.Configuration") -> None:
        super().__init__(configuration)
        header_name = configuration.get("tenant", "header_name")
        # Convert header name to WSGI format: X-Tenant-ID -> HTTP_X_TENANT_ID
        self._header_key = "HTTP_" + header_name.upper().replace("-", "_")
        self._header_name = header_name

    def extract(
        self,
        environ: "types.WSGIEnviron",
        path: str,
        user: str = ""
    ) -> Optional[TenantContext]:
        """
        Extract tenant from HTTP header.

        Args:
            environ: WSGI environment containing headers
            path: Request path
            user: Username (not used for header extraction)

        Returns:
            TenantContext with tenant_id from header
        """
        tenant_id = environ.get(self._header_key, "").strip()

        if not tenant_id:
            logger.debug(
                "Header extraction: %s header not found or empty",
                self._header_name
            )
            return self.get_default_context()

        # Sanitize tenant ID (no path separators, etc.)
        if "/" in tenant_id or "\\" in tenant_id:
            logger.warning(
                "Header extraction: invalid tenant ID %r (contains path separator)",
                tenant_id
            )
            return self.get_default_context()

        logger.debug(
            "Header extraction: %s=%r -> tenant=%r",
            self._header_name, tenant_id, tenant_id
        )

        return TenantContext(
            tenant_id=tenant_id,
            extraction_method="header",
            original_path=path,
            rewritten_path=path,
            metadata={"header_name": self._header_name}
        )

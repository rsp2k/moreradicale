"""
Path prefix tenant extraction.

Extracts tenant from URL path: /tenant/user/calendar/ -> tenant "tenant"

The path is rewritten to remove the tenant prefix for downstream processing.

Configuration:
    [tenant]
    type = path_prefix
    path_prefix_pattern = /{tenant}/
"""

import re
from typing import Optional, TYPE_CHECKING

from moreradicale.log import logger
from moreradicale.tenant import TenantContext
from moreradicale.tenant.base import BaseTenantExtractor

if TYPE_CHECKING:
    from moreradicale import config, types


class Extractor(BaseTenantExtractor):
    """
    Extract tenant from URL path prefix.

    Supports configurable patterns like:
    - /{tenant}/  (default - first component is tenant)
    - /org/{tenant}/  (tenant after fixed prefix)
    """

    _pattern: str
    _pattern_regex: re.Pattern
    _prefix_parts: int  # Number of fixed parts before {tenant}

    def __init__(self, configuration: "config.Configuration") -> None:
        super().__init__(configuration)
        self._reserved_paths = {
            ".well-known", ".metrics", ".web"
        }
        self._pattern = configuration.get("tenant", "path_prefix_pattern")
        self._parse_pattern()

    def _parse_pattern(self) -> None:
        """Parse the pattern to create regex and compute prefix parts."""
        # Pattern like /{tenant}/ or /org/{tenant}/
        # Convert {tenant} to a capturing group
        pattern = self._pattern.strip("/")
        parts = pattern.split("/")

        self._prefix_parts = 0
        regex_parts = []

        for part in parts:
            if part == "{tenant}":
                regex_parts.append("([^/]+)")
            else:
                regex_parts.append(re.escape(part))
                self._prefix_parts += 1

        # Build regex to match the prefix pattern
        regex = "^/" + "/".join(regex_parts) + "(?:/(.*))?$"
        self._pattern_regex = re.compile(regex)
        logger.debug("Path prefix pattern %r -> regex %r", self._pattern, regex)

    def extract(
        self,
        environ: "types.WSGIEnviron",
        path: str,
        user: str = ""
    ) -> Optional[TenantContext]:
        """
        Extract tenant from path using configured pattern.

        Args:
            environ: WSGI environment
            path: Request path like /tenant/user/calendar/
            user: Username (not used for path extraction)

        Returns:
            TenantContext with tenant_id and rewritten path
        """
        # Root path - no tenant
        if not path or path == "/":
            logger.debug("Path prefix extraction: root path, no tenant")
            return self.get_default_context()

        # Match against pattern
        match = self._pattern_regex.match(path)
        if not match:
            logger.debug(
                "Path prefix extraction: %r doesn't match pattern %r",
                path, self._pattern
            )
            return self.get_default_context()

        tenant_id = match.group(1)
        rest = match.group(2) or ""

        # Skip reserved paths
        if tenant_id.startswith(".") or tenant_id in self._reserved_paths:
            logger.debug(
                "Path prefix extraction: reserved path %r",
                tenant_id
            )
            return self.get_default_context()

        # Build rewritten path (the rest after pattern match)
        rewritten = "/" + rest if rest else "/"
        if path.endswith("/") and not rewritten.endswith("/"):
            rewritten += "/"

        logger.debug(
            "Path prefix extraction: %r -> tenant=%r, path=%r",
            path, tenant_id, rewritten
        )

        return TenantContext(
            tenant_id=tenant_id,
            extraction_method="path_prefix",
            original_path=path,
            rewritten_path=rewritten
        )

    def rewrite_path(self, path: str, context: TenantContext) -> str:
        """Return the rewritten path without tenant prefix."""
        return context.rewritten_path

# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2012-2017 Guillaume Ayoub
# Copyright © 2017-2018 Unrud <unrud@outlook.com>
# Copyright © 2025 Multi-tenant extension
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
Rights backend with multi-tenant isolation.

Extends owner_only to:
1. Enforce tenant boundaries (users can only access their tenant's data)
2. Support both logical and filesystem isolation modes
3. Prevent cross-tenant access

In logical isolation mode, users can only access paths owned by users
in the same tenant. In filesystem isolation mode, paths are already
isolated at the storage layer.
"""

from typing import Optional, TYPE_CHECKING

import radicale.rights.owner_only as owner_only
from radicale import config, pathutils
from radicale.log import logger

if TYPE_CHECKING:
    from radicale.tenant import TenantContext


class Rights(owner_only.Rights):
    """
    Rights with multi-tenant isolation enforcement.

    Inherits from owner_only and adds tenant boundary checks
    for logical isolation mode.
    """

    _tenant_context: Optional["TenantContext"] = None
    _tenant_enabled: bool = False
    _tenant_isolation_mode: str = "logical"

    def __init__(self, configuration: config.Configuration) -> None:
        super().__init__(configuration)
        self._tenant_enabled = configuration.get("tenant", "enabled")
        self._tenant_isolation_mode = configuration.get("tenant", "isolation_mode")

    def set_tenant_context(self, context: Optional["TenantContext"]) -> None:
        """
        Set tenant context for rights checking.

        Args:
            context: TenantContext from request, or None
        """
        self._tenant_context = context

    def authorization(self, user: str, path: str) -> str:
        """
        Check authorization with tenant isolation.

        In logical isolation mode, verifies that the user is accessing
        data belonging to a user in the same tenant.

        Args:
            user: Authenticated username
            path: Sanitized request path

        Returns:
            Permission string (e.g., "rw", "R", "")
        """
        # First check tenant boundary (for logical isolation)
        if not self._check_tenant_boundary(user, path):
            logger.debug(
                "Cross-tenant access denied: user=%r path=%r tenant=%r",
                user, path,
                self._tenant_context.tenant_id if self._tenant_context else "none"
            )
            return ""

        # Fall back to standard owner_only logic
        return super().authorization(user, path)

    def _check_tenant_boundary(self, user: str, path: str) -> bool:
        """
        Verify user is accessing data within their tenant.

        Args:
            user: Authenticated username
            path: Request path

        Returns:
            True if access is within tenant boundary
        """
        # Skip check if multi-tenancy not enabled
        if not self._tenant_enabled:
            return True

        # Skip check if no tenant context
        if not self._tenant_context or not self._tenant_context.is_valid:
            return True

        # Filesystem isolation handles this at storage layer
        if self._tenant_isolation_mode == "filesystem":
            return True

        # Logical isolation: verify path owner belongs to same tenant
        sane_path = pathutils.strip_path(path)
        if not sane_path:
            return True  # Root path always allowed

        path_owner = sane_path.split("/", maxsplit=1)[0]

        # Get tenant for requesting user and path owner
        user_tenant = self._get_user_tenant(user)
        owner_tenant = self._get_user_tenant(path_owner)

        # Both must be in the same tenant
        if user_tenant and owner_tenant and user_tenant != owner_tenant:
            return False

        return True

    def _get_user_tenant(self, user: str) -> str:
        """
        Determine tenant for a user.

        For domain-based tenancy, extracts domain from user@domain format.
        For other modes, uses the current tenant context.

        Args:
            user: Username

        Returns:
            Tenant ID for the user
        """
        if not self._tenant_context:
            return ""

        # For domain extraction, parse username
        if self._tenant_context.extraction_method == "domain":
            if "@" in user:
                _, domain = user.rsplit("@", 1)
                return domain.lower()
            return ""

        # For other methods, all users in the request share the same tenant
        return self._tenant_context.tenant_id

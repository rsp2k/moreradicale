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
Tenant extractor that disables multi-tenancy.

This is the default extractor when multi-tenancy is configured
but type is set to "none". It always returns None, effectively
disabling tenant extraction.
"""

from typing import Optional, TYPE_CHECKING

from radicale import config
from radicale.tenant.base import BaseTenantExtractor

if TYPE_CHECKING:
    from radicale import types
    from radicale.tenant import TenantContext


class Extractor(BaseTenantExtractor):
    """
    Tenant extractor that always returns None.

    Use this when you want to configure the tenant section
    but not actually extract tenants.
    """

    def __init__(self, configuration: config.Configuration) -> None:
        super().__init__(configuration)

    def extract(self, environ: "types.WSGIEnviron", path: str,
                user: str = "") -> Optional["TenantContext"]:
        """Always returns None - no tenant extraction."""
        return None

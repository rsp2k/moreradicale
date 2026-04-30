# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 RFC 3253 Versioning Implementation
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

"""RFC 3253 WebDAV Versioning support.

This module provides version history access via git.
Virtual paths under /.versions/ expose historical versions of items.

URL structure:
  /.versions/{collection}/{item}/       - Version history (list all versions)
  /.versions/{collection}/{item}/{sha}  - Specific version content

Write methods (CHECKOUT, CHECKIN, UNCHECKOUT, VERSION-CONTROL) operate
on regular resource paths and use the CheckoutManager for state tracking.
"""

from moreradicale.versioning.checkout_manager import CheckoutManager
from moreradicale.versioning.handler import VersioningHandler

__all__ = ["CheckoutManager", "VersioningHandler"]

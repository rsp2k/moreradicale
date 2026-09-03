# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2012-2017 Guillaume Ayoub
# Copyright © 2017-2018 Unrud <unrud@outlook.com>
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
The rights module used to determine if a user can read and/or write
collections and entries.

Permissions:

  - R: read collections (excluding address books and calendars)
  - r: read address book and calendar collections
  - i: subset of **r** that only allows direct access via HTTP method GET
       (CalDAV/CardDAV is susceptible to expensive search requests)
  - W: write collections (excluding address books and calendars)
  - w: write address book and calendar collections

Take a look at the class ``BaseRights`` if you want to implement your own.

"""

import contextvars
from typing import TYPE_CHECKING, Optional, Sequence, Set

from moreradicale import config, utils

if TYPE_CHECKING:
    from moreradicale.tenant import TenantContext

INTERNAL_TYPES: Sequence[str] = ("authenticated", "owner_write", "owner_only",
                                 "owner_only_shared", "tenant_owner_only",
                                 "from_file", "allow_read_write")


def load(configuration: "config.Configuration") -> "BaseRights":
    """Load the rights module chosen in configuration."""
    return utils.load_plugin(INTERNAL_TYPES, "rights", "Rights", BaseRights,
                             configuration)


def intersect(a: str, b: str) -> str:
    """Intersect two lists of rights.

    Returns all rights that are both in ``a`` and ``b``.

    """
    return "".join(set(a).intersection(set(b)))


# Group memberships used to evaluate the request being served.
#
# Not instance state: ApplicationBase builds exactly one rights object for the
# whole process, so an instance/class attribute here is shared by every
# concurrent request. app/__init__.py assigns this per request from the auth
# backend, and from_file.Rights intersects it with the configured allowed
# groups - so a leak across requests means one user's group memberships
# deciding another user's access. See the matching note in auth/__init__.py.
# Defaults to None rather than an empty set: a mutable default on a
# ContextVar would be shared by every context that never assigned one.
_user_groups_var: "contextvars.ContextVar[Optional[Set[str]]]" = (
    contextvars.ContextVar("moreradicale_user_groups", default=None))


class BaseRights:

    # Bound as a class attribute so subclasses share the one ContextVar.
    _user_groups_var = _user_groups_var

    @property
    def _user_groups(self) -> Set[str]:
        """Groups for the request currently being served on this thread."""
        return self._user_groups_var.get() or set()

    @_user_groups.setter
    def _user_groups(self, value: Set[str]) -> None:
        self._user_groups_var.set(value)

    def __init__(self, configuration: "config.Configuration") -> None:
        """Initialize BaseRights.

        ``configuration`` see ``moreradicale.config`` module.
        The ``configuration`` must not change during the lifetime of
        this object, it is kept as an internal reference.

        """
        self.configuration = configuration

    def set_tenant_context(self, context: Optional["TenantContext"]) -> None:
        """Set tenant context for rights checking.

        This is a no-op for most rights backends. Override in tenant-aware
        backends like ``tenant_owner_only``.

        Args:
            context: TenantContext from request, or None

        """
        pass  # Default no-op for non-tenant-aware backends

    def authorization(self, user: str, path: str) -> str:
        """Get granted rights of ``user`` for the collection ``path``.

        If ``user`` is empty, check for anonymous rights.

        ``path`` is sanitized.

        Returns granted rights (e.g. ``"RW"``).

        """
        raise NotImplementedError

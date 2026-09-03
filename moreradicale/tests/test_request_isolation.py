# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2026 Ryan Malloy and contributors
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
Per-request state must not leak between concurrent requests.

ApplicationBase builds exactly one auth, storage and rights object for the
whole process. Several code paths used to write per-request identity onto
those singletons:

  - LDAP group memberships (auth -> rights), used to decide access
  - the tenant context, used to resolve the storage root directory
  - the calendar filter's floating timezone, used to interpret floating times

asgiref's WsgiToAsgi serialized every WSGI call through a single thread, so
those writes happened to be safe. Replacing it with a real thread pool made
them live, and an adversarial review demonstrated privilege escalation and
cross-tenant access as a result. The state now lives in ContextVars.

Each test forces the interleaving that breaks shared state: every thread
writes a distinct value, all threads meet at a barrier so no write can be
read back before the others have landed, then each thread reads its own
value. Without isolation a thread reads a neighbour's value.

A plain "run N requests concurrently and see if it works" test does NOT
catch this - the window is small and usually closes before anything reads.
The barrier is what makes it deterministic.
"""

import concurrent.futures
import threading

from moreradicale.auth import BaseAuth
from moreradicale.item import filter as radicale_filter
from moreradicale.rights import BaseRights
from moreradicale.tenant import current_tenant

THREADS = 8


def _run_interleaved(body):
    """Run `body(i)` on THREADS threads, all rendezvousing mid-way."""
    barrier = threading.Barrier(THREADS)
    problems = []

    def worker(i):
        try:
            body(i, barrier, problems)
        except threading.BrokenBarrierError:  # pragma: no cover
            problems.append(("barrier broken", i, None))

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as ex:
        list(ex.map(worker, range(THREADS)))
    return problems


class TestPerRequestIsolation:

    def test_ldap_groups_do_not_leak_between_threads(self):
        """One user's LDAP groups must not decide another user's access."""
        auth = BaseAuth.__new__(BaseAuth)   # the single shared instance

        def body(i, barrier, problems):
            mine = {"cn=group-%d" % i}
            auth._ldap_groups = mine
            barrier.wait(timeout=10)
            if auth._ldap_groups != mine:
                problems.append(("ldap_groups", i, auth._ldap_groups))

        assert _run_interleaved(body) == []

    def test_user_groups_do_not_leak_between_threads(self):
        """Same, on the rights side, which is what actually grants access."""
        rights = BaseRights.__new__(BaseRights)

        def body(i, barrier, problems):
            mine = {"cn=group-%d" % i}
            rights._user_groups = mine
            barrier.wait(timeout=10)
            if rights._user_groups != mine:
                problems.append(("user_groups", i, rights._user_groups))

        assert _run_interleaved(body) == []

    def test_tenant_context_does_not_leak_between_threads(self):
        """A leak here resolves the storage root to another tenant's data."""
        def body(i, barrier, problems):
            mine = "tenant-%d" % i
            current_tenant.set(mine)
            barrier.wait(timeout=10)
            if current_tenant.get() != mine:
                problems.append(("tenant", i, current_tenant.get()))

        assert _run_interleaved(body) == []

    def test_floating_timezone_does_not_leak_between_threads(self):
        """A leak here silently filters events against another calendar's zone.

        This one has no configuration gate - any deployment where a calendar
        sets C:calendar-timezone is exposed, and the failure is wrong output
        rather than an error.
        """
        from datetime import timedelta, timezone

        def body(i, barrier, problems):
            mine = timezone(timedelta(hours=i - 4))
            radicale_filter.set_default_floating_timezone(mine)
            barrier.wait(timeout=10)
            got = radicale_filter.get_default_floating_timezone()
            if got != mine:
                problems.append(("timezone", i, got))
            radicale_filter.set_default_floating_timezone(None)

        assert _run_interleaved(body) == []

    def test_floating_timezone_defaults_to_utc_when_unset(self):
        """Clearing must restore the documented default, not leave a value."""
        import vobject

        radicale_filter.set_default_floating_timezone(None)
        assert (radicale_filter.get_default_floating_timezone() is
                vobject.icalendar.utc)

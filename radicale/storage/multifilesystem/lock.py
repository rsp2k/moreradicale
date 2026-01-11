# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2014 Jean-Marc Martins
# Copyright © 2012-2017 Guillaume Ayoub
# Copyright © 2017-2022 Unrud <unrud@outlook.com>
# Copyright © 2023-2025 Peter Bieringer <pb@bieringer.de>
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

import contextlib
import logging
import os
import shlex
import signal
import subprocess
import sys
from typing import Dict, Iterator

from radicale import config, pathutils, types
from radicale.log import logger
from radicale.storage.multifilesystem.base import CollectionBase, StorageBase


class CollectionPartLock(CollectionBase):

    @types.contextmanager
    def _acquire_cache_lock(self, ns: str = "") -> Iterator[None]:
        if self._storage._lock.locked == "w":
            yield
            return
        cache_folder = self._storage._get_collection_cache_subfolder(self._filesystem_path, ".Radicale.cache", ns)
        self._storage._makedirs_synced(cache_folder)
        lock_path = os.path.join(cache_folder,
                                 ".Radicale.lock" + (".%s" % ns if ns else ""))
        logger.debug("Lock file (CollectionPartLock): %r" % lock_path)
        lock = pathutils.RwLock(lock_path)
        with lock.acquire("w"):
            yield


class StoragePartLock(StorageBase):

    _lock: pathutils.RwLock
    _tenant_locks: Dict[str, pathutils.RwLock]
    _per_tenant_locking: bool
    _hook: str

    def __init__(self, configuration: config.Configuration) -> None:
        super().__init__(configuration)
        lock_path = os.path.join(self._filesystem_folder, ".Radicale.lock")
        logger.debug("Lock file (StoragePartLock): %r" % lock_path)
        self._lock = pathutils.RwLock(lock_path)
        self._tenant_locks = {}
        self._per_tenant_locking = configuration.get("tenant", "per_tenant_locking")
        self._hook = configuration.get("storage", "hook")

    def _get_lock(self) -> pathutils.RwLock:
        """
        Get appropriate lock for current tenant context.

        Returns per-tenant lock if per_tenant_locking is enabled
        and filesystem isolation is active, otherwise returns global lock.
        """
        if (self._per_tenant_locking and
                self._tenant_enabled and
                self._tenant_isolation_mode == "filesystem" and
                self._tenant_context and
                self._tenant_context.is_valid):

            tenant_id = self._tenant_context.tenant_id
            if tenant_id not in self._tenant_locks:
                # Create lock for this tenant
                tenant_lock_path = os.path.join(
                    self._filesystem_folder,
                    "tenants",
                    tenant_id,
                    ".Radicale.lock"
                )
                # Ensure tenant directory exists
                tenant_dir = os.path.dirname(tenant_lock_path)
                os.makedirs(tenant_dir, exist_ok=True)
                logger.debug(
                    "Lock file (per-tenant) for %r: %r",
                    tenant_id, tenant_lock_path
                )
                self._tenant_locks[tenant_id] = pathutils.RwLock(tenant_lock_path)

            return self._tenant_locks[tenant_id]

        return self._lock

    @types.contextmanager
    def acquire_lock(self, mode: str, user: str = "", *args, **kwargs) -> Iterator[None]:
        lock = self._get_lock()
        with lock.acquire(mode):
            yield
            # execute hook
            if mode == "w" and self._hook:
                debug = logger.isEnabledFor(logging.DEBUG)
                # Use new process group for child to prevent terminals
                # from sending SIGINT etc.
                preexec_fn = None
                creationflags = 0
                if sys.platform == "win32":
                    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    # Process group is also used to identify child processes
                    preexec_fn = os.setpgrp
                # optional argument
                path = kwargs.get('path', "")
                request = kwargs.get('request', "NONE")
                to_path = kwargs.get('to_path', "")
                if to_path != "":
                    to_path = shlex.quote(self._get_collection_root_folder() + to_path)
                try:
                    command = self._hook % {
                        "path": shlex.quote(self._get_collection_root_folder() + path),
                        "to_path": to_path,
                        "cwd": shlex.quote(self._filesystem_folder),
                        "request": shlex.quote(request),
                        "user": shlex.quote(user or "Anonymous")}
                except KeyError as e:
                    logger.error("Storage hook contains not supported placeholder %s (skip execution of: %r)" % (e, self._hook))
                    return

                logger.debug("Executing storage hook: '%s'" % command)
                try:
                    p = subprocess.Popen(
                        command, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE if debug else subprocess.DEVNULL,
                        stderr=subprocess.PIPE if debug else subprocess.DEVNULL,
                        shell=True, universal_newlines=True, preexec_fn=preexec_fn,
                        cwd=self._filesystem_folder, creationflags=creationflags)
                except Exception as e:
                    logger.error("Execution of storage hook not successful on 'Popen': %s" % e)
                    return
                logger.debug("Executing storage hook started 'Popen'")
                try:
                    stdout_data, stderr_data = p.communicate()
                except BaseException as e:  # e.g. KeyboardInterrupt or SystemExit
                    logger.error("Execution of storage hook not successful on 'communicate': %s" % e)
                    p.kill()
                    p.wait()
                    return
                finally:
                    if sys.platform != "win32":
                        # Kill remaining children identified by process group
                        with contextlib.suppress(OSError):
                            os.killpg(p.pid, signal.SIGKILL)
                logger.debug("Executing storage hook finished")
                if stdout_data:
                    logger.debug("Captured stdout from storage hook:\n%s", stdout_data)
                if stderr_data:
                    logger.debug("Captured stderr from storage hook:\n%s", stderr_data)
                if p.returncode != 0:
                    logger.error("Execution of storage hook not successful: %s" % subprocess.CalledProcessError(p.returncode, p.args))
                    return

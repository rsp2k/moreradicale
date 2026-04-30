# This file is part of Radicale - CalDAV and CardDAV server
# Rights backend that allows all users (including anonymous) full read/write access
# FOR TESTING PURPOSES ONLY - NOT FOR PRODUCTION USE

from moreradicale import pathutils, rights


class Rights(rights.BaseRights):
    def authorization(self, user: str, path: str) -> str:
        """Allow all access regardless of user or path."""
        sane_path = pathutils.strip_path(path)
        if not sane_path:
            return "RW"  # Root
        if "/" not in sane_path:
            return "RW"  # Principal
        if sane_path.count("/") == 1:
            return "rw"  # Collection
        return "rw"  # Items

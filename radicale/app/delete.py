# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2008 Nicolas Kandel
# Copyright © 2008 Pascal Halter
# Copyright © 2008-2017 Guillaume Ayoub
# Copyright © 2017-2020 Unrud <unrud@outlook.com>
# Copyright © 2024-2025 Peter Bieringer <pb@bieringer.de>
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

import xml.etree.ElementTree as ET
from http import client
from typing import Optional

from radicale import httputils, storage, types, xmlutils
from radicale.app.base import Access, ApplicationBase
from radicale.hook import HookNotificationItem, HookNotificationItemTypes
from radicale.log import logger


def xml_delete(base_prefix: str, path: str, collection: storage.BaseCollection,
               item_href: Optional[str] = None) -> ET.Element:
    """Read and answer DELETE requests.

    Read rfc4918-9.6 for info.

    """
    collection.delete(item_href)

    multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))
    response = ET.Element(xmlutils.make_clark("D:response"))
    multistatus.append(response)

    href_element = ET.Element(xmlutils.make_clark("D:href"))
    href_element.text = xmlutils.make_href(base_prefix, path)
    response.append(href_element)

    status = ET.Element(xmlutils.make_clark("D:status"))
    status.text = xmlutils.make_response(200)
    response.append(status)

    return multistatus


class ApplicationPartDelete(ApplicationBase):

    def do_DELETE(self, environ: types.WSGIEnviron, base_prefix: str,
                  path: str, user: str, remote_host: str, remote_useragent: str) -> types.WSGIResponse:
        """Manage DELETE request."""
        access = Access(self._rights, user, path)
        if not access.check("w"):
            return httputils.NOT_ALLOWED
        with self._storage.acquire_lock("w", user, path=path, request="DELETE"):
            item = next(iter(self._storage.discover(path)), None)
            if not item:
                return httputils.NOT_FOUND
            if not access.check("w", item):
                return httputils.NOT_ALLOWED
            if_match = environ.get("HTTP_IF_MATCH", "*")
            if if_match not in ("*", item.etag):
                # ETag precondition not verified, do not delete item
                return httputils.PRECONDITION_FAILED
            hook_notification_item_list = []
            if isinstance(item, storage.BaseCollection):
                # Allow deletion of notification resources without permit_delete_collection
                is_notification = "/notifications/" in path and path.endswith(".xml")
                if is_notification:
                    # User can only delete their own notifications
                    notification_owner = path.split("/")[1] if path.startswith("/") else path.split("/")[0]
                    if notification_owner != user:
                        logger.warning("User %s attempted to delete %s's notification", user, notification_owner)
                        return httputils.NOT_ALLOWED
                    logger.debug("Allowing notification deletion: %s", path)
                elif self._permit_delete_collection:
                    if access.check("d", item):
                        logger.info("delete of collection is permitted by config/option [rights] permit_delete_collection but explicit forbidden by permission 'd': %s", path)
                        return httputils.NOT_ALLOWED
                else:
                    if not access.check("D", item):
                        logger.info("delete of collection is prevented by config/option [rights] permit_delete_collection and not explicit allowed by permission 'D': %s", path)
                        return httputils.NOT_ALLOWED
                for i in item.get_all():
                    hook_notification_item_list.append(
                        HookNotificationItem(
                            notification_item_type=HookNotificationItemTypes.DELETE,
                            path=access.path,
                            content=i.uid,
                            uid=i.uid,
                            old_content=i.serialize(),  # type: ignore
                            new_content=None
                        )
                    )
                xml_answer = xml_delete(base_prefix, path, item)
            else:
                assert item.collection is not None
                assert item.href is not None

                # RFC 6638: Process iTIP CANCEL if event has attendees
                if self.configuration.get("scheduling", "enabled"):
                    try:
                        from radicale.itip import processor
                        itip_processor = processor.ITIPProcessor(self._storage, self.configuration)
                        itip_processor.process_delete(item, user)
                    except Exception as e:
                        logger.warning("Failed to process iTIP CANCEL: %s", e)
                        # Continue with deletion even if CANCEL delivery fails

                hook_notification_item_list.append(
                    HookNotificationItem(
                        notification_item_type=HookNotificationItemTypes.DELETE,
                        path=access.path,
                        content=item.uid,
                        uid=item.uid,
                        old_content=item.serialize(),  # type: ignore
                        new_content=None,
                    )
                )
                xml_answer = xml_delete(
                    base_prefix, path, item.collection, item.href)
            for notification_item in hook_notification_item_list:
                self._hook.notify(notification_item)
            headers = {"Content-Type": "text/xml; charset=%s" % self._encoding}
            return client.OK, headers, self._xml_response(xml_answer), None

# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2008 Nicolas Kandel
# Copyright © 2008 Pascal Halter
# Copyright © 2008-2017 Guillaume Ayoub
# Copyright © 2017-2021 Unrud <unrud@outlook.com>
# Copyright © 2025-2025 Peter Bieringer <pb@bieringer.de>
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

import collections
import itertools
import json
import posixpath
import socket
import xml.etree.ElementTree as ET
from http import client
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from radicale import config, httputils, pathutils, rights, storage, types, xmlutils
from radicale.app.base import Access, ApplicationBase
from radicale.log import logger
from radicale.sharing import (SHARES_PROPERTY, PROXY_READ_PROPERTY,
                               PROXY_WRITE_PROPERTY, InviteStatus, ShareAccess)


def _add_proxy_for_elements(element: ET.Element, user: str, proxy_type: str,
                            base_prefix: str, storage_module,
                            configuration: 'config.Configuration') -> None:
    """Add D:href elements for principals that user can proxy for.

    This scans all principals to find those who have granted the user
    proxy access (read or write).

    Args:
        element: Parent XML element to append hrefs to
        user: Username to check proxy access for
        proxy_type: "read" or "write"
        base_prefix: URL base prefix
        storage_module: Storage module for discovering principals
        configuration: Radicale configuration
    """
    import os
    from radicale import pathutils

    prop_name = PROXY_READ_PROPERTY if proxy_type == "read" else PROXY_WRITE_PROPERTY

    # Get the storage folder path
    filesystem_folder = configuration.get("storage", "filesystem_folder")
    collection_root = os.path.join(filesystem_folder, "collection-root")

    if not os.path.isdir(collection_root):
        return

    try:
        for entry in os.scandir(collection_root):
            if not entry.is_dir():
                continue

            principal_name = entry.name
            if not pathutils.is_safe_filesystem_path_component(principal_name):
                continue

            # Skip user's own principal
            if principal_name == user:
                continue

            # Check if this principal has granted us proxy access
            props_path = os.path.join(entry.path, ".Radicale.props")
            if not os.path.isfile(props_path):
                continue

            try:
                with open(props_path, encoding="utf-8") as f:
                    props = json.load(f)

                proxy_json = props.get(prop_name)
                if proxy_json:
                    proxies = json.loads(proxy_json)
                    if user in proxies:
                        # User has proxy access to this principal
                        href_elem = ET.Element(xmlutils.make_clark("D:href"))
                        href_elem.text = xmlutils.make_href(
                            base_prefix, f"/{principal_name}/")
                        element.append(href_elem)
            except (OSError, json.JSONDecodeError):
                continue

    except OSError:
        pass


def xml_propfind(base_prefix: str, path: str,
                 xml_request: Optional[ET.Element],
                 allowed_items: Iterable[Tuple[types.CollectionOrItem, str]],
                 user: str, encoding: str, max_resource_size: int,
                 configuration: 'config.Configuration') -> Optional[ET.Element]:
    """Read and answer PROPFIND requests.

    Read rfc4918-9.1 for info.

    The collections parameter is a list of collections that are to be included
    in the output.

    """
    # A client may choose not to submit a request body.  An empty PROPFIND
    # request body MUST be treated as if it were an 'allprop' request.
    top_element = (xml_request[0] if xml_request is not None else
                   ET.Element(xmlutils.make_clark("D:allprop")))

    props: List[str] = []
    allprop = False
    propname = False
    if top_element.tag == xmlutils.make_clark("D:allprop"):
        allprop = True
    elif top_element.tag == xmlutils.make_clark("D:propname"):
        propname = True
    elif top_element.tag == xmlutils.make_clark("D:prop"):
        props.extend(prop.tag for prop in top_element)

    if xmlutils.make_clark("D:current-user-principal") in props and not user:
        # Ask for authentication
        # Returning the DAV:unauthenticated pseudo-principal as specified in
        # RFC 5397 doesn't seem to work with DAVx5.
        return None

    # Writing answer
    multistatus = ET.Element(xmlutils.make_clark("D:multistatus"))

    for item, permission in allowed_items:
        write = permission == "w"
        multistatus.append(xml_propfind_response(
            base_prefix, path, item, props, user, encoding, max_resource_size,
            configuration, write=write,
            allprop=allprop, propname=propname))

    return multistatus


def xml_propfind_response(
        base_prefix: str, path: str, item: types.CollectionOrItem,
        props: Sequence[str], user: str, encoding: str, max_resource_size: int,
        configuration: 'config.Configuration', write: bool = False,
        propname: bool = False, allprop: bool = False) -> ET.Element:
    """Build and return a PROPFIND response."""
    if propname and allprop or (props and (propname or allprop)):
        raise ValueError("Only use one of props, propname and allprops")

    if isinstance(item, storage.BaseCollection):
        is_collection = True
        is_leaf = item.tag in ("VADDRESSBOOK", "VCALENDAR", "VSUBSCRIBED",
                                "SCHEDULING-INBOX", "SCHEDULING-OUTBOX")
        collection = item
        # Some clients expect collections to end with `/`
        uri = pathutils.unstrip_path(item.path, True)
    else:
        is_collection = is_leaf = False
        assert item.collection is not None
        assert item.href
        collection = item.collection
        uri = pathutils.unstrip_path(posixpath.join(
            collection.path, item.href))
    response = ET.Element(xmlutils.make_clark("D:response"))
    href = ET.Element(xmlutils.make_clark("D:href"))
    href.text = xmlutils.make_href(base_prefix, uri)
    response.append(href)

    if propname or allprop:
        props = []
        # Should list all properties that can be retrieved by the code below
        props.append(xmlutils.make_clark("D:principal-collection-set"))
        props.append(xmlutils.make_clark("D:current-user-principal"))
        props.append(xmlutils.make_clark("D:current-user-privilege-set"))
        props.append(xmlutils.make_clark("D:supported-report-set"))
        props.append(xmlutils.make_clark("D:resourcetype"))
        props.append(xmlutils.make_clark("D:owner"))
        if not allprop:
            # RFC4791#5.2.5: SHOULD NOT be returned by a PROPFIND DAV:allprop request
            props.append(xmlutils.make_clark("C:max-resource-size"))

        if is_collection and collection.is_principal:
            props.append(xmlutils.make_clark("C:calendar-user-address-set"))
            props.append(xmlutils.make_clark("D:principal-URL"))
            props.append(xmlutils.make_clark("CR:addressbook-home-set"))
            props.append(xmlutils.make_clark("C:calendar-home-set"))
            # RFC 6638 scheduling properties
            props.append(xmlutils.make_clark("C:schedule-inbox-URL"))
            props.append(xmlutils.make_clark("C:schedule-outbox-URL"))
            props.append(xmlutils.make_clark("C:schedule-default-calendar-URL"))
            props.append(xmlutils.make_clark("C:calendar-user-type"))
            # RFC 8607 managed attachments properties
            if configuration.get("attachments", "enabled"):
                props.append(xmlutils.make_clark("C:max-attachment-size"))
                props.append(xmlutils.make_clark("C:max-attachments-per-resource"))
            # RFC 8030 Web Push properties
            if configuration.get("push", "enabled"):
                props.append(xmlutils.make_clark("D:push-transports"))
                props.append(xmlutils.make_clark("CS:pushkey"))

        if not is_collection or is_leaf:
            props.append(xmlutils.make_clark("D:getetag"))
            props.append(xmlutils.make_clark("D:getlastmodified"))
            props.append(xmlutils.make_clark("D:getcontenttype"))
            props.append(xmlutils.make_clark("D:getcontentlength"))

        if is_collection:
            if is_leaf:
                props.append(xmlutils.make_clark("D:displayname"))
                props.append(xmlutils.make_clark("D:sync-token"))
            if collection.tag == "VCALENDAR":
                props.append(xmlutils.make_clark("CS:getctag"))
                props.append(
                    xmlutils.make_clark("C:supported-calendar-component-set"))

            meta = collection.get_meta()
            for tag in meta:
                if tag == "tag":
                    continue
                clark_tag = xmlutils.make_clark(tag)
                if clark_tag not in props:
                    props.append(clark_tag)

    responses: Dict[int, List[ET.Element]] = collections.defaultdict(list)
    if propname:
        for tag in props:
            responses[200].append(ET.Element(tag))
        props = []
    for tag in props:
        element = ET.Element(tag)
        is404 = False
        if tag == xmlutils.make_clark("D:getetag"):
            if not is_collection or is_leaf:
                element.text = item.etag
            else:
                is404 = True
        elif tag == xmlutils.make_clark("D:getlastmodified"):
            if not is_collection or is_leaf:
                element.text = item.last_modified
            else:
                is404 = True
        elif tag == xmlutils.make_clark("D:principal-collection-set"):
            child_element = ET.Element(xmlutils.make_clark("D:href"))
            child_element.text = xmlutils.make_href(base_prefix, "/")
            element.append(child_element)
        elif tag == xmlutils.make_clark("C:calendar-user-address-set") and is_collection:
            # RFC 6638: Return mailto: URI for calendar user address
            # Works for both principals and calendar collections
            if collection.is_principal:
                # Direct principal request: /alice/
                username = path.strip("/")
            else:
                # Calendar collection: /alice/calendar/ -> extract "alice"
                path_parts = path.strip("/").split("/")
                username = path_parts[0] if path_parts else None

            if username:
                internal_domain = configuration.get("scheduling", "internal_domain")
                child_element = ET.Element(xmlutils.make_clark("D:href"))
                child_element.text = f"mailto:{username}@{internal_domain}"
                element.append(child_element)
            else:
                is404 = True
        elif (tag in (xmlutils.make_clark("D:principal-URL"),
                      xmlutils.make_clark("CR:addressbook-home-set"),
                      xmlutils.make_clark("C:calendar-home-set")) and
              is_collection and collection.is_principal):
            child_element = ET.Element(xmlutils.make_clark("D:href"))
            child_element.text = xmlutils.make_href(base_prefix, path)
            element.append(child_element)
        # RFC 6638 scheduling property handlers
        elif tag == xmlutils.make_clark("C:schedule-inbox-URL") and is_collection:
            # Works for both principals and calendar collections
            if collection.is_principal:
                # Direct principal request: /alice/
                principal_path = path
            else:
                # Calendar collection: /alice/calendar/ -> /alice/
                path_parts = path.strip("/").split("/")
                principal_path = f"/{path_parts[0]}/" if path_parts else None

            if principal_path:
                child_element = ET.Element(xmlutils.make_clark("D:href"))
                child_element.text = xmlutils.make_href(base_prefix, principal_path + "schedule-inbox/")
                element.append(child_element)
            else:
                is404 = True
        elif tag == xmlutils.make_clark("C:schedule-outbox-URL") and is_collection:
            # Works for both principals and calendar collections
            if collection.is_principal:
                # Direct principal request: /alice/
                principal_path = path
            else:
                # Calendar collection: /alice/calendar/ -> /alice/
                path_parts = path.strip("/").split("/")
                principal_path = f"/{path_parts[0]}/" if path_parts else None

            if principal_path:
                child_element = ET.Element(xmlutils.make_clark("D:href"))
                child_element.text = xmlutils.make_href(base_prefix, principal_path + "schedule-outbox/")
                element.append(child_element)
            else:
                is404 = True
        elif (tag == xmlutils.make_clark("C:schedule-default-calendar-URL") and
              is_collection and collection.is_principal):
            # RFC 6638 §9.2: Point to the principal's default scheduling calendar
            # Look for a calendar collection under the principal
            default_cal_path = None
            try:
                # First check if principal has a "calendar" collection (common default)
                calendar_path = f"{path}calendar/"
                discovered = list(storage.discover(calendar_path, depth="0"))
                if discovered:
                    cal = discovered[0]
                    if hasattr(cal, 'get_meta') and cal.get_meta("tag") == "VCALENDAR":
                        default_cal_path = calendar_path

                # If no "calendar", look for first VCALENDAR collection
                if not default_cal_path:
                    discovered = list(storage.discover(path, depth="1"))
                    for resource in discovered:
                        if resource.path != path and hasattr(resource, 'get_meta'):
                            if resource.get_meta("tag") == "VCALENDAR":
                                default_cal_path = resource.path
                                break
            except Exception:
                pass

            if default_cal_path:
                child_element = ET.Element(xmlutils.make_clark("D:href"))
                child_element.text = xmlutils.make_href(base_prefix, default_cal_path)
                element.append(child_element)
            # If no calendar found, return empty element (valid per RFC)
        elif tag == xmlutils.make_clark("C:calendar-user-type") and is_collection:
            # RFC 6638 Section 2.4.2: INDIVIDUAL, GROUP, RESOURCE, ROOM, UNKNOWN
            # Default to INDIVIDUAL for user principals and their calendars
            element.text = "INDIVIDUAL"
        elif tag == xmlutils.make_clark("D:push-transports") and is_collection:
            # RFC 8030 Web Push transports
            if configuration.get("push", "enabled"):
                # Advertise web-push transport
                transport = ET.Element(xmlutils.make_clark("D:web-push"))
                # Add subscription URL
                sub_url = ET.Element(xmlutils.make_clark("D:subscription-url"))
                sub_url.text = xmlutils.make_href(base_prefix, "/.push/subscribe")
                transport.append(sub_url)
                element.append(transport)
            else:
                is404 = True
        elif tag == xmlutils.make_clark("CS:pushkey") and is_collection:
            # CalendarServer pushkey for subscribing to changes
            if configuration.get("push", "enabled") and is_leaf:
                from radicale.push.subscription import generate_pushkey
                pushkey = generate_pushkey(path, user or "")
                element.text = pushkey
            else:
                is404 = True
        elif tag == xmlutils.make_clark("C:supported-calendar-component-set"):
            human_tag = xmlutils.make_human_tag(tag)
            if is_collection and is_leaf:
                components_text = collection.get_meta(human_tag)
                if components_text:
                    components = components_text.split(",")
                else:
                    components = ["VTODO", "VEVENT", "VJOURNAL"]
                for component in components:
                    comp = ET.Element(xmlutils.make_clark("C:comp"))
                    comp.set("name", component)
                    element.append(comp)
            else:
                is404 = True
        elif tag == xmlutils.make_clark("D:current-user-principal"):
            if user:
                child_element = ET.Element(xmlutils.make_clark("D:href"))
                child_element.text = xmlutils.make_href(
                    base_prefix, "/%s/" % user)
                element.append(child_element)
            else:
                element.append(ET.Element(
                    xmlutils.make_clark("D:unauthenticated")))
        elif tag == xmlutils.make_clark("D:current-user-privilege-set"):
            privileges = ["D:read"]
            if write:
                privileges.append("D:all")
                privileges.append("D:write")
                privileges.append("D:write-properties")
                privileges.append("D:write-content")
                # RFC 6638 scheduling privileges
                if is_collection and is_leaf:
                    if collection.tag == "VCALENDAR":
                        privileges.append("C:schedule")
                    elif collection.tag == "SCHEDULING-INBOX":
                        privileges.append("C:schedule-deliver")
                    elif collection.tag == "SCHEDULING-OUTBOX":
                        privileges.append("C:schedule-send")
            for human_tag in privileges:
                privilege = ET.Element(xmlutils.make_clark("D:privilege"))
                privilege.append(ET.Element(
                    xmlutils.make_clark(human_tag)))
                element.append(privilege)
        elif tag == xmlutils.make_clark("D:supported-report-set"):
            # These 3 reports are not implemented
            reports = ["D:expand-property",
                       "D:principal-search-property-set",
                       "D:principal-property-search"]
            if is_collection and is_leaf:
                reports.append("D:sync-collection")
                if collection.tag == "VADDRESSBOOK":
                    reports.append("CR:addressbook-multiget")
                    reports.append("CR:addressbook-query")
                elif collection.tag == "VCALENDAR":
                    reports.append("C:calendar-multiget")
                    reports.append("C:calendar-query")
            for human_tag in reports:
                supported_report = ET.Element(
                    xmlutils.make_clark("D:supported-report"))
                report_element = ET.Element(xmlutils.make_clark("D:report"))
                report_element.append(
                    ET.Element(xmlutils.make_clark(human_tag)))
                supported_report.append(report_element)
                element.append(supported_report)
        elif tag == xmlutils.make_clark("D:getcontentlength"):
            if not is_collection or is_leaf:
                element.text = str(len(item.serialize().encode(encoding)))
            else:
                is404 = True
        elif tag == xmlutils.make_clark("D:owner"):
            # return empty elment, if no owner available (rfc3744-5.1)
            if collection.owner:
                child_element = ET.Element(xmlutils.make_clark("D:href"))
                child_element.text = xmlutils.make_href(
                    base_prefix, "/%s/" % collection.owner)
                element.append(child_element)
        elif tag == xmlutils.make_clark("C:max-resource-size"):
            # RFC4791#5.2.5
            element.text = str(max_resource_size)
        # RFC 8607 managed attachment properties
        elif tag == xmlutils.make_clark("C:max-attachment-size"):
            # RFC8607: Maximum size of a single managed attachment
            if configuration.get("attachments", "enabled"):
                element.text = str(configuration.get("attachments", "max_size"))
            else:
                is404 = True
        elif tag == xmlutils.make_clark("C:max-attachments-per-resource"):
            # RFC8607: Maximum managed attachments per calendar object
            if configuration.get("attachments", "enabled"):
                element.text = str(configuration.get("attachments", "max_per_resource"))
            else:
                is404 = True
        elif is_collection:
            if tag == xmlutils.make_clark("D:getcontenttype"):
                if is_leaf:
                    element.text = xmlutils.MIMETYPES[
                        collection.tag]
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("D:resourcetype"):
                if collection.is_principal:
                    child_element = ET.Element(
                        xmlutils.make_clark("D:principal"))
                    element.append(child_element)
                if is_leaf:
                    if collection.tag == "VADDRESSBOOK":
                        child_element = ET.Element(
                            xmlutils.make_clark("CR:addressbook"))
                        element.append(child_element)
                    elif collection.tag == "VCALENDAR":
                        child_element = ET.Element(
                            xmlutils.make_clark("C:calendar"))
                        element.append(child_element)
                    elif collection.tag == "VSUBSCRIBED":
                        child_element = ET.Element(
                            xmlutils.make_clark("CS:subscribed"))
                        element.append(child_element)
                    elif collection.tag == "SCHEDULING-INBOX":
                        child_element = ET.Element(
                            xmlutils.make_clark("C:schedule-inbox"))
                        element.append(child_element)
                    elif collection.tag == "SCHEDULING-OUTBOX":
                        child_element = ET.Element(
                            xmlutils.make_clark("C:schedule-outbox"))
                        element.append(child_element)
                child_element = ET.Element(xmlutils.make_clark("D:collection"))
                element.append(child_element)
            elif tag == xmlutils.make_clark("RADICALE:displayname"):
                # Only for internal use by the web interface
                displayname = collection.get_meta("D:displayname")
                if displayname is not None:
                    element.text = displayname
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("RADICALE:getcontentcount"):
                # Only for internal use by the web interface
                if isinstance(item, storage.BaseCollection) and not collection.is_principal:
                    element.text = str(sum(1 for x in item.get_all()))
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("D:displayname"):
                displayname = collection.get_meta("D:displayname")
                if not displayname and is_leaf:
                    displayname = collection.path
                if displayname is not None:
                    element.text = displayname
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("CS:getctag"):
                if is_leaf:
                    element.text = collection.etag
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("D:sync-token"):
                if is_leaf:
                    element.text, _ = collection.sync()
                else:
                    is404 = True
            # Calendar sharing properties (CalendarServer extension)
            elif tag == xmlutils.make_clark("CS:invite"):
                # Returns share invitations for this calendar
                if is_leaf and collection.tag == "VCALENDAR":
                    sharing_enabled = configuration.get("sharing", "enabled")
                    if sharing_enabled:
                        shares_json = collection.get_meta(SHARES_PROPERTY)
                        if shares_json:
                            try:
                                shares = json.loads(shares_json)
                                for sharee, share_data in shares.items():
                                    # Build CS:user element for each sharee
                                    user_elem = ET.Element(xmlutils.make_clark("CS:user"))

                                    # CS:href - user's principal URL
                                    href_elem = ET.Element(xmlutils.make_clark("D:href"))
                                    href_elem.text = xmlutils.make_href(base_prefix, f"/{sharee}/")
                                    user_elem.append(href_elem)

                                    # CS:common-name (optional)
                                    cn = share_data.get("cn")
                                    if cn:
                                        cn_elem = ET.Element(xmlutils.make_clark("CS:common-name"))
                                        cn_elem.text = cn
                                        user_elem.append(cn_elem)

                                    # CS:invite-accepted, CS:invite-noresponse, or CS:invite-declined
                                    status = share_data.get("status", "pending")
                                    if status == InviteStatus.ACCEPTED.value:
                                        status_elem = ET.Element(xmlutils.make_clark("CS:invite-accepted"))
                                    elif status == InviteStatus.DECLINED.value:
                                        status_elem = ET.Element(xmlutils.make_clark("CS:invite-declined"))
                                    else:
                                        status_elem = ET.Element(xmlutils.make_clark("CS:invite-noresponse"))
                                    user_elem.append(status_elem)

                                    # CS:access - read-only or read-write
                                    access = share_data.get("access", "read")
                                    access_elem = ET.Element(xmlutils.make_clark("CS:access"))
                                    if access == ShareAccess.READ_WRITE.value:
                                        access_elem.append(ET.Element(xmlutils.make_clark("CS:read-write")))
                                    else:
                                        access_elem.append(ET.Element(xmlutils.make_clark("CS:read")))
                                    user_elem.append(access_elem)

                                    element.append(user_elem)
                            except json.JSONDecodeError:
                                pass
                    # Empty element if no shares (valid per CalendarServer extension)
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("CS:allowed-sharing-modes"):
                # Indicates what sharing modes the server supports
                if is_leaf and collection.tag == "VCALENDAR":
                    sharing_enabled = configuration.get("sharing", "enabled")
                    if sharing_enabled:
                        # Server supports both read and read-write sharing
                        can_read = ET.Element(xmlutils.make_clark("CS:can-be-shared"))
                        element.append(can_read)
                    else:
                        is404 = True
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("CS:shared-url"):
                # For shared calendars, returns the URL of the original calendar
                # Only present on calendars that were shared TO the current user
                if is_leaf and collection.tag == "VCALENDAR":
                    sharing_enabled = configuration.get("sharing", "enabled")
                    if sharing_enabled and user:
                        # Check if current user is NOT the owner (i.e., viewing shared calendar)
                        if collection.owner and collection.owner != user:
                            # This is a shared calendar - return the canonical URL
                            child_element = ET.Element(xmlutils.make_clark("D:href"))
                            child_element.text = xmlutils.make_href(base_prefix, uri)
                            element.append(child_element)
                        else:
                            # User is the owner, property not applicable
                            is404 = True
                    else:
                        is404 = True
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("CS:calendar-proxy-read-for"):
                # Returns principals that the current user can proxy-read for
                if is_collection and collection.is_principal and user:
                    delegation_enabled = configuration.get("sharing", "delegation_enabled")
                    if delegation_enabled:
                        # Scan all principals to find who has granted us read proxy
                        _add_proxy_for_elements(element, user, "read",
                                               base_prefix, storage, configuration)
                    # Empty element is valid if no proxies
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("CS:calendar-proxy-write-for"):
                # Returns principals that the current user can proxy-write for
                if is_collection and collection.is_principal and user:
                    delegation_enabled = configuration.get("sharing", "delegation_enabled")
                    if delegation_enabled:
                        # Scan all principals to find who has granted us write proxy
                        _add_proxy_for_elements(element, user, "write",
                                               base_prefix, storage, configuration)
                    # Empty element is valid if no proxies
                else:
                    is404 = True
            elif tag == xmlutils.make_clark("CS:source"):
                if is_leaf:
                    child_element = ET.Element(xmlutils.make_clark("D:href"))
                    child_element.text = collection.get_meta('CS:source')
                    element.append(child_element)
                else:
                    is404 = True
            else:
                human_tag = xmlutils.make_human_tag(tag)
                tag_text = collection.get_meta(human_tag)
                if tag_text is not None:
                    element.text = tag_text
                else:
                    is404 = True
        # Not for collections
        elif tag == xmlutils.make_clark("D:getcontenttype"):
            assert not isinstance(item, storage.BaseCollection)
            element.text = xmlutils.get_content_type(item, encoding)
        elif tag == xmlutils.make_clark("D:resourcetype"):
            # resourcetype must be returned empty for non-collection elements
            pass
        else:
            is404 = True

        responses[404 if is404 else 200].append(element)

    for status_code, children in responses.items():
        if not children:
            continue
        propstat = ET.Element(xmlutils.make_clark("D:propstat"))
        response.append(propstat)
        prop = ET.Element(xmlutils.make_clark("D:prop"))
        prop.extend(children)
        propstat.append(prop)
        status = ET.Element(xmlutils.make_clark("D:status"))
        status.text = xmlutils.make_response(status_code)
        propstat.append(status)

    return response


class ApplicationPartPropfind(ApplicationBase):

    def _collect_allowed_items(
            self, items: Iterable[types.CollectionOrItem], user: str
            ) -> Iterator[Tuple[types.CollectionOrItem, str]]:
        """Get items from request that user is allowed to access."""
        for item in items:
            if isinstance(item, storage.BaseCollection):
                path = pathutils.unstrip_path(item.path, True)
                if item.tag:
                    permissions = rights.intersect(
                        self._rights.authorization(user, path), "rw")
                    target = "collection with tag %r" % item.path
                else:
                    permissions = rights.intersect(
                        self._rights.authorization(user, path), "RW")
                    target = "collection %r" % item.path
            else:
                assert item.collection is not None
                path = pathutils.unstrip_path(item.collection.path, True)
                permissions = rights.intersect(
                    self._rights.authorization(user, path), "rw")
                target = "item %r from %r" % (item.href, item.collection.path)
            if rights.intersect(permissions, "Ww"):
                permission = "w"
                status = "write"
            elif rights.intersect(permissions, "Rr"):
                permission = "r"
                status = "read"
            else:
                permission = ""
                status = "NO"
            logger.debug(
                "%s has %s access to %s",
                repr(user) if user else "anonymous user", status, target)
            if permission:
                yield item, permission

    def do_PROPFIND(self, environ: types.WSGIEnviron, base_prefix: str,
                    path: str, user: str, remote_host: str, remote_useragent: str) -> types.WSGIResponse:
        """Manage PROPFIND request."""
        access = Access(self._rights, user, path)
        if not access.check("r"):
            return httputils.NOT_ALLOWED
        try:
            xml_content = self._read_xml_request_body(environ)
        except RuntimeError as e:
            logger.warning(
                "Bad PROPFIND request on %r: %s", path, e, exc_info=True)
            return httputils.BAD_REQUEST
        except socket.timeout:
            logger.debug("Client timed out", exc_info=True)
            return httputils.REQUEST_TIMEOUT
        with self._storage.acquire_lock("r", user):
            items_iter = iter(self._storage.discover(
                path, environ.get("HTTP_DEPTH", "0"),
                None, self._rights._user_groups))
            # take root item for rights checking
            item = next(items_iter, None)
            if not item:
                return httputils.NOT_FOUND
            if not access.check("r", item):
                return httputils.NOT_ALLOWED
            # put item back
            items_iter = itertools.chain([item], items_iter)
            allowed_items = self._collect_allowed_items(items_iter, user)
            headers = {"DAV": httputils.get_dav_headers(self.configuration),
                       "Content-Type": "text/xml; charset=%s" % self._encoding}
            xml_answer = xml_propfind(base_prefix, path, xml_content,
                                      allowed_items, user, self._encoding, max_resource_size=self._max_resource_size,
                                      configuration=self.configuration)
            if xml_answer is None:
                return httputils.NOT_ALLOWED
            return client.MULTI_STATUS, headers, self._xml_response(xml_answer), xmlutils.pretty_xml(xml_content)

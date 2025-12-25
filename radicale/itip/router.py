# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025-2025 RFC 6638 Scheduling Implementation
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
Attendee routing for iTIP messages.

Determines whether attendees are internal (same Radicale server)
or external (require email delivery).
"""

from typing import Optional, Tuple

from radicale.log import logger


def route_attendee(attendee_email: str, storage, configuration) -> Tuple[bool, Optional[str]]:
    """Determine if attendee is internal (local Radicale user).

    This function implements the core routing logic for RFC 6638.
    Internal attendees get iTIP messages delivered to their schedule-inbox.
    External attendees would be sent email (Phase 4 feature).

    Args:
        attendee_email: Attendee's email address
        storage: Radicale storage instance
        configuration: Radicale configuration instance

    Returns:
        Tuple of (is_internal, principal_path)
        - is_internal: True if attendee is on this server
        - principal_path: Path like "/username/" if internal, None otherwise

    Example:
        >>> route_attendee("alice@example.com", storage, config)
        (True, "/alice/")
        >>> route_attendee("bob@external.org", storage, config)
        (False, None)
    """
    # Extract domain from email
    if '@' not in attendee_email:
        logger.debug("Invalid email address (no @): %s", attendee_email)
        return False, None

    username, domain = attendee_email.rsplit('@', 1)

    # Check if domain matches internal domain
    internal_domain = configuration.get("scheduling", "internal_domain")
    if not internal_domain:
        # No internal domain configured - all attendees are external
        logger.debug("No internal_domain configured, treating %s as external",
                    attendee_email)
        return False, None

    if domain.lower() != internal_domain.lower():
        logger.debug("Domain %s does not match internal domain %s",
                    domain, internal_domain)
        return False, None

    # Domain matches - check if principal exists
    principal_path = f"/{username}/"

    try:
        with storage.acquire_lock("r"):
            # Try to discover the principal
            discovered = list(storage.discover(principal_path, depth="0"))
            if discovered:
                collection = discovered[0]
                if collection.is_principal:
                    logger.debug("Found internal principal for %s at %s",
                                attendee_email, principal_path)
                    return True, principal_path

        logger.debug("Principal not found for %s", attendee_email)
        return False, None

    except Exception as e:
        logger.warning("Error checking principal %s: %s", principal_path, e)
        return False, None


def get_principal_email(principal_path: str, configuration) -> str:
    """Get email address for a principal.

    Args:
        principal_path: Principal path like "/username/"
        configuration: Radicale configuration instance

    Returns:
        Email address like "username@domain"

    Example:
        >>> get_principal_email("/alice/", config)
        "alice@example.com"
    """
    # Extract username from path
    username = principal_path.strip("/")

    # Get internal domain
    internal_domain = configuration.get("scheduling", "internal_domain")
    if not internal_domain:
        # Fallback to localhost
        internal_domain = "localhost"

    return f"{username}@{internal_domain}"


def validate_organizer_permission(organizer_email: str, user: str,
                                  configuration) -> bool:
    """Check if user is authorized to send iTIP as organizer.

    This prevents users from spoofing invitations from other users.

    Args:
        organizer_email: Email in ORGANIZER property
        user: Authenticated username
        configuration: Radicale configuration instance

    Returns:
        True if user is authorized to send as this organizer

    Example:
        >>> validate_organizer_permission("alice@example.com", "alice", config)
        True
        >>> validate_organizer_permission("bob@example.com", "alice", config)
        False
    """
    # Get user's expected email
    principal_path = f"/{user}/"
    user_email = get_principal_email(principal_path, configuration)

    # Check if organizer email matches user
    if organizer_email.lower() != user_email.lower():
        logger.warning("User %s attempted to send iTIP as organizer %s",
                      user, organizer_email)
        return False

    return True

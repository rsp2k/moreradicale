"""
Attendee routing - determine if attendees are internal or external.
"""

import re
from typing import Tuple, Optional


def extract_email(mailto_uri: str) -> Optional[str]:
    """
    Extract email address from mailto: URI.

    Args:
        mailto_uri: URI like "mailto:user@example.com"

    Returns:
        Email address or None
    """
    if not mailto_uri:
        return None

    # Remove mailto: prefix if present
    email = mailto_uri.lower()
    if email.startswith('mailto:'):
        email = email[7:]

    # Basic email validation
    # Allow both user@example.com and user@localhost formats
    if '@' in email and re.match(r'^[^@]+@[^@]+$', email):
        return email

    return None


def route_attendee(attendee_email: str, storage, base_prefix: str = "") -> Tuple[bool, Optional[str]]:
    """
    Determine if attendee is internal (local Radicale user).

    For simplicity, we consider attendees internal if:
    1. Email username matches a principal path that exists
    2. For example: bob@localhost -> /bob/ principal exists

    Args:
        attendee_email: Email address (e.g., "bob@localhost")
        storage: Radicale storage backend
        base_prefix: Base prefix for paths

    Returns:
        Tuple of (is_internal, principal_path)
    """
    if not attendee_email or '@' not in attendee_email:
        return False, None

    # Extract username from email
    username = attendee_email.split('@')[0]

    # Try to find principal at /<username>/
    principal_path = f"/{username}/"

    try:
        # Check if principal exists (no lock needed - we're already in PUT handler's lock)
        discovered = list(storage.discover(principal_path, depth="0"))
        if discovered:
            collection = discovered[0]
            # Verify it's a principal (has is_principal attribute)
            if hasattr(collection, 'is_principal') and collection.is_principal:
                return True, principal_path
    except Exception:
        pass

    return False, None


def get_inbox_path(principal_path: str) -> str:
    """
    Get schedule-inbox path for a principal.

    Args:
        principal_path: Principal path (e.g., "/bob/")

    Returns:
        Schedule-inbox path (e.g., "/bob/schedule-inbox/")
    """
    # Ensure trailing slash
    if not principal_path.endswith('/'):
        principal_path += '/'

    return f"{principal_path}schedule-inbox/"


def get_principal_email(principal_path: str, configuration) -> str:
    """
    Get email address for a principal.

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
                                  configuration, storage=None) -> bool:
    """
    Check if user is authorized to send iTIP as organizer.

    This prevents users from spoofing invitations from other users.
    Authorization is granted if:
    1. The organizer email matches the user's email (direct match)
    2. The user is a scheduling delegate for the organizer (delegation)

    Args:
        organizer_email: Email in ORGANIZER property
        user: Authenticated username
        configuration: Radicale configuration instance
        storage: Optional storage backend (required for delegation checks)

    Returns:
        True if user is authorized to send as this organizer

    Example:
        >>> validate_organizer_permission("alice@example.com", "alice", config)
        True
        >>> validate_organizer_permission("bob@example.com", "alice", config)
        False
        >>> # If alice is a delegate for bob:
        >>> validate_organizer_permission("bob@example.com", "alice", config, storage)
        True
    """
    from moreradicale.log import logger

    # Get user's expected email
    principal_path = f"/{user}/"
    user_email = get_principal_email(principal_path, configuration)

    # Check if organizer email matches user (direct authorization)
    if organizer_email.lower() == user_email.lower():
        return True

    # Check delegation if storage is provided and delegation is enabled
    delegation_enabled = configuration.get("sharing", "delegation_enabled")
    if storage and delegation_enabled:
        # Extract organizer username from email
        organizer_username = organizer_email.split('@')[0]

        if _check_delegation(user, organizer_username, storage, logger):
            logger.info("User %s authorized as delegate for organizer %s",
                       user, organizer_email)
            return True

    logger.warning("User %s attempted to send iTIP as organizer %s (not authorized)",
                  user, organizer_email)
    return False


def _check_delegation(delegate_user: str, organizer_user: str,
                      storage, logger) -> bool:
    """
    Check if delegate_user can act on behalf of organizer_user.

    Args:
        delegate_user: User claiming to be a delegate
        organizer_user: Principal username to check delegation for
        storage: Radicale storage backend
        logger: Logger instance

    Returns:
        True if delegate_user is in organizer's schedule-delegates
    """
    import json
    from moreradicale.sharing import SCHEDULE_DELEGATES_PROPERTY

    # Get organizer's principal collection
    organizer_principal_path = f"/{organizer_user}/"

    try:
        discovered = list(storage.discover(organizer_principal_path, depth="0"))
        if not discovered:
            logger.debug("Organizer principal %s not found", organizer_principal_path)
            return False

        principal = discovered[0]
        if not hasattr(principal, 'get_meta'):
            logger.debug("Principal %s has no get_meta method", organizer_principal_path)
            return False

        # Get schedule-delegates property
        delegates_json = principal.get_meta(SCHEDULE_DELEGATES_PROPERTY)
        if not delegates_json:
            return False

        delegates = json.loads(delegates_json)
        if delegate_user in delegates:
            logger.debug("User %s is a schedule-delegate for %s",
                        delegate_user, organizer_user)
            return True

    except Exception as e:
        logger.debug("Error checking delegation for %s -> %s: %s",
                    delegate_user, organizer_user, e)

    return False

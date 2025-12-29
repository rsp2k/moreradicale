"""
Attendee routing - determine if attendees are internal or external.
"""

import re
from typing import Tuple, Optional
from radicale import pathutils


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
                                  configuration) -> bool:
    """
    Check if user is authorized to send iTIP as organizer.

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
    from radicale.log import logger

    # Get user's expected email
    principal_path = f"/{user}/"
    user_email = get_principal_email(principal_path, configuration)

    # Check if organizer email matches user
    if organizer_email.lower() != user_email.lower():
        logger.warning("User %s attempted to send iTIP as organizer %s",
                      user, organizer_email)
        return False

    return True

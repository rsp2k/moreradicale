"""
RFC 4331: Quota and Size Properties for DAV Collections.

This module provides quota calculation and reporting for Radicale,
allowing clients to see storage usage and available space.
"""

import os
from pathlib import Path
from typing import Optional, Tuple

from moreradicale.log import logger


def get_directory_size(path: str, include_cache: bool = False) -> int:
    """
    Calculate total size of a directory in bytes.

    Args:
        path: Directory path to measure
        include_cache: Whether to include .Radicale.cache folders

    Returns:
        Total size in bytes
    """
    total_size = 0

    try:
        for dirpath, dirnames, filenames in os.walk(path):
            # Optionally skip cache directories
            if not include_cache:
                # Remove cache dirs from traversal
                dirnames[:] = [d for d in dirnames if not d.startswith(".Radicale.cache")]

            for filename in filenames:
                # Skip lock files and other temp files
                if filename.startswith(".Radicale.lock"):
                    continue

                filepath = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(filepath)
                except (OSError, IOError):
                    # File may have been deleted
                    pass

    except (OSError, IOError) as e:
        logger.warning("Error calculating directory size for %s: %s", path, e)

    return total_size


def get_user_storage_path(configuration, user: str) -> Optional[str]:
    """
    Get the storage path for a specific user.

    Args:
        configuration: Radicale configuration
        user: Username

    Returns:
        Path to user's storage folder, or None if not found
    """
    storage_folder = configuration.get("storage", "filesystem_folder")
    user_path = os.path.join(storage_folder, "collection-root", user)

    if os.path.isdir(user_path):
        return user_path

    return None


def calculate_user_quota(configuration, user: str) -> Tuple[int, int]:
    """
    Calculate quota usage for a user.

    Args:
        configuration: Radicale configuration
        user: Username

    Returns:
        Tuple of (used_bytes, available_bytes)
        available_bytes is -1 if unlimited
    """
    if not configuration.get("quota", "enabled"):
        return (0, -1)

    max_bytes = configuration.get("quota", "max_bytes")
    include_cache = configuration.get("quota", "include_cache")

    # Get user's storage path
    user_path = get_user_storage_path(configuration, user)

    if not user_path:
        # User has no storage yet
        used_bytes = 0
    else:
        used_bytes = get_directory_size(user_path, include_cache)

    # Calculate available bytes
    if max_bytes == 0:
        # Unlimited quota
        available_bytes = -1
    else:
        available_bytes = max(0, max_bytes - used_bytes)

    logger.debug("Quota for user %s: used=%d, available=%d, max=%d",
                 user, used_bytes, available_bytes, max_bytes)

    return (used_bytes, available_bytes)


def check_quota_exceeded(configuration, user: str, additional_bytes: int = 0) -> bool:
    """
    Check if a user's quota would be exceeded.

    Args:
        configuration: Radicale configuration
        user: Username
        additional_bytes: Bytes that would be added

    Returns:
        True if quota would be exceeded
    """
    if not configuration.get("quota", "enabled"):
        return False

    max_bytes = configuration.get("quota", "max_bytes")
    if max_bytes == 0:
        # Unlimited
        return False

    used_bytes, _ = calculate_user_quota(configuration, user)
    return (used_bytes + additional_bytes) > max_bytes


def format_bytes(size: int) -> str:
    """
    Format bytes as human-readable string.

    Args:
        size: Size in bytes

    Returns:
        Human-readable string (e.g., "1.5 MB")
    """
    if size < 0:
        return "unlimited"

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} PB"

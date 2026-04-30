# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 RFC 3253 Activity Support
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
RFC 3253 Activity management.

Activities group related changes together (like feature branches in git).
They help organize parallel development and provide logical change sets.

Activity lifecycle:
1. MKACTIVITY - Create activity
2. CHECKOUT with activity context - Associate checkouts with activity
3. CHECKIN - Versions inherit activity membership
4. Query activity to see all related changes
"""

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ActivityInfo:
    """Information about an activity."""
    activity_id: str  # Unique activity identifier (UUID)
    creator: str  # User who created the activity
    created: str  # ISO 8601 creation timestamp
    display_name: str  # Human-readable activity name
    description: str  # Activity description
    checkouts: List[str]  # List of resource paths checked out in this activity
    versions: List[str]  # List of version SHAs created by this activity

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ActivityInfo":
        """Create from dictionary."""
        return cls(
            activity_id=data.get("activity_id", ""),
            creator=data.get("creator", ""),
            created=data.get("created", ""),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            checkouts=data.get("checkouts", []),
            versions=data.get("versions", [])
        )


class ActivityManager:
    """
    Manage RFC 3253 activities for version control.

    Activities organize related changes into logical units (change sets).
    Each activity tracks:
    - Which resources are checked out as part of the activity
    - Which versions were created by the activity
    - Metadata: creator, timestamp, description

    Storage structure:
    {storage_folder}/
      .activities/
        {activity-id}.json  - Activity metadata and membership
    """

    ACTIVITIES_DIR = ".activities"

    def __init__(self, storage_folder: str):
        """
        Initialize activity manager.

        Args:
            storage_folder: Path to storage folder
        """
        self.storage_folder = storage_folder
        self.activities_path = os.path.join(storage_folder, self.ACTIVITIES_DIR)

        # Ensure activities directory exists
        os.makedirs(self.activities_path, exist_ok=True)

    def _activity_file_path(self, activity_id: str) -> str:
        """Get path to activity metadata file."""
        # Sanitize activity ID for filesystem safety
        safe_id = activity_id.replace("/", "_").replace("\\", "_")
        return os.path.join(self.activities_path, f"{safe_id}.json")

    def activity_exists(self, activity_id: str) -> bool:
        """Check if an activity exists."""
        return os.path.exists(self._activity_file_path(activity_id))

    def create_activity(self, creator: str, display_name: str,
                       description: str = "") -> ActivityInfo:
        """
        Create a new activity.

        Args:
            creator: User creating the activity
            display_name: Human-readable activity name
            description: Optional activity description

        Returns:
            ActivityInfo for the new activity
        """
        # Generate unique activity ID
        activity_id = str(uuid.uuid4())

        # Create activity info
        activity = ActivityInfo(
            activity_id=activity_id,
            creator=creator,
            created=datetime.now(timezone.utc).isoformat(),
            display_name=display_name,
            description=description,
            checkouts=[],
            versions=[]
        )

        # Write to disk
        self._save_activity(activity)

        logger.info("Created activity %s: '%s' by %s",
                    activity_id[:8], display_name, creator)

        return activity

    def get_activity(self, activity_id: str) -> Optional[ActivityInfo]:
        """
        Get activity information.

        Args:
            activity_id: Activity identifier

        Returns:
            ActivityInfo if found, None otherwise
        """
        activity_file = self._activity_file_path(activity_id)

        if not os.path.exists(activity_file):
            return None

        try:
            with open(activity_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ActivityInfo.from_dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read activity %s: %s", activity_id, e)
            return None

    def add_checkout(self, activity_id: str, resource_path: str) -> bool:
        """
        Associate a checkout with an activity.

        Args:
            activity_id: Activity identifier
            resource_path: Path to checked-out resource

        Returns:
            True on success, False if activity doesn't exist
        """
        activity = self.get_activity(activity_id)
        if not activity:
            return False

        # Add checkout if not already present
        if resource_path not in activity.checkouts:
            activity.checkouts.append(resource_path)
            self._save_activity(activity)
            logger.debug("Added checkout %s to activity %s",
                        resource_path, activity_id[:8])

        return True

    def remove_checkout(self, activity_id: str, resource_path: str) -> bool:
        """
        Remove a checkout from an activity (e.g., after CHECKIN).

        Args:
            activity_id: Activity identifier
            resource_path: Path to resource

        Returns:
            True on success, False if activity doesn't exist
        """
        activity = self.get_activity(activity_id)
        if not activity:
            return False

        # Remove checkout if present
        if resource_path in activity.checkouts:
            activity.checkouts.remove(resource_path)
            self._save_activity(activity)
            logger.debug("Removed checkout %s from activity %s",
                        resource_path, activity_id[:8])

        return True

    def add_version(self, activity_id: str, version_sha: str) -> bool:
        """
        Associate a version with an activity.

        Args:
            activity_id: Activity identifier
            version_sha: Git commit SHA

        Returns:
            True on success, False if activity doesn't exist
        """
        activity = self.get_activity(activity_id)
        if not activity:
            return False

        # Add version if not already present
        if version_sha not in activity.versions:
            activity.versions.append(version_sha)
            self._save_activity(activity)
            logger.debug("Added version %s to activity %s",
                        version_sha[:8], activity_id[:8])

        return True

    def list_activities(self, creator: Optional[str] = None) -> List[ActivityInfo]:
        """
        List all activities, optionally filtered by creator.

        Args:
            creator: Optional user filter

        Returns:
            List of ActivityInfo objects
        """
        activities = []

        if not os.path.exists(self.activities_path):
            return activities

        for filename in os.listdir(self.activities_path):
            if not filename.endswith(".json"):
                continue

            activity_id = filename[:-5]  # Remove .json extension
            activity = self.get_activity(activity_id)

            if activity:
                if creator is None or activity.creator == creator:
                    activities.append(activity)

        # Sort by creation time (newest first)
        activities.sort(key=lambda a: a.created, reverse=True)

        return activities

    def delete_activity(self, activity_id: str) -> bool:
        """
        Delete an activity.

        Args:
            activity_id: Activity identifier

        Returns:
            True on success, False if activity doesn't exist
        """
        activity_file = self._activity_file_path(activity_id)

        if not os.path.exists(activity_file):
            return False

        try:
            os.remove(activity_file)
            logger.info("Deleted activity %s", activity_id[:8])
            return True
        except OSError as e:
            logger.warning("Failed to delete activity %s: %s", activity_id, e)
            return False

    def get_activities_for_resource(self, resource_path: str) -> List[str]:
        """
        Get all activities that have checked out a resource.

        Args:
            resource_path: Path to resource

        Returns:
            List of activity IDs
        """
        activity_ids = []

        for activity in self.list_activities():
            if resource_path in activity.checkouts:
                activity_ids.append(activity.activity_id)

        return activity_ids

    def get_activities_for_version(self, version_sha: str) -> List[str]:
        """
        Get all activities that created a version.

        Args:
            version_sha: Git commit SHA

        Returns:
            List of activity IDs
        """
        activity_ids = []

        for activity in self.list_activities():
            if version_sha in activity.versions:
                activity_ids.append(activity.activity_id)

        return activity_ids

    def _save_activity(self, activity: ActivityInfo) -> None:
        """Save activity to disk."""
        activity_file = self._activity_file_path(activity.activity_id)

        try:
            with open(activity_file, "w", encoding="utf-8") as f:
                json.dump(activity.to_dict(), f, indent=2)
        except OSError as e:
            logger.error("Failed to save activity %s: %s",
                        activity.activity_id, e)
            raise

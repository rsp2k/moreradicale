"""
Persistent storage for push subscriptions.

Stores subscriptions as JSON files organized by user and collection.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from moreradicale.log import logger

from .subscription import PushSubscription


class SubscriptionStorage:
    """
    File-based storage for push subscriptions.

    Structure:
        {storage_folder}/.Radicale.push/
            {user}/
                {collection_hash}/
                    {subscription_id}.json
    """

    def __init__(self, configuration):
        """
        Initialize subscription storage.

        Args:
            configuration: Radicale configuration instance
        """
        self._configuration = configuration

        # Get storage folder
        custom_folder = configuration.get("push", "subscription_folder")
        if custom_folder:
            self._base_path = Path(custom_folder)
        else:
            storage_folder = configuration.get("storage", "filesystem_folder")
            self._base_path = Path(storage_folder) / ".Radicale.push"

        # Create base directory if needed
        self._base_path.mkdir(parents=True, exist_ok=True)
        logger.debug("Push subscription storage: %s", self._base_path)

    def _get_user_path(self, user: str) -> Path:
        """Get path for user's subscriptions."""
        # Sanitize username for filesystem
        safe_user = user.replace("/", "_").replace("\\", "_")
        return self._base_path / safe_user

    def _get_collection_path(self, user: str, collection_path: str) -> Path:
        """Get path for collection subscriptions."""
        import hashlib
        # Hash collection path for filesystem safety
        collection_hash = hashlib.sha256(collection_path.encode()).hexdigest()[:16]
        return self._get_user_path(user) / collection_hash

    def _get_subscription_path(self, user: str, collection_path: str, sub_id: str) -> Path:
        """Get path for specific subscription file."""
        return self._get_collection_path(user, collection_path) / f"{sub_id}.json"

    def add_subscription(self, subscription: PushSubscription) -> bool:
        """
        Add a new push subscription.

        Args:
            subscription: PushSubscription to store

        Returns:
            True if successful
        """
        try:
            # Create directory structure
            collection_dir = self._get_collection_path(
                subscription.user,
                subscription.collection_path
            )
            collection_dir.mkdir(parents=True, exist_ok=True)

            # Write subscription file
            sub_path = collection_dir / f"{subscription.id}.json"
            with open(sub_path, "w") as f:
                json.dump(subscription.to_dict(), f, indent=2)

            logger.info("Added push subscription %s for %s on %s",
                       subscription.id, subscription.user, subscription.collection_path)
            return True

        except Exception as e:
            logger.error("Failed to add subscription: %s", e)
            return False

    def remove_subscription(self, subscription_id: str, user: str = None) -> bool:
        """
        Remove a subscription by ID.

        Args:
            subscription_id: ID of subscription to remove
            user: Optional user to search within

        Returns:
            True if subscription was found and removed
        """
        try:
            # Search for subscription file
            if user:
                search_paths = [self._get_user_path(user)]
            else:
                search_paths = list(self._base_path.iterdir()) if self._base_path.exists() else []

            for user_path in search_paths:
                if not user_path.is_dir():
                    continue
                for collection_path in user_path.iterdir():
                    if not collection_path.is_dir():
                        continue
                    sub_file = collection_path / f"{subscription_id}.json"
                    if sub_file.exists():
                        sub_file.unlink()
                        logger.info("Removed subscription %s", subscription_id)
                        # Clean up empty directories
                        self._cleanup_empty_dirs(collection_path)
                        return True

            return False

        except Exception as e:
            logger.error("Failed to remove subscription: %s", e)
            return False

    def _cleanup_empty_dirs(self, path: Path) -> None:
        """Remove empty directories up the tree."""
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                if path.parent != self._base_path:
                    self._cleanup_empty_dirs(path.parent)
        except Exception:
            pass

    def get_subscription(self, subscription_id: str) -> Optional[PushSubscription]:
        """
        Get a subscription by ID.

        Args:
            subscription_id: ID of subscription to find

        Returns:
            PushSubscription or None if not found
        """
        try:
            for user_path in self._base_path.iterdir():
                if not user_path.is_dir():
                    continue
                for collection_path in user_path.iterdir():
                    if not collection_path.is_dir():
                        continue
                    sub_file = collection_path / f"{subscription_id}.json"
                    if sub_file.exists():
                        with open(sub_file, "r") as f:
                            data = json.load(f)
                            return PushSubscription.from_dict(data)
            return None

        except Exception as e:
            logger.error("Failed to get subscription: %s", e)
            return None

    def get_collection_subscriptions(self, collection_path: str) -> List[PushSubscription]:
        """
        Get all subscriptions for a collection.

        Args:
            collection_path: Path to the collection

        Returns:
            List of PushSubscriptions
        """
        subscriptions = []
        try:
            for user_path in self._base_path.iterdir():
                if not user_path.is_dir():
                    continue

                import hashlib
                collection_hash = hashlib.sha256(collection_path.encode()).hexdigest()[:16]
                collection_dir = user_path / collection_hash

                if collection_dir.is_dir():
                    for sub_file in collection_dir.glob("*.json"):
                        try:
                            with open(sub_file, "r") as f:
                                data = json.load(f)
                                subscriptions.append(PushSubscription.from_dict(data))
                        except Exception as e:
                            logger.warning("Failed to load subscription %s: %s", sub_file, e)

        except Exception as e:
            logger.error("Failed to get collection subscriptions: %s", e)

        return subscriptions

    def get_user_subscriptions(self, user: str) -> List[PushSubscription]:
        """
        Get all subscriptions for a user.

        Args:
            user: Username

        Returns:
            List of PushSubscriptions
        """
        subscriptions = []
        try:
            user_path = self._get_user_path(user)
            if not user_path.is_dir():
                return subscriptions

            for collection_path in user_path.iterdir():
                if not collection_path.is_dir():
                    continue
                for sub_file in collection_path.glob("*.json"):
                    try:
                        with open(sub_file, "r") as f:
                            data = json.load(f)
                            subscriptions.append(PushSubscription.from_dict(data))
                    except Exception as e:
                        logger.warning("Failed to load subscription %s: %s", sub_file, e)

        except Exception as e:
            logger.error("Failed to get user subscriptions: %s", e)

        return subscriptions

    def update_subscription(self, subscription: PushSubscription) -> bool:
        """
        Update an existing subscription.

        Args:
            subscription: Updated PushSubscription

        Returns:
            True if successful
        """
        return self.add_subscription(subscription)

    def list_all_subscriptions(self) -> List[PushSubscription]:
        """
        List all subscriptions (for admin purposes).

        Returns:
            List of all PushSubscriptions
        """
        subscriptions = []
        try:
            if not self._base_path.exists():
                return subscriptions

            for user_path in self._base_path.iterdir():
                if not user_path.is_dir():
                    continue
                for collection_path in user_path.iterdir():
                    if not collection_path.is_dir():
                        continue
                    for sub_file in collection_path.glob("*.json"):
                        try:
                            with open(sub_file, "r") as f:
                                data = json.load(f)
                                subscriptions.append(PushSubscription.from_dict(data))
                        except Exception as e:
                            logger.warning("Failed to load subscription %s: %s", sub_file, e)

        except Exception as e:
            logger.error("Failed to list subscriptions: %s", e)

        return subscriptions

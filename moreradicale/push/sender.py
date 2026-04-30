"""
Push notification sender using pywebpush.

Sends Web Push notifications to subscribed clients when calendar data changes.
"""

import json
from http import client
from typing import Dict, List, Optional

from moreradicale.log import logger

from . import DEFAULT_TTL, URGENCY_NORMAL
from .storage import SubscriptionStorage
from .subscription import PushSubscription
from .vapid import VAPIDKeyManager

# Check if pywebpush is available
try:
    from pywebpush import webpush, WebPushException
    HAS_PYWEBPUSH = True
except ImportError:
    HAS_PYWEBPUSH = False
    webpush = None
    WebPushException = Exception


class PushSender:
    """
    Sends Web Push notifications to subscribed clients.

    Uses pywebpush library with VAPID authentication.
    """

    def __init__(self, configuration):
        """
        Initialize push sender.

        Args:
            configuration: Radicale configuration instance
        """
        self._configuration = configuration
        self._storage = SubscriptionStorage(configuration)
        self._vapid = VAPIDKeyManager(configuration)

        self._ttl = configuration.get("push", "ttl")
        self._urgency = configuration.get("push", "urgency")

        # Validate urgency
        from . import URGENCY_LEVELS
        if self._urgency not in URGENCY_LEVELS:
            logger.warning("Invalid urgency %s, using 'normal'", self._urgency)
            self._urgency = URGENCY_NORMAL

        self._initialized = False

    def initialize(self) -> bool:
        """
        Initialize sender (load VAPID keys).

        Returns:
            True if initialization successful
        """
        if self._initialized:
            return True

        if not HAS_PYWEBPUSH:
            logger.error("pywebpush not available - install with: pip install pywebpush")
            return False

        if not self._vapid.load_or_generate_keys():
            return False

        self._initialized = True
        return True

    def notify_collection_change(
        self,
        collection_path: str,
        change_type: str,
        item_href: Optional[str] = None,
        user: Optional[str] = None
    ) -> int:
        """
        Notify subscribers of a collection change.

        Args:
            collection_path: Path to the changed collection
            change_type: Type of change (create, update, delete)
            item_href: Optional href of the changed item
            user: User who made the change (excluded from notification)

        Returns:
            Number of notifications sent successfully
        """
        if not self.initialize():
            return 0

        subscriptions = self._storage.get_collection_subscriptions(collection_path)
        if not subscriptions:
            logger.debug("No subscriptions for %s", collection_path)
            return 0

        # Build notification payload
        payload = self._build_payload(collection_path, change_type, item_href)

        sent = 0
        failed_subs = []

        for sub in subscriptions:
            # Don't notify the user who made the change
            if user and sub.user == user:
                continue

            success = self._send_notification(sub, payload)
            if success:
                sent += 1
                sub.mark_used()
                self._storage.update_subscription(sub)
            else:
                failed_subs.append(sub.id)

        if failed_subs:
            logger.warning("Failed to send %d notifications for %s",
                          len(failed_subs), collection_path)

        logger.info("Sent %d push notifications for %s change", sent, change_type)
        return sent

    def _build_payload(
        self,
        collection_path: str,
        change_type: str,
        item_href: Optional[str] = None
    ) -> str:
        """
        Build notification payload.

        Returns JSON string with change information.
        """
        payload = {
            "type": "collection-changed",
            "collection": collection_path,
            "change": change_type,
            "timestamp": self._get_timestamp()
        }

        if item_href:
            payload["href"] = item_href

        return json.dumps(payload)

    def _get_timestamp(self) -> str:
        """Get current UTC timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _send_notification(self, subscription: PushSubscription, payload: str) -> bool:
        """
        Send a single push notification.

        Args:
            subscription: Target subscription
            payload: JSON payload string

        Returns:
            True if sent successfully
        """
        try:
            response = webpush(
                subscription_info=subscription.to_webpush_info(),
                data=payload,
                vapid_private_key=self._vapid.get_private_key_for_webpush(),
                vapid_claims=self._vapid.get_vapid_claims(),
                ttl=self._ttl,
                headers={
                    "Urgency": self._urgency
                }
            )

            logger.debug("Push notification sent to %s: %s",
                        subscription.endpoint[:50], response.status_code)
            return True

        except WebPushException as e:
            # Handle specific error codes
            if hasattr(e, 'response') and e.response is not None:
                status = e.response.status_code

                if status == 410:  # Gone - subscription expired
                    logger.info("Subscription %s expired, removing", subscription.id)
                    self._storage.remove_subscription(subscription.id)
                elif status == 404:  # Not found
                    logger.info("Subscription %s not found, removing", subscription.id)
                    self._storage.remove_subscription(subscription.id)
                elif status == 429:  # Too many requests
                    logger.warning("Rate limited for subscription %s", subscription.id)
                else:
                    logger.warning("Push failed for %s: %s (HTTP %d)",
                                  subscription.id, e, status)
            else:
                logger.warning("Push failed for %s: %s", subscription.id, e)

            return False

        except Exception as e:
            logger.error("Unexpected error sending push to %s: %s",
                        subscription.id, e)
            return False

    def send_test_notification(self, subscription_id: str) -> bool:
        """
        Send a test notification to verify subscription.

        Args:
            subscription_id: ID of subscription to test

        Returns:
            True if notification sent successfully
        """
        if not self.initialize():
            return False

        subscription = self._storage.get_subscription(subscription_id)
        if not subscription:
            logger.warning("Subscription %s not found", subscription_id)
            return False

        payload = json.dumps({
            "type": "test",
            "message": "Test notification from Radicale",
            "timestamp": self._get_timestamp()
        })

        return self._send_notification(subscription, payload)


class PushNotifier:
    """
    High-level interface for sending push notifications.

    Integrates with Radicale's hook system to automatically send
    notifications when collections change.
    """

    def __init__(self, configuration):
        """
        Initialize push notifier.

        Args:
            configuration: Radicale configuration instance
        """
        self._configuration = configuration
        self._sender = PushSender(configuration)
        self._enabled = configuration.get("push", "enabled")

    def notify(self, collection_path: str, change_type: str,
               item_href: Optional[str] = None, user: Optional[str] = None) -> None:
        """
        Send push notifications for a change.

        Args:
            collection_path: Path to changed collection
            change_type: Type of change
            item_href: Optional changed item href
            user: User who made the change
        """
        if not self._enabled:
            return

        self._sender.notify_collection_change(
            collection_path,
            change_type,
            item_href,
            user
        )

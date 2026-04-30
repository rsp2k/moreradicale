"""
HTTP handler for push subscription management.

Handles subscribe/unsubscribe requests for Web Push notifications.
"""

import json
from http import client
from typing import Dict, Optional, Tuple

from moreradicale.log import logger

from .storage import SubscriptionStorage
from .subscription import (
    PushSubscription,
    generate_subscription_id,
    parse_subscription_request
)
from .vapid import VAPIDKeyManager


class PushHandler:
    """
    Handles HTTP requests for push subscription management.

    Endpoints:
        POST /push/subscribe - Subscribe to push notifications
        DELETE /push/subscribe/{id} - Unsubscribe
        GET /push/vapid-public-key - Get VAPID public key for browser
    """

    def __init__(self, configuration):
        """
        Initialize push handler.

        Args:
            configuration: Radicale configuration instance
        """
        self._configuration = configuration
        self._storage = SubscriptionStorage(configuration)
        self._vapid = VAPIDKeyManager(configuration)
        self._vapid.load_or_generate_keys()

    def handle_subscribe(
        self,
        environ: Dict,
        user: str,
        collection_path: str
    ) -> Tuple[int, Dict[str, str], str, None]:
        """
        Handle push subscription request.

        Expects JSON body with browser PushSubscription.

        Args:
            environ: WSGI environment
            user: Authenticated user
            collection_path: Path to subscribe to

        Returns:
            Tuple of (status, headers, body, xml_request)
        """
        try:
            # Read request body
            content_length = int(environ.get("CONTENT_LENGTH", 0))
            if content_length == 0:
                return self._error_response(
                    client.BAD_REQUEST,
                    "Missing request body"
                )

            body = environ["wsgi.input"].read(content_length).decode("utf-8")
            sub_data = parse_subscription_request(body)

            if not sub_data:
                return self._error_response(
                    client.BAD_REQUEST,
                    "Invalid subscription data"
                )

            # Create subscription
            subscription = PushSubscription(
                id=generate_subscription_id(),
                user=user,
                collection_path=collection_path,
                endpoint=sub_data["endpoint"],
                p256dh_key=sub_data["p256dh_key"],
                auth_key=sub_data["auth_key"],
                user_agent=environ.get("HTTP_USER_AGENT")
            )

            # Store subscription
            if not self._storage.add_subscription(subscription):
                return self._error_response(
                    client.INTERNAL_SERVER_ERROR,
                    "Failed to store subscription"
                )

            # Return subscription info
            response = {
                "id": subscription.id,
                "collection": collection_path,
                "created_at": subscription.created_at
            }

            return (
                client.CREATED,
                {"Content-Type": "application/json"},
                json.dumps(response, indent=2),
                None
            )

        except Exception as e:
            logger.error("Error handling subscribe: %s", e)
            return self._error_response(
                client.INTERNAL_SERVER_ERROR,
                str(e)
            )

    def handle_unsubscribe(
        self,
        subscription_id: str,
        user: str
    ) -> Tuple[int, Dict[str, str], Optional[str], None]:
        """
        Handle push unsubscription request.

        Args:
            subscription_id: ID of subscription to remove
            user: Authenticated user

        Returns:
            Tuple of (status, headers, body, xml_request)
        """
        # Get subscription to verify ownership
        subscription = self._storage.get_subscription(subscription_id)

        if not subscription:
            return self._error_response(
                client.NOT_FOUND,
                "Subscription not found"
            )

        # Verify user owns the subscription
        if subscription.user != user:
            return self._error_response(
                client.FORBIDDEN,
                "Not authorized to remove this subscription"
            )

        # Remove subscription
        if self._storage.remove_subscription(subscription_id):
            return (client.NO_CONTENT, {}, None, None)
        else:
            return self._error_response(
                client.INTERNAL_SERVER_ERROR,
                "Failed to remove subscription"
            )

    def handle_get_vapid_key(self) -> Tuple[int, Dict[str, str], str, None]:
        """
        Return VAPID public key for browser subscription.

        Returns:
            Tuple of (status, headers, body, xml_request)
        """
        public_key = self._vapid.get_public_key_base64()

        if not public_key:
            return self._error_response(
                client.INTERNAL_SERVER_ERROR,
                "VAPID keys not available"
            )

        response = {
            "publicKey": public_key
        }

        return (
            client.OK,
            {
                "Content-Type": "application/json",
                "Cache-Control": "public, max-age=86400"  # Cache for 24h
            },
            json.dumps(response),
            None
        )

    def handle_list_subscriptions(
        self,
        user: str
    ) -> Tuple[int, Dict[str, str], str, None]:
        """
        List user's push subscriptions.

        Args:
            user: Authenticated user

        Returns:
            Tuple of (status, headers, body, xml_request)
        """
        subscriptions = self._storage.get_user_subscriptions(user)

        response = {
            "subscriptions": [
                {
                    "id": sub.id,
                    "collection": sub.collection_path,
                    "created_at": sub.created_at,
                    "last_used": sub.last_used
                }
                for sub in subscriptions
            ]
        }

        return (
            client.OK,
            {"Content-Type": "application/json"},
            json.dumps(response, indent=2),
            None
        )

    def _error_response(
        self,
        status: int,
        message: str
    ) -> Tuple[int, Dict[str, str], str, None]:
        """Generate error response."""
        return (
            status,
            {"Content-Type": "application/json"},
            json.dumps({"error": message}),
            None
        )


def should_handle_push_request(path: str) -> bool:
    """
    Check if path is a push subscription request.

    Args:
        path: Request path

    Returns:
        True if this is a push endpoint
    """
    return path.startswith("/.push/") or path == "/.push"


def parse_push_path(path: str) -> Tuple[str, Optional[str]]:
    """
    Parse push endpoint path.

    Args:
        path: Request path like /.push/subscribe or /.push/subscription/{id}

    Returns:
        Tuple of (action, subscription_id)
    """
    parts = path.strip("/").split("/")

    if len(parts) < 2:
        return ("vapid-key", None)

    action = parts[1]

    if action == "subscription" and len(parts) > 2:
        return ("subscription", parts[2])

    return (action, None)

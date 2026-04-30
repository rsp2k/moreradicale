"""
Push subscription data model and management.

Represents browser push subscriptions as per RFC 8030.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from moreradicale.log import logger


@dataclass
class PushSubscription:
    """
    Represents a Web Push subscription.

    Attributes:
        id: Unique subscription identifier
        user: User who owns the subscription
        collection_path: Path to the collection being watched
        endpoint: Push service endpoint URL
        p256dh_key: Client public key for encryption (Base64)
        auth_key: Client authentication secret (Base64)
        created_at: When the subscription was created
        last_used: When last notification was sent
        user_agent: Optional client user agent string
    """
    id: str
    user: str
    collection_path: str
    endpoint: str
    p256dh_key: str
    auth_key: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_used: Optional[str] = None
    user_agent: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "user": self.user,
            "collection_path": self.collection_path,
            "endpoint": self.endpoint,
            "p256dh_key": self.p256dh_key,
            "auth_key": self.auth_key,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "user_agent": self.user_agent
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PushSubscription":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            user=data["user"],
            collection_path=data["collection_path"],
            endpoint=data["endpoint"],
            p256dh_key=data["p256dh_key"],
            auth_key=data["auth_key"],
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            last_used=data.get("last_used"),
            user_agent=data.get("user_agent")
        )

    def to_webpush_info(self) -> Dict:
        """
        Convert to format expected by pywebpush.

        Returns:
            Dict with endpoint and keys for webpush()
        """
        return {
            "endpoint": self.endpoint,
            "keys": {
                "p256dh": self.p256dh_key,
                "auth": self.auth_key
            }
        }

    def mark_used(self) -> None:
        """Update last_used timestamp."""
        self.last_used = datetime.now(timezone.utc).isoformat()


def generate_subscription_id() -> str:
    """Generate a unique subscription ID."""
    return str(uuid.uuid4())


def generate_pushkey(collection_path: str, user: str, secret: str = "") -> str:
    """
    Generate a pushkey for a collection.

    The pushkey is a unique identifier that clients use to subscribe to
    change notifications for a specific collection.

    Args:
        collection_path: Path to the collection
        user: User who owns the collection
        secret: Optional server secret for additional security

    Returns:
        Base64-encoded pushkey string
    """
    import base64

    # Create a deterministic but unique key
    data = f"{collection_path}:{user}:{secret}".encode()
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest[:16]).decode().rstrip("=")


def get_collection_pushkey(collection, user: str, base_prefix: str) -> Optional[str]:
    """
    Get the pushkey for a collection.

    Args:
        collection: Radicale collection object
        user: Authenticated user
        base_prefix: URL base prefix

    Returns:
        Pushkey string or None if not applicable
    """
    if not collection or not hasattr(collection, "path"):
        return None

    return generate_pushkey(collection.path, user)


def parse_subscription_request(data: str) -> Optional[Dict]:
    """
    Parse a push subscription request body.

    Expected format (PushSubscription from browser):
    {
        "endpoint": "https://push.example.com/...",
        "keys": {
            "p256dh": "...",
            "auth": "..."
        }
    }

    Args:
        data: JSON string from request body

    Returns:
        Parsed subscription data or None if invalid
    """
    try:
        parsed = json.loads(data)

        # Validate required fields
        if "endpoint" not in parsed:
            logger.warning("Push subscription missing endpoint")
            return None

        if "keys" not in parsed:
            logger.warning("Push subscription missing keys")
            return None

        keys = parsed["keys"]
        if "p256dh" not in keys or "auth" not in keys:
            logger.warning("Push subscription missing p256dh or auth key")
            return None

        return {
            "endpoint": parsed["endpoint"],
            "p256dh_key": keys["p256dh"],
            "auth_key": keys["auth"]
        }

    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON in push subscription: %s", e)
        return None

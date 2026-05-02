"""
WebSocket Sync Connection Manager.

Manages WebSocket connections and broadcasts change notifications
to subscribed clients.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from moreradicale.log import logger


class NotificationType(Enum):
    """Types of change notifications."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SYNC = "sync"          # Collection sync token changed
    COLLECTION = "collection"  # Collection-level change


@dataclass
class ChangeNotification:
    """
    Represents a change notification to send to clients.

    Attributes:
        type: Type of change (create, update, delete, sync)
        path: Path of the affected item/collection
        sync_token: Current sync token (if applicable)
        etag: Item ETag (if applicable)
        timestamp: When the change occurred
        user: User who made the change (for filtering)
    """
    type: NotificationType
    path: str
    sync_token: Optional[str] = None
    etag: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    user: Optional[str] = None

    def to_json(self) -> str:
        """Convert to JSON string for transmission."""
        data = {
            "type": self.type.value,
            "path": self.path,
            "timestamp": self.timestamp,
        }
        if self.sync_token:
            data["sync_token"] = self.sync_token
        if self.etag:
            data["etag"] = self.etag
        return json.dumps(data)


@dataclass
class WebSyncConnection:
    """
    Represents a client WebSocket connection.

    Attributes:
        connection_id: Unique identifier for this connection
        user: Authenticated user
        subscriptions: Set of collection paths this connection subscribes to
        send_callback: Function to send messages to the client
        created_at: Connection creation timestamp
        last_activity: Last activity timestamp
    """
    connection_id: str
    user: str
    subscriptions: Set[str] = field(default_factory=set)
    send_callback: Optional[Callable[[str], None]] = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def is_subscribed(self, path: str) -> bool:
        """Check if connection is subscribed to a path."""
        # Check exact match
        if path in self.subscriptions:
            return True

        # Check if path is within a subscribed collection
        for sub_path in self.subscriptions:
            if path.startswith(sub_path):
                return True

        return False

    def send(self, message: str) -> bool:
        """Send message to client."""
        if self.send_callback:
            try:
                self.send_callback(message)
                self.last_activity = time.time()
                return True
            except Exception as e:
                logger.debug("Failed to send to connection %s: %s",
                             self.connection_id, e)
        return False

    def subscribe(self, path: str):
        """Subscribe to a collection path."""
        # Normalize path
        if not path.endswith("/"):
            path += "/"
        self.subscriptions.add(path)
        self.last_activity = time.time()

    def unsubscribe(self, path: str):
        """Unsubscribe from a collection path."""
        if not path.endswith("/"):
            path += "/"
        self.subscriptions.discard(path)
        self.last_activity = time.time()


class WebSyncManager:
    """
    Manages WebSocket sync connections and notifications.

    Thread-safe singleton for broadcasting changes to connected clients.
    """

    _instance: Optional['WebSyncManager'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._connections: Dict[str, WebSyncConnection] = {}
        self._connections_lock = threading.Lock()

        # Path -> set of connection IDs subscribed to it
        self._subscriptions: Dict[str, Set[str]] = {}

        # Notification queue for async processing
        self._notification_queue: List[ChangeNotification] = []
        self._queue_lock = threading.Lock()

        # Stats
        self._total_notifications = 0
        self._total_connections = 0

        logger.debug("WebSyncManager initialized")

    def register_connection(
        self,
        connection_id: str,
        user: str,
        send_callback: Callable[[str], None]
    ) -> WebSyncConnection:
        """
        Register a new WebSocket connection.

        Args:
            connection_id: Unique connection identifier
            user: Authenticated user
            send_callback: Function to send messages to client

        Returns:
            WebSyncConnection object
        """
        conn = WebSyncConnection(
            connection_id=connection_id,
            user=user,
            send_callback=send_callback
        )

        with self._connections_lock:
            self._connections[connection_id] = conn
            self._total_connections += 1

        logger.info("WebSync: Registered connection %s for user %s",
                    connection_id, user)
        return conn

    def unregister_connection(self, connection_id: str):
        """Remove a WebSocket connection."""
        with self._connections_lock:
            conn = self._connections.pop(connection_id, None)
            if conn:
                # Remove from all subscription lists
                for path in conn.subscriptions:
                    if path in self._subscriptions:
                        self._subscriptions[path].discard(connection_id)

        if conn:
            logger.info("WebSync: Unregistered connection %s", connection_id)

    def subscribe(self, connection_id: str, path: str) -> bool:
        """
        Subscribe a connection to a collection path.

        Args:
            connection_id: Connection to subscribe
            path: Collection path to subscribe to

        Returns:
            True if subscription successful
        """
        with self._connections_lock:
            conn = self._connections.get(connection_id)
            if not conn:
                return False

            # Normalize path
            if not path.endswith("/"):
                path += "/"

            # Add subscription
            conn.subscribe(path)

            # Track in subscription index
            if path not in self._subscriptions:
                self._subscriptions[path] = set()
            self._subscriptions[path].add(connection_id)

        logger.debug("WebSync: Connection %s subscribed to %s",
                     connection_id, path)
        return True

    def unsubscribe(self, connection_id: str, path: str) -> bool:
        """Unsubscribe a connection from a collection path."""
        with self._connections_lock:
            conn = self._connections.get(connection_id)
            if not conn:
                return False

            if not path.endswith("/"):
                path += "/"

            conn.unsubscribe(path)

            if path in self._subscriptions:
                self._subscriptions[path].discard(connection_id)

        logger.debug("WebSync: Connection %s unsubscribed from %s",
                     connection_id, path)
        return True

    def notify(
        self,
        notification_type: NotificationType,
        path: str,
        sync_token: Optional[str] = None,
        etag: Optional[str] = None,
        user: Optional[str] = None
    ):
        """
        Broadcast a change notification to subscribed clients.

        Args:
            notification_type: Type of change
            path: Affected path
            sync_token: Current sync token
            etag: Item ETag
            user: User who made the change
        """
        notification = ChangeNotification(
            type=notification_type,
            path=path,
            sync_token=sync_token,
            etag=etag,
            user=user
        )

        # Find collection path for this item
        if not path.endswith("/"):
            collection_path = "/".join(path.split("/")[:-1]) + "/"
        else:
            collection_path = path

        message = notification.to_json()

        with self._connections_lock:
            # Get all connections subscribed to this path
            notified = 0

            for conn in self._connections.values():
                if conn.is_subscribed(collection_path):
                    # Notify all subscribed connections, including those
                    # belonging to the user who made the change - they
                    # may have multiple tabs/clients open and other
                    # tabs need the same update.
                    if conn.send(message):
                        notified += 1

            self._total_notifications += 1

        if notified > 0:
            logger.debug("WebSync: Notified %d clients of %s on %s",
                         notified, notification_type.value, path)

    def get_connection_count(self) -> int:
        """Get current number of active connections."""
        with self._connections_lock:
            return len(self._connections)

    def get_subscription_count(self, path: str = None) -> int:
        """Get number of subscriptions, optionally for a specific path."""
        with self._connections_lock:
            if path:
                if not path.endswith("/"):
                    path += "/"
                return len(self._subscriptions.get(path, set()))
            else:
                return sum(len(s) for s in self._subscriptions.values())

    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        with self._connections_lock:
            # Calculate subscription count directly to avoid lock reentry
            subscription_count = sum(len(s) for s in self._subscriptions.values())
            return {
                "active_connections": len(self._connections),
                "total_connections": self._total_connections,
                "total_notifications": self._total_notifications,
                "subscription_count": subscription_count,
                "paths_subscribed": list(self._subscriptions.keys()),
            }

    def cleanup_stale_connections(self, max_age: float = 3600):
        """
        Remove connections that haven't had activity.

        Args:
            max_age: Maximum seconds since last activity
        """
        now = time.time()
        stale = []

        with self._connections_lock:
            for conn_id, conn in self._connections.items():
                if now - conn.last_activity > max_age:
                    stale.append(conn_id)

        for conn_id in stale:
            self.unregister_connection(conn_id)

        if stale:
            logger.info("WebSync: Cleaned up %d stale connections", len(stale))

    def reset(self):
        """Reset manager state (for testing)."""
        with self._connections_lock:
            self._connections.clear()
            self._subscriptions.clear()
            self._total_notifications = 0
            self._total_connections = 0


# Global instance
websync_manager = WebSyncManager()

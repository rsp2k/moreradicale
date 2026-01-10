"""
WebSocket Real-time Sync for Radicale.

Provides push-based change notifications to clients, eliminating
the need for constant polling. Clients can subscribe to specific
collections and receive updates when items change.

Protocol:
    Client -> Server: {"action": "subscribe", "path": "/user/calendar/"}
    Client -> Server: {"action": "unsubscribe", "path": "/user/calendar/"}
    Server -> Client: {"type": "change", "path": "/user/calendar/", "sync_token": "..."}
    Server -> Client: {"type": "delete", "path": "/user/calendar/event.ics"}

Features:
- Per-collection subscriptions
- Authentication via initial handshake
- Automatic sync-token updates
- Graceful connection handling
"""

from radicale.websync.manager import (
    WebSyncManager,
    WebSyncConnection,
    ChangeNotification,
    NotificationType,
)
from radicale.websync.handler import WebSyncHandler

__all__ = [
    "WebSyncManager",
    "WebSyncConnection",
    "WebSyncHandler",
    "ChangeNotification",
    "NotificationType",
]

"""
External ICS Subscriptions.

Enables Radicale to subscribe to external calendar feeds and
automatically sync/import events from those URLs.

Key components:
- SubscriptionManager: Coordinates sync operations
- SyncEngine: Fetches and processes external ICS data
- RefreshScheduler: Background refresh with configurable intervals

Supports CalendarServer subscribed calendars (CS:source property).
"""

from radicale.subscriptions.engine import (
    SyncEngine,
    SyncResult,
    SyncStatus,
)
from radicale.subscriptions.manager import SubscriptionManager

__all__ = [
    "SyncEngine",
    "SyncResult",
    "SyncStatus",
    "SubscriptionManager",
]

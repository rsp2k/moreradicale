"""
Subscription Manager.

Coordinates sync operations for subscribed calendars, managing
the lifecycle of fetching, updating, and refreshing external feeds.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from radicale.log import logger
from radicale.subscriptions.engine import SyncEngine, SyncResult, SyncStatus


@dataclass
class SubscriptionState:
    """
    Persistent state for a subscription.

    Tracks sync metadata for smart caching and scheduling.
    """
    source_url: str
    collection_path: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    last_sync: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    items_count: int = 0

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            "source_url": self.source_url,
            "collection_path": self.collection_path,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "content_hash": self.content_hash,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "items_count": self.items_count,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SubscriptionState":
        """Deserialize from dictionary."""
        return cls(
            source_url=data.get("source_url", ""),
            collection_path=data.get("collection_path", ""),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            content_hash=data.get("content_hash"),
            last_sync=datetime.fromisoformat(data["last_sync"]) if data.get("last_sync") else None,
            last_success=datetime.fromisoformat(data["last_success"]) if data.get("last_success") else None,
            last_error=data.get("last_error"),
            consecutive_failures=data.get("consecutive_failures", 0),
            items_count=data.get("items_count", 0),
        )


class SubscriptionManager:
    """
    Manages external ICS calendar subscriptions.

    Features:
    - Syncs subscribed calendars from external URLs
    - Persists sync state for smart caching
    - Background refresh with configurable intervals
    - Exponential backoff on failures
    """

    # State file name within collection
    STATE_FILE = ".subscription-state.json"

    def __init__(self, storage, configuration):
        """
        Initialize the subscription manager.

        Args:
            storage: Radicale storage instance
            configuration: Radicale configuration
        """
        self._storage = storage
        self._configuration = configuration
        self._engine = SyncEngine(configuration)
        self._lock = threading.Lock()
        self._refresh_thread = None
        self._stop_event = threading.Event()

    def sync_collection(self, collection_path: str) -> SyncResult:
        """
        Sync a subscribed calendar collection.

        Fetches the external ICS feed and updates the local collection.

        Args:
            collection_path: Path to the subscribed collection

        Returns:
            SyncResult with sync status and statistics
        """
        with self._lock:
            return self._sync_collection_internal(collection_path)

    def _sync_collection_internal(self, collection_path: str) -> SyncResult:
        """Internal sync implementation (must hold lock)."""
        try:
            # Get the collection
            items = list(self._storage.discover(collection_path, depth="0"))
            if not items:
                return SyncResult(
                    status=SyncStatus.ERROR,
                    message=f"Collection not found: {collection_path}"
                )

            collection = items[0]

            # Check if it's a subscribed calendar
            tag = collection.get_meta("tag")
            if tag != "VSUBSCRIBED":
                return SyncResult(
                    status=SyncStatus.ERROR,
                    message="Collection is not a subscribed calendar"
                )

            # Get source URL
            source_url = collection.get_meta("CS:source")
            if not source_url:
                return SyncResult(
                    status=SyncStatus.ERROR,
                    message="No source URL configured"
                )

            logger.info("Syncing subscription %s from %s",
                       collection_path, source_url)

            # Load sync state
            state = self._load_state(collection_path)
            if state is None:
                state = SubscriptionState(
                    source_url=source_url,
                    collection_path=collection_path
                )

            # Fetch external feed
            result, ics_data = self._engine.fetch(
                source_url,
                etag=state.etag,
                last_modified=state.last_modified
            )

            # Update state
            state.last_sync = datetime.now(timezone.utc)

            if result.status == SyncStatus.NOT_MODIFIED:
                # Content unchanged
                state.consecutive_failures = 0
                self._save_state(collection_path, state)
                logger.debug("Subscription %s not modified", collection_path)
                return result

            if result.status != SyncStatus.SUCCESS:
                # Sync failed
                state.last_error = result.message
                state.consecutive_failures += 1
                self._save_state(collection_path, state)
                logger.warning("Subscription sync failed for %s: %s",
                              collection_path, result.message)
                return result

            # Check if content actually changed
            if result.content_hash == state.content_hash:
                logger.debug("Subscription %s content hash unchanged",
                            collection_path)
                state.consecutive_failures = 0
                self._save_state(collection_path, state)
                return SyncResult(
                    status=SyncStatus.NOT_MODIFIED,
                    message="Content unchanged (hash match)"
                )

            # Parse and import events
            events = self._engine.parse_events(ics_data)
            import_result = self._import_events(collection, events)

            # Update state with success
            state.etag = result.etag
            state.last_modified = result.last_modified
            state.content_hash = result.content_hash
            state.last_success = datetime.now(timezone.utc)
            state.last_error = None
            state.consecutive_failures = 0
            state.items_count = len(events)
            self._save_state(collection_path, state)

            logger.info("Synced %s: +%d ~%d -%d items",
                       collection_path,
                       import_result.items_added,
                       import_result.items_updated,
                       import_result.items_deleted)

            return import_result

        except Exception as e:
            logger.error("Error syncing %s: %s", collection_path, e,
                        exc_info=True)
            return SyncResult(
                status=SyncStatus.ERROR,
                message=f"Internal error: {e}"
            )

    def _import_events(self, collection, events: List[Dict]) -> SyncResult:
        """
        Import events into collection.

        Performs a full sync: adds new items, updates existing,
        and removes items no longer in the feed.

        Args:
            collection: Target collection
            events: List of parsed event dicts

        Returns:
            SyncResult with import statistics
        """
        from radicale import item as radicale_item

        added = 0
        updated = 0
        deleted = 0

        # Get existing items
        existing_items = {}
        for item in collection.get_all():
            if hasattr(item, "uid"):
                existing_items[item.uid] = item.href

        # Track UIDs from feed
        feed_uids = set()

        # Import each event
        for event in events:
            uid = event["uid"]
            feed_uids.add(uid)
            ics_data = event["data"]

            # Create item
            try:
                prepared_item = radicale_item.Item(
                    collection_path=collection.path,
                    text=ics_data,
                )
                prepared_item.prepare()
            except Exception as e:
                logger.warning("Failed to prepare item %s: %s", uid, e)
                continue

            # Check if exists
            if uid in existing_items:
                # Update existing
                href = existing_items[uid]
                try:
                    collection.upload(href, prepared_item)
                    updated += 1
                except Exception as e:
                    logger.warning("Failed to update %s: %s", href, e)
            else:
                # Add new
                href = f"{uid}.ics"
                # Sanitize href
                href = href.replace("/", "_").replace("\\", "_")
                try:
                    collection.upload(href, prepared_item)
                    added += 1
                except Exception as e:
                    logger.warning("Failed to add %s: %s", href, e)

        # Delete items no longer in feed
        for uid, href in existing_items.items():
            if uid not in feed_uids:
                try:
                    collection.delete(href)
                    deleted += 1
                except Exception as e:
                    logger.warning("Failed to delete %s: %s", href, e)

        return SyncResult(
            status=SyncStatus.SUCCESS,
            items_added=added,
            items_updated=updated,
            items_deleted=deleted
        )

    def sync_all(self) -> Dict[str, SyncResult]:
        """
        Sync all subscribed calendars.

        Returns:
            Dict mapping collection paths to their SyncResults
        """
        results = {}

        subscribed = self._find_subscribed_collections()
        for collection_path in subscribed:
            results[collection_path] = self.sync_collection(collection_path)

        return results

    def _find_subscribed_collections(self) -> List[str]:
        """Find all VSUBSCRIBED collections in storage."""
        subscribed = []

        try:
            # Discover all collections
            for item in self._storage.discover("", depth="infinity"):
                if hasattr(item, "get_meta"):
                    tag = item.get_meta("tag")
                    if tag == "VSUBSCRIBED":
                        subscribed.append(item.path)
        except Exception as e:
            logger.warning("Error finding subscribed collections: %s", e)

        return subscribed

    def get_sync_state(self, collection_path: str) -> Optional[SubscriptionState]:
        """
        Get the sync state for a collection.

        Args:
            collection_path: Path to collection

        Returns:
            SubscriptionState or None
        """
        return self._load_state(collection_path)

    def _load_state(self, collection_path: str) -> Optional[SubscriptionState]:
        """Load sync state from storage."""
        try:
            # Get collection's cache directory
            items = list(self._storage.discover(collection_path, depth="0"))
            if not items:
                return None

            collection = items[0]
            cache_path = Path(collection._filesystem_path) / ".Radicale.cache"
            state_file = cache_path / self.STATE_FILE

            if state_file.exists():
                with open(state_file, "r") as f:
                    data = json.load(f)
                    return SubscriptionState.from_dict(data)

        except Exception as e:
            logger.debug("Error loading subscription state: %s", e)

        return None

    def _save_state(self, collection_path: str, state: SubscriptionState):
        """Save sync state to storage."""
        try:
            # Get collection's cache directory
            items = list(self._storage.discover(collection_path, depth="0"))
            if not items:
                return

            collection = items[0]
            cache_path = Path(collection._filesystem_path) / ".Radicale.cache"
            cache_path.mkdir(parents=True, exist_ok=True)

            state_file = cache_path / self.STATE_FILE
            with open(state_file, "w") as f:
                json.dump(state.to_dict(), f, indent=2)

        except Exception as e:
            logger.warning("Error saving subscription state: %s", e)

    def start_background_refresh(self):
        """Start background refresh thread."""
        if not self._configuration.get("subscriptions", "auto_refresh"):
            logger.debug("Subscription auto-refresh disabled")
            return

        if self._refresh_thread and self._refresh_thread.is_alive():
            logger.warning("Background refresh already running")
            return

        self._stop_event.clear()
        self._refresh_thread = threading.Thread(
            target=self._background_refresh_loop,
            name="SubscriptionRefresh",
            daemon=True
        )
        self._refresh_thread.start()
        logger.info("Started subscription background refresh")

    def stop_background_refresh(self):
        """Stop background refresh thread."""
        self._stop_event.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=5)
            self._refresh_thread = None
        logger.info("Stopped subscription background refresh")

    def _background_refresh_loop(self):
        """Background thread for periodic refresh."""
        refresh_interval = self._configuration.get(
            "subscriptions", "refresh_interval"
        )

        logger.debug("Background refresh interval: %d seconds", refresh_interval)

        while not self._stop_event.is_set():
            try:
                # Find collections due for refresh
                subscribed = self._find_subscribed_collections()

                for collection_path in subscribed:
                    if self._stop_event.is_set():
                        break

                    if self._should_refresh(collection_path):
                        try:
                            self.sync_collection(collection_path)
                        except Exception as e:
                            logger.warning("Background sync error for %s: %s",
                                          collection_path, e)

                    # Brief pause between syncs
                    time.sleep(1)

            except Exception as e:
                logger.error("Background refresh loop error: %s", e)

            # Wait for next cycle
            self._stop_event.wait(refresh_interval)

    def _should_refresh(self, collection_path: str) -> bool:
        """Check if collection is due for refresh."""
        state = self._load_state(collection_path)
        if state is None:
            return True  # Never synced

        if state.last_sync is None:
            return True

        refresh_interval = self._configuration.get(
            "subscriptions", "refresh_interval"
        )

        # Exponential backoff on failures
        if state.consecutive_failures > 0:
            backoff = min(
                refresh_interval * (2 ** state.consecutive_failures),
                86400  # Max 24 hours
            )
            next_sync = state.last_sync + timedelta(seconds=backoff)
        else:
            next_sync = state.last_sync + timedelta(seconds=refresh_interval)

        return datetime.now(timezone.utc) >= next_sync

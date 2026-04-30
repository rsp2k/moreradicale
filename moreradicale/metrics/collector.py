"""
Prometheus Metrics Collector.

Thread-safe metrics collection for Radicale operations.
Follows Prometheus exposition format (text/plain).
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Histogram:
    """Simple histogram for latency tracking."""
    buckets: Tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    counts: Dict[float, int] = field(default_factory=lambda: defaultdict(int))
    total_count: int = 0
    total_sum: float = 0.0

    def observe(self, value: float):
        """Record an observation."""
        self.total_count += 1
        self.total_sum += value
        for bucket in self.buckets:
            if value <= bucket:
                self.counts[bucket] += 1


class MetricsCollector:
    """
    Collects and exposes Prometheus metrics.

    Thread-safe singleton for application-wide metrics.
    """

    _instance: Optional['MetricsCollector'] = None
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
        self._metrics_lock = threading.Lock()

        # Counters
        self._request_count: Dict[Tuple[str, str, int], int] = defaultdict(int)
        self._auth_attempts: Dict[Tuple[str, bool], int] = defaultdict(int)
        self._push_notifications: Dict[Tuple[str, bool], int] = defaultdict(int)
        self._subscription_syncs: Dict[Tuple[str, str], int] = defaultdict(int)

        # Histograms
        self._request_latency: Dict[Tuple[str, str], Histogram] = defaultdict(Histogram)

        # Gauges
        self._collections_count: int = 0
        self._items_count: int = 0
        self._storage_bytes: int = 0
        self._active_subscriptions: int = 0
        self._push_subscriptions: int = 0

        # Info
        self._start_time = time.time()
        self._version = ""

    def set_version(self, version: str):
        """Set Radicale version for info metric."""
        self._version = version

    # === Request Metrics ===

    def inc_request(self, method: str, path_type: str, status_code: int):
        """Increment request counter."""
        with self._metrics_lock:
            self._request_count[(method, path_type, status_code)] += 1

    def observe_request_latency(self, method: str, path_type: str, duration: float):
        """Record request latency."""
        with self._metrics_lock:
            key = (method, path_type)
            if key not in self._request_latency:
                self._request_latency[key] = Histogram()
            self._request_latency[key].observe(duration)

    # === Auth Metrics ===

    def inc_auth_attempt(self, auth_type: str, success: bool):
        """Increment authentication attempt counter."""
        with self._metrics_lock:
            self._auth_attempts[(auth_type, success)] += 1

    # === Push Metrics ===

    def inc_push_notification(self, notification_type: str, success: bool):
        """Increment push notification counter."""
        with self._metrics_lock:
            self._push_notifications[(notification_type, success)] += 1

    def set_push_subscriptions(self, count: int):
        """Set current push subscription count."""
        with self._metrics_lock:
            self._push_subscriptions = count

    # === Subscription Metrics ===

    def inc_subscription_sync(self, collection_path: str, status: str):
        """Increment subscription sync counter."""
        with self._metrics_lock:
            self._subscription_syncs[(collection_path, status)] += 1

    def set_active_subscriptions(self, count: int):
        """Set current active subscription count."""
        with self._metrics_lock:
            self._active_subscriptions = count

    # === Storage Metrics ===

    def set_storage_stats(self, collections: int, items: int, bytes_used: int):
        """Set storage statistics."""
        with self._metrics_lock:
            self._collections_count = collections
            self._items_count = items
            self._storage_bytes = bytes_used

    # === Export ===

    def export(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []

        with self._metrics_lock:
            # Info metric
            lines.append("# HELP radicale_info Radicale server information")
            lines.append("# TYPE radicale_info gauge")
            lines.append(f'radicale_info{{version="{self._version}"}} 1')

            # Uptime
            lines.append("# HELP radicale_uptime_seconds Server uptime in seconds")
            lines.append("# TYPE radicale_uptime_seconds gauge")
            lines.append(f"radicale_uptime_seconds {time.time() - self._start_time:.3f}")

            # Request counter
            lines.append("# HELP radicale_requests_total Total HTTP requests")
            lines.append("# TYPE radicale_requests_total counter")
            for (method, path_type, status), count in self._request_count.items():
                lines.append(
                    f'radicale_requests_total{{method="{method}",path_type="{path_type}",'
                    f'status="{status}"}} {count}'
                )

            # Request latency histogram
            lines.append("# HELP radicale_request_duration_seconds Request latency")
            lines.append("# TYPE radicale_request_duration_seconds histogram")
            for (method, path_type), hist in self._request_latency.items():
                base = f'radicale_request_duration_seconds{{method="{method}",path_type="{path_type}"'
                cumulative = 0
                for bucket in hist.buckets:
                    cumulative += hist.counts.get(bucket, 0)
                    lines.append(f'{base},le="{bucket}"}} {cumulative}')
                lines.append(f'{base},le="+Inf"}} {hist.total_count}')
                lines.append(
                    f'radicale_request_duration_seconds_sum{{method="{method}",'
                    f'path_type="{path_type}"}} {hist.total_sum:.6f}'
                )
                lines.append(
                    f'radicale_request_duration_seconds_count{{method="{method}",'
                    f'path_type="{path_type}"}} {hist.total_count}'
                )

            # Auth attempts
            lines.append("# HELP radicale_auth_attempts_total Authentication attempts")
            lines.append("# TYPE radicale_auth_attempts_total counter")
            for (auth_type, success), count in self._auth_attempts.items():
                result = "success" if success else "failure"
                lines.append(
                    f'radicale_auth_attempts_total{{type="{auth_type}",result="{result}"}} {count}'
                )

            # Push notifications
            lines.append("# HELP radicale_push_notifications_total Push notifications sent")
            lines.append("# TYPE radicale_push_notifications_total counter")
            for (notif_type, success), count in self._push_notifications.items():
                result = "success" if success else "failure"
                lines.append(
                    f'radicale_push_notifications_total{{type="{notif_type}",'
                    f'result="{result}"}} {count}'
                )

            # Push subscriptions gauge
            lines.append("# HELP radicale_push_subscriptions Active push subscriptions")
            lines.append("# TYPE radicale_push_subscriptions gauge")
            lines.append(f"radicale_push_subscriptions {self._push_subscriptions}")

            # Subscription syncs
            lines.append("# HELP radicale_subscription_syncs_total External calendar syncs")
            lines.append("# TYPE radicale_subscription_syncs_total counter")
            for (path, status), count in self._subscription_syncs.items():
                # Sanitize path for label
                safe_path = path.replace('"', '\\"')[:50]
                lines.append(
                    f'radicale_subscription_syncs_total{{path="{safe_path}",'
                    f'status="{status}"}} {count}'
                )

            # Active subscriptions gauge
            lines.append("# HELP radicale_active_subscriptions Active calendar subscriptions")
            lines.append("# TYPE radicale_active_subscriptions gauge")
            lines.append(f"radicale_active_subscriptions {self._active_subscriptions}")

            # Storage gauges
            lines.append("# HELP radicale_collections_total Total collections")
            lines.append("# TYPE radicale_collections_total gauge")
            lines.append(f"radicale_collections_total {self._collections_count}")

            lines.append("# HELP radicale_items_total Total items (events, contacts, etc.)")
            lines.append("# TYPE radicale_items_total gauge")
            lines.append(f"radicale_items_total {self._items_count}")

            lines.append("# HELP radicale_storage_bytes Storage size in bytes")
            lines.append("# TYPE radicale_storage_bytes gauge")
            lines.append(f"radicale_storage_bytes {self._storage_bytes}")

        lines.append("")  # Trailing newline
        return "\n".join(lines)

    def reset(self):
        """Reset all metrics (for testing)."""
        with self._metrics_lock:
            self._request_count.clear()
            self._auth_attempts.clear()
            self._push_notifications.clear()
            self._subscription_syncs.clear()
            self._request_latency.clear()
            self._collections_count = 0
            self._items_count = 0
            self._storage_bytes = 0
            self._active_subscriptions = 0
            self._push_subscriptions = 0
            self._start_time = time.time()


# Global instance
metrics = MetricsCollector()

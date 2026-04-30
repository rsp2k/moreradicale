"""
Tests for Prometheus Metrics.

Tests the metrics collector, handler, and endpoint integration.
"""

import pytest

from moreradicale.tests import BaseTest


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    def test_singleton_pattern(self):
        """Test that MetricsCollector is a singleton."""
        from moreradicale.metrics.collector import MetricsCollector

        collector1 = MetricsCollector()
        collector2 = MetricsCollector()

        assert collector1 is collector2

    def test_request_counter(self):
        """Test incrementing request counter."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.reset()

        collector.inc_request("GET", "collection", 200)
        collector.inc_request("GET", "collection", 200)
        collector.inc_request("PUT", "item", 201)

        output = collector.export()

        assert 'radicale_requests_total{method="GET",path_type="collection",status="200"} 2' in output
        assert 'radicale_requests_total{method="PUT",path_type="item",status="201"} 1' in output

    def test_request_latency_histogram(self):
        """Test recording request latency."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.reset()

        collector.observe_request_latency("GET", "collection", 0.05)
        collector.observe_request_latency("GET", "collection", 0.15)

        output = collector.export()

        assert "radicale_request_duration_seconds" in output
        assert 'method="GET"' in output
        assert "_sum" in output
        assert "_count" in output

    def test_auth_attempts(self):
        """Test authentication attempt tracking."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.reset()

        collector.inc_auth_attempt("htpasswd", True)
        collector.inc_auth_attempt("htpasswd", True)
        collector.inc_auth_attempt("htpasswd", False)

        output = collector.export()

        assert 'radicale_auth_attempts_total{type="htpasswd",result="success"} 2' in output
        assert 'radicale_auth_attempts_total{type="htpasswd",result="failure"} 1' in output

    def test_push_notifications(self):
        """Test push notification tracking."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.reset()

        collector.inc_push_notification("web_push", True)
        collector.inc_push_notification("web_push", False)

        output = collector.export()

        assert 'radicale_push_notifications_total{type="web_push",result="success"} 1' in output
        assert 'radicale_push_notifications_total{type="web_push",result="failure"} 1' in output

    def test_storage_gauges(self):
        """Test storage statistic gauges."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.reset()

        collector.set_storage_stats(collections=10, items=150, bytes_used=1048576)

        output = collector.export()

        assert "radicale_collections_total 10" in output
        assert "radicale_items_total 150" in output
        assert "radicale_storage_bytes 1048576" in output

    def test_subscription_metrics(self):
        """Test subscription sync tracking."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.reset()

        collector.inc_subscription_sync("/user/holidays/", "success")
        collector.inc_subscription_sync("/user/holidays/", "success")
        collector.inc_subscription_sync("/user/work/", "error")
        collector.set_active_subscriptions(5)

        output = collector.export()

        assert "radicale_subscription_syncs_total" in output
        assert "radicale_active_subscriptions 5" in output

    def test_uptime_metric(self):
        """Test uptime gauge."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()

        output = collector.export()

        assert "radicale_uptime_seconds" in output

    def test_info_metric(self):
        """Test info metric with version."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.set_version("3.5.0")

        output = collector.export()

        assert 'radicale_info{version="3.5.0"} 1' in output

    def test_export_format(self):
        """Test that export follows Prometheus format."""
        from moreradicale.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        collector.reset()

        output = collector.export()

        # Should have HELP and TYPE comments
        assert "# HELP" in output
        assert "# TYPE" in output
        # Should end with newline
        assert output.endswith("\n")


class TestMetricsHandler:
    """Tests for MetricsHandler."""

    def test_handler_disabled(self):
        """Test handler returns 404 when disabled."""
        from unittest.mock import Mock
        from moreradicale.metrics.handler import MetricsHandler

        config = Mock()
        config.get.side_effect = lambda s, k: {
            ("metrics", "enabled"): False,
            ("metrics", "require_auth"): True,
        }.get((s, k), False)

        handler = MetricsHandler(config)
        status, headers, body = handler.handle_request()

        assert status == 404

    def test_handler_requires_auth(self):
        """Test handler requires authentication when configured."""
        from unittest.mock import Mock
        from moreradicale.metrics.handler import MetricsHandler

        config = Mock()
        config.get.side_effect = lambda s, k: {
            ("metrics", "enabled"): True,
            ("metrics", "require_auth"): True,
        }.get((s, k), True)

        handler = MetricsHandler(config)
        # No environ and no user - should require auth
        status, headers, body = handler.handle_request(environ={}, user="")

        assert status == 401
        assert "WWW-Authenticate" in headers

    def test_handler_returns_metrics(self):
        """Test handler returns metrics when authenticated."""
        from unittest.mock import Mock
        from moreradicale.metrics.handler import MetricsHandler

        config = Mock()
        config.get.side_effect = lambda s, k: {
            ("metrics", "enabled"): True,
            ("metrics", "require_auth"): True,
        }.get((s, k), True)

        handler = MetricsHandler(config)
        # Pre-authenticated user bypasses auth check
        status, headers, body = handler.handle_request(user="admin")

        assert status == 200
        assert "text/plain" in headers["Content-Type"]
        assert "radicale_" in body


class TestMetricsEndpoint(BaseTest):
    """Tests for metrics HTTP endpoint integration."""

    def test_metrics_disabled_returns_404(self):
        """Test that /.metrics returns 404 when disabled."""
        self.configure({
            "metrics": {"enabled": "False"},
            "auth": {"type": "none"}
        })

        status, _, _ = self.request("GET", "/.metrics")

        assert status == 404

    def test_metrics_enabled_returns_200(self):
        """Test that /.metrics returns 200 when enabled."""
        self.configure({
            "metrics": {"enabled": "True", "require_auth": "False"},
            "auth": {"type": "none"}
        })

        status, headers, body = self.request("GET", "/.metrics")

        assert status == 200
        assert "text/plain" in headers.get("Content-Type", "")
        assert "radicale_uptime_seconds" in body

    def test_metrics_requires_auth_when_configured(self):
        """Test that /.metrics requires auth when configured."""
        self.configure({
            "metrics": {"enabled": "True", "require_auth": "True"},
            "auth": {"type": "none"}
        })

        # Without login
        status, _, _ = self.request("GET", "/.metrics")
        assert status == 401

        # With login
        status, _, body = self.request("GET", "/.metrics", login="user:user")
        assert status == 200
        assert "radicale_" in body


class TestHistogram:
    """Tests for Histogram helper class."""

    def test_histogram_observe(self):
        """Test histogram observation."""
        from moreradicale.metrics.collector import Histogram

        hist = Histogram()
        hist.observe(0.05)
        hist.observe(0.15)
        hist.observe(0.5)

        assert hist.total_count == 3
        assert abs(hist.total_sum - 0.7) < 0.001

    def test_histogram_bucket_counting(self):
        """Test histogram bucket counting (cumulative)."""
        from moreradicale.metrics.collector import Histogram

        hist = Histogram(buckets=(0.1, 0.5, 1.0))

        hist.observe(0.05)  # <= 0.1, 0.5, 1.0
        hist.observe(0.3)   # <= 0.5, 1.0
        hist.observe(0.8)   # <= 1.0

        # Each bucket counts observations that fit in it
        # 0.05 fits in all buckets
        assert hist.counts[0.1] == 1  # Only 0.05 fits
        assert hist.counts[0.5] == 2  # 0.05 and 0.3 fit
        assert hist.counts[1.0] == 3  # All three fit

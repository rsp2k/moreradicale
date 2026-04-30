"""
Tests for WebSocket Real-time Sync.

Tests connection management, subscriptions, notifications, and frame handling.
"""

import json
import struct
import time
import pytest


class TestWebSyncManager:
    """Tests for WebSyncManager."""

    def setup_method(self):
        """Reset manager before each test."""
        from moreradicale.websync.manager import websync_manager
        websync_manager.reset()

    def test_singleton_pattern(self):
        """Test that WebSyncManager is a singleton."""
        from moreradicale.websync.manager import WebSyncManager

        manager1 = WebSyncManager()
        manager2 = WebSyncManager()

        assert manager1 is manager2

    def test_register_connection(self):
        """Test registering a connection."""
        from moreradicale.websync.manager import websync_manager

        messages = []

        def send(msg):
            messages.append(msg)

        conn = websync_manager.register_connection(
            "conn-1", "user1", send
        )

        assert conn.connection_id == "conn-1"
        assert conn.user == "user1"
        assert websync_manager.get_connection_count() == 1

    def test_unregister_connection(self):
        """Test unregistering a connection."""
        from moreradicale.websync.manager import websync_manager

        websync_manager.register_connection("conn-1", "user1", lambda m: None)
        assert websync_manager.get_connection_count() == 1

        websync_manager.unregister_connection("conn-1")
        assert websync_manager.get_connection_count() == 0

    def test_subscribe(self):
        """Test subscribing to a path."""
        from moreradicale.websync.manager import websync_manager

        websync_manager.register_connection("conn-1", "user1", lambda m: None)
        result = websync_manager.subscribe("conn-1", "/user1/calendar/")

        assert result is True
        assert websync_manager.get_subscription_count("/user1/calendar/") == 1

    def test_unsubscribe(self):
        """Test unsubscribing from a path."""
        from moreradicale.websync.manager import websync_manager

        websync_manager.register_connection("conn-1", "user1", lambda m: None)
        websync_manager.subscribe("conn-1", "/user1/calendar/")
        assert websync_manager.get_subscription_count("/user1/calendar/") == 1

        websync_manager.unsubscribe("conn-1", "/user1/calendar/")
        assert websync_manager.get_subscription_count("/user1/calendar/") == 0

    def test_notify_subscribed_connections(self):
        """Test notification delivery to subscribed connections."""
        from moreradicale.websync.manager import websync_manager, NotificationType

        messages1 = []
        messages2 = []

        websync_manager.register_connection("conn-1", "user1", messages1.append)
        websync_manager.register_connection("conn-2", "user2", messages2.append)

        websync_manager.subscribe("conn-1", "/user1/calendar/")
        websync_manager.subscribe("conn-2", "/user2/calendar/")

        # Notify on user1's calendar
        websync_manager.notify(
            NotificationType.UPDATE,
            "/user1/calendar/event.ics",
            sync_token="token-1"
        )

        # Only conn-1 should receive notification
        assert len(messages1) == 1
        assert len(messages2) == 0

        # Verify message content
        msg = json.loads(messages1[0])
        assert msg["type"] == "update"
        assert msg["path"] == "/user1/calendar/event.ics"
        assert msg["sync_token"] == "token-1"

    def test_notify_excludes_self(self):
        """Test that user who made change doesn't get notified."""
        from moreradicale.websync.manager import websync_manager, NotificationType

        messages = []
        websync_manager.register_connection("conn-1", "user1", messages.append)
        websync_manager.subscribe("conn-1", "/user1/calendar/")

        # Notify with same user
        websync_manager.notify(
            NotificationType.UPDATE,
            "/user1/calendar/event.ics",
            user="user1"  # Same as connection user
        )

        # Should not receive notification
        assert len(messages) == 0

    def test_connection_is_subscribed(self):
        """Test subscription matching."""
        from moreradicale.websync.manager import WebSyncConnection

        conn = WebSyncConnection("conn-1", "user1")
        conn.subscribe("/user1/calendar/")

        # Exact match
        assert conn.is_subscribed("/user1/calendar/")

        # Item within collection
        assert conn.is_subscribed("/user1/calendar/event.ics")

        # Different collection
        assert not conn.is_subscribed("/user2/calendar/")

    def test_cleanup_stale_connections(self):
        """Test stale connection cleanup."""
        from moreradicale.websync.manager import websync_manager

        conn = websync_manager.register_connection("conn-1", "user1", lambda m: None)

        # Set last activity to past
        conn.last_activity = time.time() - 7200  # 2 hours ago

        websync_manager.cleanup_stale_connections(max_age=3600)

        assert websync_manager.get_connection_count() == 0

    def test_get_stats(self):
        """Test statistics gathering."""
        from moreradicale.websync.manager import websync_manager, NotificationType

        websync_manager.register_connection("conn-1", "user1", lambda m: None)
        websync_manager.subscribe("conn-1", "/user1/calendar/")
        websync_manager.notify(NotificationType.UPDATE, "/user1/calendar/event.ics")

        stats = websync_manager.get_stats()

        assert stats["active_connections"] == 1
        assert stats["total_notifications"] == 1
        assert "/user1/calendar/" in stats["paths_subscribed"]


class TestChangeNotification:
    """Tests for ChangeNotification."""

    def test_notification_to_json(self):
        """Test notification JSON serialization."""
        from moreradicale.websync.manager import ChangeNotification, NotificationType

        notification = ChangeNotification(
            type=NotificationType.CREATE,
            path="/user1/calendar/event.ics",
            sync_token="sync-123",
            etag="etag-456"
        )

        json_str = notification.to_json()
        data = json.loads(json_str)

        assert data["type"] == "create"
        assert data["path"] == "/user1/calendar/event.ics"
        assert data["sync_token"] == "sync-123"
        assert data["etag"] == "etag-456"
        assert "timestamp" in data

    def test_notification_minimal_json(self):
        """Test notification with minimal fields."""
        from moreradicale.websync.manager import ChangeNotification, NotificationType

        notification = ChangeNotification(
            type=NotificationType.DELETE,
            path="/user1/calendar/old.ics"
        )

        json_str = notification.to_json()
        data = json.loads(json_str)

        assert data["type"] == "delete"
        assert data["path"] == "/user1/calendar/old.ics"
        assert "sync_token" not in data
        assert "etag" not in data


class TestWebSyncHandler:
    """Tests for WebSyncHandler."""

    def test_is_websocket_request(self):
        """Test WebSocket request detection."""
        from unittest.mock import Mock
        from moreradicale.websync.handler import WebSyncHandler

        config = Mock()
        config.get.return_value = True

        handler = WebSyncHandler(config)

        # Valid WebSocket request
        environ = {
            "HTTP_UPGRADE": "websocket",
            "HTTP_CONNECTION": "Upgrade",
        }
        assert handler.is_websocket_request(environ)

        # Not a WebSocket request
        environ = {
            "HTTP_UPGRADE": "",
            "HTTP_CONNECTION": "keep-alive",
        }
        assert not handler.is_websocket_request(environ)

    def test_compute_accept_key(self):
        """Test WebSocket accept key computation."""
        from unittest.mock import Mock
        from moreradicale.websync.handler import WebSyncHandler

        config = Mock()
        config.get.return_value = True

        handler = WebSyncHandler(config)

        # Known test vector from RFC 6455
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        expected = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

        result = handler._compute_accept_key(key)
        assert result == expected

    def test_handle_message_subscribe(self):
        """Test handling subscribe message."""
        from unittest.mock import Mock
        from moreradicale.websync.manager import websync_manager
        from moreradicale.websync.handler import WebSyncHandler

        websync_manager.reset()
        websync_manager.register_connection("conn-1", "user1", lambda m: None)

        config = Mock()
        config.get.return_value = True

        handler = WebSyncHandler(config)

        response = handler.handle_message(
            "conn-1",
            '{"action": "subscribe", "path": "/user1/calendar/"}',
            "user1"
        )

        data = json.loads(response)
        assert data["status"] == "subscribed"
        assert data["path"] == "/user1/calendar/"

    def test_handle_message_unsubscribe(self):
        """Test handling unsubscribe message."""
        from unittest.mock import Mock
        from moreradicale.websync.manager import websync_manager
        from moreradicale.websync.handler import WebSyncHandler

        websync_manager.reset()
        websync_manager.register_connection("conn-1", "user1", lambda m: None)
        websync_manager.subscribe("conn-1", "/user1/calendar/")

        config = Mock()
        config.get.return_value = True

        handler = WebSyncHandler(config)

        response = handler.handle_message(
            "conn-1",
            '{"action": "unsubscribe", "path": "/user1/calendar/"}',
            "user1"
        )

        data = json.loads(response)
        assert data["status"] == "unsubscribed"

    def test_handle_message_ping(self):
        """Test handling ping message."""
        from unittest.mock import Mock
        from moreradicale.websync.handler import WebSyncHandler

        config = Mock()
        config.get.return_value = True

        handler = WebSyncHandler(config)

        response = handler.handle_message(
            "conn-1",
            '{"action": "ping", "timestamp": 12345}',
            "user1"
        )

        data = json.loads(response)
        assert data["action"] == "pong"
        assert data["timestamp"] == 12345

    def test_handle_message_invalid_json(self):
        """Test handling invalid JSON."""
        from unittest.mock import Mock
        from moreradicale.websync.handler import WebSyncHandler

        config = Mock()
        config.get.return_value = True

        handler = WebSyncHandler(config)

        response = handler.handle_message(
            "conn-1",
            "not valid json",
            "user1"
        )

        data = json.loads(response)
        assert "error" in data

    def test_can_access_path(self):
        """Test path access checking."""
        from unittest.mock import Mock
        from moreradicale.websync.handler import WebSyncHandler

        config = Mock()
        config.get.return_value = True

        handler = WebSyncHandler(config)

        # User can access own paths
        assert handler._can_access_path("user1", "/user1/calendar/")

        # User cannot access other users' paths
        assert not handler._can_access_path("user1", "/user2/calendar/")

        # Empty user cannot access anything
        assert not handler._can_access_path("", "/user1/calendar/")


class TestWebSocketFrame:
    """Tests for WebSocket frame handling."""

    def test_build_text_frame(self):
        """Test building text frame."""
        from moreradicale.websync.handler import WebSocketFrame

        frame = WebSocketFrame.build_text_frame("Hello")

        # Parse it back
        opcode, fin, payload, length = WebSocketFrame.parse_frame(frame)

        assert opcode == WebSocketFrame.TEXT
        assert fin is True
        assert payload == b"Hello"

    def test_build_close_frame(self):
        """Test building close frame."""
        from moreradicale.websync.handler import WebSocketFrame

        frame = WebSocketFrame.build_close_frame(1000, "Normal closure")

        opcode, fin, payload, length = WebSocketFrame.parse_frame(frame)

        assert opcode == WebSocketFrame.CLOSE
        assert fin is True

        # Close frame has 2-byte code + reason
        code = struct.unpack(">H", payload[:2])[0]
        reason = payload[2:].decode("utf-8")

        assert code == 1000
        assert reason == "Normal closure"

    def test_build_ping_frame(self):
        """Test building ping frame."""
        from moreradicale.websync.handler import WebSocketFrame

        frame = WebSocketFrame.build_ping_frame(b"ping-data")

        opcode, fin, payload, length = WebSocketFrame.parse_frame(frame)

        assert opcode == WebSocketFrame.PING
        assert payload == b"ping-data"

    def test_build_pong_frame(self):
        """Test building pong frame."""
        from moreradicale.websync.handler import WebSocketFrame

        frame = WebSocketFrame.build_pong_frame(b"pong-data")

        opcode, fin, payload, length = WebSocketFrame.parse_frame(frame)

        assert opcode == WebSocketFrame.PONG
        assert payload == b"pong-data"

    def test_extended_length_126(self):
        """Test frame with 126-byte length encoding."""
        from moreradicale.websync.handler import WebSocketFrame

        # Create payload > 125 bytes but < 65536
        payload_text = "x" * 200

        frame = WebSocketFrame.build_text_frame(payload_text)
        opcode, fin, payload, length = WebSocketFrame.parse_frame(frame)

        assert payload.decode("utf-8") == payload_text

    def test_frame_too_short(self):
        """Test handling of too-short frame."""
        from moreradicale.websync.handler import WebSocketFrame

        with pytest.raises(ValueError, match="too short"):
            WebSocketFrame.parse_frame(b"\x81")


class TestNotifyChangeFunction:
    """Tests for notify_change convenience function."""

    def setup_method(self):
        """Reset manager before each test."""
        from moreradicale.websync.manager import websync_manager
        websync_manager.reset()

    def test_notify_change(self):
        """Test notify_change convenience function."""
        from moreradicale.websync.manager import websync_manager
        from moreradicale.websync.handler import notify_change

        messages = []
        websync_manager.register_connection("conn-1", "user1", messages.append)
        websync_manager.subscribe("conn-1", "/user1/calendar/")

        notify_change(
            "/user1/calendar/event.ics",
            "create",
            sync_token="token-123"
        )

        assert len(messages) == 1
        data = json.loads(messages[0])
        assert data["type"] == "create"
        assert data["sync_token"] == "token-123"

    def test_notify_change_invalid_type(self):
        """Test notify_change with invalid type defaults to update."""
        from moreradicale.websync.manager import websync_manager
        from moreradicale.websync.handler import notify_change

        messages = []
        websync_manager.register_connection("conn-1", "user1", messages.append)
        websync_manager.subscribe("conn-1", "/user1/calendar/")

        notify_change(
            "/user1/calendar/event.ics",
            "invalid_type"
        )

        assert len(messages) == 1
        data = json.loads(messages[0])
        assert data["type"] == "update"  # Falls back to update

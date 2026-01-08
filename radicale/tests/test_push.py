"""
Tests for RFC 8030 Web Push notifications.

Tests the push subscription management, VAPID keys, storage,
and WebDAV property discovery.
"""

import json
import os
import tempfile
from io import BytesIO

import pytest

from radicale import config
from radicale.tests import BaseTest


def make_configuration(tmpdir, push_config=None, storage_config=None):
    """Create a configuration for push tests."""
    configuration = config.load()

    defaults = {
        "storage": {"filesystem_folder": tmpdir, "_filesystem_fsync": "False"},
        "push": {
            "enabled": "True",
            "vapid_private_key": "",
            "vapid_subject": "mailto:test@example.com",
            "subscription_folder": "",
            "ttl": "86400",
            "urgency": "normal",
            "batch_interval": "5"
        }
    }

    if storage_config:
        defaults["storage"].update(storage_config)
    if push_config:
        defaults["push"].update(push_config)

    configuration.update(defaults, "test", privileged=True)
    return configuration


class TestPushSubscription:
    """Tests for PushSubscription model and helpers."""

    def test_subscription_creation(self):
        """Test creating a push subscription."""
        from radicale.push.subscription import PushSubscription

        sub = PushSubscription(
            id="test-id-123",
            user="testuser",
            collection_path="/testuser/calendar/",
            endpoint="https://push.example.com/abc123",
            p256dh_key="BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM=",
            auth_key="tBHItJI5svbpez7KI4CCXg=="
        )

        assert sub.id == "test-id-123"
        assert sub.user == "testuser"
        assert sub.collection_path == "/testuser/calendar/"
        assert sub.endpoint == "https://push.example.com/abc123"
        assert sub.created_at is not None
        assert sub.last_used is None

    def test_subscription_to_dict(self):
        """Test serializing subscription to dict."""
        from radicale.push.subscription import PushSubscription

        sub = PushSubscription(
            id="test-id",
            user="user1",
            collection_path="/user1/cal/",
            endpoint="https://push.example.com/xyz",
            p256dh_key="key123",
            auth_key="auth456",
            user_agent="Mozilla/5.0"
        )

        data = sub.to_dict()
        assert data["id"] == "test-id"
        assert data["user"] == "user1"
        assert data["collection_path"] == "/user1/cal/"
        assert data["endpoint"] == "https://push.example.com/xyz"
        assert data["p256dh_key"] == "key123"
        assert data["auth_key"] == "auth456"
        assert data["user_agent"] == "Mozilla/5.0"

    def test_subscription_from_dict(self):
        """Test deserializing subscription from dict."""
        from radicale.push.subscription import PushSubscription

        data = {
            "id": "sub-123",
            "user": "alice",
            "collection_path": "/alice/contacts/",
            "endpoint": "https://push.example.com/endpoint",
            "p256dh_key": "publickey",
            "auth_key": "authkey",
            "created_at": "2024-01-01T00:00:00Z",
            "last_used": "2024-01-02T12:00:00Z"
        }

        sub = PushSubscription.from_dict(data)
        assert sub.id == "sub-123"
        assert sub.user == "alice"
        assert sub.collection_path == "/alice/contacts/"
        assert sub.created_at == "2024-01-01T00:00:00Z"
        assert sub.last_used == "2024-01-02T12:00:00Z"

    def test_subscription_roundtrip(self):
        """Test serialization roundtrip."""
        from radicale.push.subscription import PushSubscription

        original = PushSubscription(
            id="roundtrip-id",
            user="bob",
            collection_path="/bob/calendar/",
            endpoint="https://push.example.com/bob",
            p256dh_key="p256dhkey",
            auth_key="authkey"
        )

        data = original.to_dict()
        restored = PushSubscription.from_dict(data)

        assert restored.id == original.id
        assert restored.user == original.user
        assert restored.collection_path == original.collection_path
        assert restored.endpoint == original.endpoint
        assert restored.p256dh_key == original.p256dh_key
        assert restored.auth_key == original.auth_key

    def test_subscription_to_webpush_info(self):
        """Test conversion to pywebpush format."""
        from radicale.push.subscription import PushSubscription

        sub = PushSubscription(
            id="test",
            user="user",
            collection_path="/path/",
            endpoint="https://fcm.googleapis.com/fcm/send/abc123",
            p256dh_key="public_key_base64",
            auth_key="auth_secret_base64"
        )

        info = sub.to_webpush_info()
        assert info["endpoint"] == "https://fcm.googleapis.com/fcm/send/abc123"
        assert info["keys"]["p256dh"] == "public_key_base64"
        assert info["keys"]["auth"] == "auth_secret_base64"

    def test_subscription_mark_used(self):
        """Test marking subscription as used."""
        from radicale.push.subscription import PushSubscription

        sub = PushSubscription(
            id="test",
            user="user",
            collection_path="/path/",
            endpoint="https://push.example.com/",
            p256dh_key="key",
            auth_key="auth"
        )

        assert sub.last_used is None
        sub.mark_used()
        assert sub.last_used is not None


class TestPushHelpers:
    """Tests for push subscription helper functions."""

    def test_generate_subscription_id_unique(self):
        """Test that subscription IDs are unique."""
        from radicale.push.subscription import generate_subscription_id

        ids = set()
        for _ in range(100):
            sub_id = generate_subscription_id()
            assert sub_id not in ids
            ids.add(sub_id)

    def test_generate_pushkey_deterministic(self):
        """Test that pushkey is deterministic for same inputs."""
        from radicale.push.subscription import generate_pushkey

        key1 = generate_pushkey("/user/calendar/", "user1")
        key2 = generate_pushkey("/user/calendar/", "user1")

        assert key1 == key2

    def test_generate_pushkey_unique_per_collection(self):
        """Test that different collections get different pushkeys."""
        from radicale.push.subscription import generate_pushkey

        key1 = generate_pushkey("/user/calendar1/", "user1")
        key2 = generate_pushkey("/user/calendar2/", "user1")

        assert key1 != key2

    def test_generate_pushkey_unique_per_user(self):
        """Test that different users get different pushkeys."""
        from radicale.push.subscription import generate_pushkey

        key1 = generate_pushkey("/shared/calendar/", "user1")
        key2 = generate_pushkey("/shared/calendar/", "user2")

        assert key1 != key2

    def test_generate_pushkey_with_secret(self):
        """Test pushkey generation with secret."""
        from radicale.push.subscription import generate_pushkey

        key1 = generate_pushkey("/user/cal/", "user", "secret1")
        key2 = generate_pushkey("/user/cal/", "user", "secret2")

        assert key1 != key2

    def test_parse_subscription_request_valid(self):
        """Test parsing valid subscription request."""
        from radicale.push.subscription import parse_subscription_request

        data = json.dumps({
            "endpoint": "https://push.example.com/send/abc",
            "keys": {
                "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM=",
                "auth": "tBHItJI5svbpez7KI4CCXg=="
            }
        })

        result = parse_subscription_request(data)
        assert result is not None
        assert result["endpoint"] == "https://push.example.com/send/abc"
        assert "p256dh_key" in result
        assert "auth_key" in result

    def test_parse_subscription_request_missing_endpoint(self):
        """Test parsing request without endpoint."""
        from radicale.push.subscription import parse_subscription_request

        data = json.dumps({
            "keys": {
                "p256dh": "key",
                "auth": "auth"
            }
        })

        result = parse_subscription_request(data)
        assert result is None

    def test_parse_subscription_request_missing_keys(self):
        """Test parsing request without keys."""
        from radicale.push.subscription import parse_subscription_request

        data = json.dumps({
            "endpoint": "https://push.example.com/"
        })

        result = parse_subscription_request(data)
        assert result is None

    def test_parse_subscription_request_missing_p256dh(self):
        """Test parsing request without p256dh key."""
        from radicale.push.subscription import parse_subscription_request

        data = json.dumps({
            "endpoint": "https://push.example.com/",
            "keys": {
                "auth": "auth"
            }
        })

        result = parse_subscription_request(data)
        assert result is None

    def test_parse_subscription_request_invalid_json(self):
        """Test parsing invalid JSON."""
        from radicale.push.subscription import parse_subscription_request

        result = parse_subscription_request("not valid json")
        assert result is None


class TestVAPIDKeyManager:
    """Tests for VAPID key management."""

    def test_vapid_key_generation(self):
        """Test VAPID key generation."""
        from radicale.push.vapid import VAPIDKeyManager, HAS_CRYPTOGRAPHY

        if not HAS_CRYPTOGRAPHY:
            pytest.skip("cryptography package not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            manager = VAPIDKeyManager(configuration)
            result = manager.load_or_generate_keys()

            assert result is True
            assert manager.get_public_key_base64() is not None

    def test_vapid_key_persistence(self):
        """Test that VAPID keys persist across instances."""
        from radicale.push.vapid import VAPIDKeyManager, HAS_CRYPTOGRAPHY

        if not HAS_CRYPTOGRAPHY:
            pytest.skip("cryptography package not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)

            # First instance generates keys
            manager1 = VAPIDKeyManager(configuration)
            manager1.load_or_generate_keys()
            public_key1 = manager1.get_public_key_base64()

            # Second instance loads existing keys
            manager2 = VAPIDKeyManager(configuration)
            manager2.load_or_generate_keys()
            public_key2 = manager2.get_public_key_base64()

            assert public_key1 == public_key2

    def test_vapid_claims(self):
        """Test VAPID claims generation."""
        from radicale.push.vapid import VAPIDKeyManager, HAS_CRYPTOGRAPHY

        if not HAS_CRYPTOGRAPHY:
            pytest.skip("cryptography package not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir, push_config={
                "vapid_subject": "mailto:admin@calendar.example.com"
            })

            manager = VAPIDKeyManager(configuration)
            manager.load_or_generate_keys()

            claims = manager.get_vapid_claims()
            assert claims["sub"] == "mailto:admin@calendar.example.com"

    def test_vapid_public_key_format(self):
        """Test that public key is in correct format for browsers."""
        from radicale.push.vapid import VAPIDKeyManager, HAS_CRYPTOGRAPHY

        if not HAS_CRYPTOGRAPHY:
            pytest.skip("cryptography package not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            manager = VAPIDKeyManager(configuration)
            manager.load_or_generate_keys()

            public_key = manager.get_public_key_base64()

            # Should be base64 URL-safe encoded (no padding, no + or /)
            assert "+" not in public_key
            assert "/" not in public_key
            assert not public_key.endswith("=")

            # Should be 65 bytes when decoded (uncompressed EC point)
            import base64
            decoded = base64.urlsafe_b64decode(public_key + "==")
            assert len(decoded) == 65

    def test_check_vapid_dependencies(self):
        """Test dependency checking."""
        from radicale.push.vapid import check_vapid_dependencies

        available, message = check_vapid_dependencies()
        # Just verify it returns the expected types
        assert isinstance(available, bool)
        assert isinstance(message, str)


class TestSubscriptionStorage:
    """Tests for push subscription storage."""

    def test_storage_add_subscription(self):
        """Test adding a subscription."""
        from radicale.push.storage import SubscriptionStorage
        from radicale.push.subscription import PushSubscription

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            storage = SubscriptionStorage(configuration)

            sub = PushSubscription(
                id="add-test-id",
                user="testuser",
                collection_path="/testuser/calendar/",
                endpoint="https://push.example.com/add",
                p256dh_key="key",
                auth_key="auth"
            )

            result = storage.add_subscription(sub)
            assert result is True

            # Verify file was created
            retrieved = storage.get_subscription("add-test-id")
            assert retrieved is not None
            assert retrieved.user == "testuser"

    def test_storage_remove_subscription(self):
        """Test removing a subscription."""
        from radicale.push.storage import SubscriptionStorage
        from radicale.push.subscription import PushSubscription

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            storage = SubscriptionStorage(configuration)

            sub = PushSubscription(
                id="remove-test-id",
                user="testuser",
                collection_path="/testuser/calendar/",
                endpoint="https://push.example.com/remove",
                p256dh_key="key",
                auth_key="auth"
            )

            storage.add_subscription(sub)
            assert storage.get_subscription("remove-test-id") is not None

            result = storage.remove_subscription("remove-test-id")
            assert result is True
            assert storage.get_subscription("remove-test-id") is None

    def test_storage_get_collection_subscriptions(self):
        """Test getting all subscriptions for a collection."""
        from radicale.push.storage import SubscriptionStorage
        from radicale.push.subscription import PushSubscription

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            storage = SubscriptionStorage(configuration)

            # Add subscriptions for same collection from different users
            for i in range(3):
                sub = PushSubscription(
                    id=f"coll-sub-{i}",
                    user=f"user{i}",
                    collection_path="/shared/calendar/",
                    endpoint=f"https://push.example.com/{i}",
                    p256dh_key="key",
                    auth_key="auth"
                )
                storage.add_subscription(sub)

            # Add subscription for different collection
            other = PushSubscription(
                id="other-sub",
                user="user0",
                collection_path="/other/calendar/",
                endpoint="https://push.example.com/other",
                p256dh_key="key",
                auth_key="auth"
            )
            storage.add_subscription(other)

            subs = storage.get_collection_subscriptions("/shared/calendar/")
            assert len(subs) == 3

    def test_storage_get_user_subscriptions(self):
        """Test getting all subscriptions for a user."""
        from radicale.push.storage import SubscriptionStorage
        from radicale.push.subscription import PushSubscription

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            storage = SubscriptionStorage(configuration)

            # Add subscriptions for same user
            for i in range(3):
                sub = PushSubscription(
                    id=f"user-sub-{i}",
                    user="alice",
                    collection_path=f"/alice/calendar{i}/",
                    endpoint=f"https://push.example.com/alice/{i}",
                    p256dh_key="key",
                    auth_key="auth"
                )
                storage.add_subscription(sub)

            # Add subscription for different user
            other = PushSubscription(
                id="bob-sub",
                user="bob",
                collection_path="/bob/calendar/",
                endpoint="https://push.example.com/bob",
                p256dh_key="key",
                auth_key="auth"
            )
            storage.add_subscription(other)

            subs = storage.get_user_subscriptions("alice")
            assert len(subs) == 3

            bob_subs = storage.get_user_subscriptions("bob")
            assert len(bob_subs) == 1

    def test_storage_custom_folder(self):
        """Test using custom subscription folder."""
        from radicale.push.storage import SubscriptionStorage
        from radicale.push.subscription import PushSubscription

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_folder = os.path.join(tmpdir, "custom_push")
            configuration = make_configuration(tmpdir, push_config={
                "subscription_folder": custom_folder
            })
            storage = SubscriptionStorage(configuration)

            sub = PushSubscription(
                id="custom-folder-test",
                user="testuser",
                collection_path="/testuser/calendar/",
                endpoint="https://push.example.com/",
                p256dh_key="key",
                auth_key="auth"
            )

            storage.add_subscription(sub)

            # Verify custom folder was used
            assert os.path.exists(custom_folder)

    def test_storage_list_all_subscriptions(self):
        """Test listing all subscriptions."""
        from radicale.push.storage import SubscriptionStorage
        from radicale.push.subscription import PushSubscription

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            storage = SubscriptionStorage(configuration)

            # Add multiple subscriptions
            for i in range(5):
                sub = PushSubscription(
                    id=f"list-all-{i}",
                    user=f"user{i % 2}",
                    collection_path=f"/user{i % 2}/calendar{i}/",
                    endpoint=f"https://push.example.com/{i}",
                    p256dh_key="key",
                    auth_key="auth"
                )
                storage.add_subscription(sub)

            all_subs = storage.list_all_subscriptions()
            assert len(all_subs) == 5


class TestPushHandler:
    """Tests for push HTTP handler."""

    def test_handle_get_vapid_key(self):
        """Test getting VAPID public key."""
        from radicale.push.handler import PushHandler
        from radicale.push.vapid import HAS_CRYPTOGRAPHY

        if not HAS_CRYPTOGRAPHY:
            pytest.skip("cryptography package not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            handler = PushHandler(configuration)
            status, headers, body, _ = handler.handle_get_vapid_key()

            assert status == 200
            assert headers["Content-Type"] == "application/json"

            data = json.loads(body)
            assert "publicKey" in data
            assert len(data["publicKey"]) > 0

    def test_handle_list_subscriptions_empty(self):
        """Test listing subscriptions when none exist."""
        from radicale.push.handler import PushHandler
        from radicale.push.vapid import HAS_CRYPTOGRAPHY

        if not HAS_CRYPTOGRAPHY:
            pytest.skip("cryptography package not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            handler = PushHandler(configuration)
            status, headers, body, _ = handler.handle_list_subscriptions("testuser")

            assert status == 200
            data = json.loads(body)
            assert "subscriptions" in data
            assert len(data["subscriptions"]) == 0


class TestPushPathParsing:
    """Tests for push endpoint path parsing."""

    def test_should_handle_push_request(self):
        """Test detecting push request paths."""
        from radicale.push.handler import should_handle_push_request

        assert should_handle_push_request("/.push/") is True
        assert should_handle_push_request("/.push/subscribe") is True
        assert should_handle_push_request("/.push/subscription/abc123") is True
        assert should_handle_push_request("/.push/vapid-key") is True
        assert should_handle_push_request("/.push") is True

        assert should_handle_push_request("/calendar/") is False
        assert should_handle_push_request("/user/calendar.ics") is False
        assert should_handle_push_request("/.well-known/caldav") is False

    def test_parse_push_path_subscribe(self):
        """Test parsing subscribe path."""
        from radicale.push.handler import parse_push_path

        action, sub_id = parse_push_path("/.push/subscribe")
        assert action == "subscribe"
        assert sub_id is None

    def test_parse_push_path_subscription(self):
        """Test parsing subscription management path."""
        from radicale.push.handler import parse_push_path

        action, sub_id = parse_push_path("/.push/subscription/abc123")
        assert action == "subscription"
        assert sub_id == "abc123"

    def test_parse_push_path_vapid_key(self):
        """Test parsing VAPID key path."""
        from radicale.push.handler import parse_push_path

        action, sub_id = parse_push_path("/.push/vapid-key")
        assert action == "vapid-key"
        assert sub_id is None

    def test_parse_push_path_root(self):
        """Test parsing root push path."""
        from radicale.push.handler import parse_push_path

        action, sub_id = parse_push_path("/.push")
        assert action == "vapid-key"
        assert sub_id is None


class TestPushSender:
    """Tests for push notification sender."""

    def test_sender_initialization(self):
        """Test sender initialization."""
        from radicale.push.sender import PushSender

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            sender = PushSender(configuration)
            # Just verify initialization doesn't crash
            assert sender._ttl == 86400
            assert sender._urgency == "normal"

    def test_sender_invalid_urgency(self):
        """Test sender handles invalid urgency."""
        from radicale.push.sender import PushSender

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir, push_config={
                "urgency": "invalid-urgency"
            })
            sender = PushSender(configuration)
            # Should fall back to normal
            assert sender._urgency == "normal"

    def test_sender_build_payload(self):
        """Test building notification payload."""
        from radicale.push.sender import PushSender

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir)
            sender = PushSender(configuration)
            payload = sender._build_payload(
                "/user/calendar/",
                "update",
                "event.ics"
            )

            data = json.loads(payload)
            assert data["type"] == "collection-changed"
            assert data["collection"] == "/user/calendar/"
            assert data["change"] == "update"
            assert data["href"] == "event.ics"
            assert "timestamp" in data


class TestPushNotifier:
    """Tests for high-level push notifier."""

    def test_notifier_disabled(self):
        """Test notifier does nothing when disabled."""
        from radicale.push.sender import PushNotifier

        with tempfile.TemporaryDirectory() as tmpdir:
            configuration = make_configuration(tmpdir, push_config={
                "enabled": "False"
            })
            notifier = PushNotifier(configuration)
            # Should not raise or do anything
            notifier.notify("/user/calendar/", "update", "event.ics", "user")


class TestPushWebDAVIntegration(BaseTest):
    """Tests for push-related WebDAV properties."""

    def test_push_disabled_vapid_key_404(self):
        """Test VAPID key endpoint returns 404 when push disabled."""
        self.configure({"push": {"enabled": "False"}})
        status, _, _ = self.request("GET", "/.push/vapid-key")
        assert status == 404

    def test_push_enabled_vapid_key(self):
        """Test VAPID key endpoint returns key when push enabled."""
        from radicale.push.vapid import HAS_CRYPTOGRAPHY

        if not HAS_CRYPTOGRAPHY:
            pytest.skip("cryptography package not available")

        self.configure({
            "push": {
                "enabled": "True",
                "vapid_subject": "mailto:test@example.com"
            }
        })
        status, headers, body = self.request("GET", "/.push/vapid-key")
        assert status == 200
        data = json.loads(body)
        assert "publicKey" in data

    def test_push_subscribe_requires_auth(self):
        """Test subscribe endpoint requires authentication."""
        from radicale.push.vapid import HAS_CRYPTOGRAPHY

        if not HAS_CRYPTOGRAPHY:
            pytest.skip("cryptography package not available")

        self.configure({
            "push": {
                "enabled": "True",
                "vapid_subject": "mailto:test@example.com"
            },
            "auth": {"type": "none"}
        })

        # Without login, should get 401 or redirect
        status, _, _ = self.request(
            "POST", "/.push/subscribe",
            data=json.dumps({
                "endpoint": "https://push.example.com/",
                "keys": {"p256dh": "key", "auth": "auth"}
            }),
            content_type="application/json"
        )
        # Should not be 200/201
        assert status != 201

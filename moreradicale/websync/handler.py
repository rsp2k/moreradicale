"""
WebSocket Handler for Real-time Sync.

Handles WebSocket upgrade requests and message processing.
Uses the websockets library if available, with fallback support
for basic HTTP long-polling.
"""

import base64
import hashlib
import json
import struct
import uuid
from typing import Dict, Optional, Tuple

from moreradicale import config
from moreradicale.log import logger
from moreradicale.websync.manager import websync_manager, NotificationType


class WebSyncHandler:
    """
    HTTP handler for WebSocket sync endpoint.

    Supports:
    - WebSocket upgrade handshake (RFC 6455)
    - Message framing and parsing
    - Subscription management
    - Fallback long-polling for non-WebSocket clients
    """

    # WebSocket GUID for handshake
    WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, configuration: config.Configuration):
        """Initialize handler."""
        self._configuration = configuration
        self._enabled = configuration.get("websync", "enabled")
        self._require_auth = configuration.get("websync", "require_auth")
        self._ping_interval = configuration.get("websync", "ping_interval")

    @property
    def enabled(self) -> bool:
        """Check if WebSync is enabled."""
        return self._enabled

    def is_websocket_request(self, environ: dict) -> bool:
        """Check if request is a WebSocket upgrade request."""
        upgrade = environ.get("HTTP_UPGRADE", "").lower()
        connection = environ.get("HTTP_CONNECTION", "").lower()
        return "websocket" in upgrade and "upgrade" in connection

    def handle_upgrade(
        self,
        environ: dict,
        user: str
    ) -> Tuple[int, Dict[str, str], bytes]:
        """
        Handle WebSocket upgrade request.

        Args:
            environ: WSGI environ
            user: Authenticated user

        Returns:
            Tuple of (status, headers, body)
        """
        if not self._enabled:
            return 404, {}, b"WebSync disabled"

        if self._require_auth and not user:
            return 401, {"WWW-Authenticate": 'Basic realm="Radicale WebSync"'}, b""

        # Validate WebSocket version
        ws_version = environ.get("HTTP_SEC_WEBSOCKET_VERSION", "")
        if ws_version != "13":
            return 400, {}, b"Unsupported WebSocket version"

        # Get WebSocket key
        ws_key = environ.get("HTTP_SEC_WEBSOCKET_KEY", "")
        if not ws_key:
            return 400, {}, b"Missing Sec-WebSocket-Key"

        # Generate accept key
        accept_key = self._compute_accept_key(ws_key)

        # Generate connection ID
        connection_id = str(uuid.uuid4())

        # WebSocket upgrade response headers
        headers = {
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Accept": accept_key,
            "X-WebSync-Connection-ID": connection_id,
        }

        logger.info("WebSync: Upgrade request from user %s, connection %s",
                    user, connection_id)

        return 101, headers, b""

    def _compute_accept_key(self, ws_key: str) -> str:
        """Compute Sec-WebSocket-Accept value."""
        key = ws_key + self.WS_GUID
        sha1 = hashlib.sha1(key.encode()).digest()
        return base64.b64encode(sha1).decode()

    def handle_message(
        self,
        connection_id: str,
        message: str,
        user: str
    ) -> Optional[str]:
        """
        Handle incoming WebSocket message.

        Args:
            connection_id: Connection identifier
            message: JSON message from client
            user: Authenticated user

        Returns:
            Response message or None
        """
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid JSON"})

        action = data.get("action")
        path = data.get("path", "")

        if action == "subscribe":
            if not self._can_access_path(user, path):
                return json.dumps({"error": "Access denied", "path": path})

            if websync_manager.subscribe(connection_id, path):
                return json.dumps({
                    "status": "subscribed",
                    "path": path
                })
            else:
                return json.dumps({"error": "Subscription failed"})

        elif action == "unsubscribe":
            if websync_manager.unsubscribe(connection_id, path):
                return json.dumps({
                    "status": "unsubscribed",
                    "path": path
                })
            else:
                return json.dumps({"error": "Unsubscribe failed"})

        elif action == "ping":
            return json.dumps({"action": "pong", "timestamp": data.get("timestamp")})

        elif action == "status":
            stats = websync_manager.get_stats()
            return json.dumps({"status": "ok", "stats": stats})

        else:
            return json.dumps({"error": f"Unknown action: {action}"})

    def _can_access_path(self, user: str, path: str) -> bool:
        """
        Check if user can access a path.

        Basic check - user can access paths starting with their username.
        """
        if not user:
            return False

        # Normalize path
        if not path.startswith("/"):
            path = "/" + path

        parts = path.split("/")
        if len(parts) < 2:
            return False

        # User can access their own collections
        path_user = parts[1]
        return path_user == user

    def handle_long_poll(
        self,
        environ: dict,
        user: str
    ) -> Tuple[int, Dict[str, str], bytes]:
        """
        Handle long-polling fallback for non-WebSocket clients.

        Args:
            environ: WSGI environ
            user: Authenticated user

        Returns:
            Tuple of (status, headers, body)
        """
        if not self._enabled:
            return 404, {}, b"WebSync disabled"

        if self._require_auth and not user:
            return 401, {"WWW-Authenticate": 'Basic realm="Radicale WebSync"'}, b""

        # Return current stats as JSON
        stats = websync_manager.get_stats()

        body = json.dumps({
            "type": "status",
            "websocket_supported": True,
            "long_poll_fallback": True,
            "stats": stats,
        })

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }

        return 200, headers, body.encode()


class WebSocketFrame:
    """
    WebSocket frame parser/builder.

    Implements RFC 6455 frame format for message encoding/decoding.
    """

    # Opcodes
    CONTINUATION = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA

    @classmethod
    def parse_frame(cls, data: bytes) -> Tuple[int, bool, bytes, int]:
        """
        Parse a WebSocket frame.

        Args:
            data: Raw frame bytes

        Returns:
            Tuple of (opcode, fin, payload, total_length)
        """
        if len(data) < 2:
            raise ValueError("Frame too short")

        fin = (data[0] & 0x80) != 0
        opcode = data[0] & 0x0F
        masked = (data[1] & 0x80) != 0
        length = data[1] & 0x7F

        offset = 2

        # Extended length
        if length == 126:
            if len(data) < 4:
                raise ValueError("Frame too short for extended length")
            length = struct.unpack(">H", data[2:4])[0]
            offset = 4
        elif length == 127:
            if len(data) < 10:
                raise ValueError("Frame too short for extended length")
            length = struct.unpack(">Q", data[2:10])[0]
            offset = 10

        # Masking key
        mask = None
        if masked:
            if len(data) < offset + 4:
                raise ValueError("Frame too short for mask")
            mask = data[offset:offset + 4]
            offset += 4

        # Payload
        if len(data) < offset + length:
            raise ValueError("Frame too short for payload")

        payload = data[offset:offset + length]

        # Unmask if necessary
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

        return opcode, fin, payload, offset + length

    @classmethod
    def build_frame(
        cls,
        payload: bytes,
        opcode: int = TEXT,
        fin: bool = True,
        mask: bool = False
    ) -> bytes:
        """
        Build a WebSocket frame.

        Args:
            payload: Frame payload
            opcode: Frame opcode
            fin: Final fragment flag
            mask: Whether to mask payload

        Returns:
            Encoded frame bytes
        """
        frame = bytearray()

        # First byte: FIN + opcode
        first_byte = opcode
        if fin:
            first_byte |= 0x80
        frame.append(first_byte)

        # Second byte: mask flag + length
        length = len(payload)
        if mask:
            length_byte = 0x80
        else:
            length_byte = 0x00

        if length < 126:
            frame.append(length_byte | length)
        elif length < 65536:
            frame.append(length_byte | 126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(length_byte | 127)
            frame.extend(struct.pack(">Q", length))

        # Masking key (if masking)
        if mask:
            import os
            mask_key = os.urandom(4)
            frame.extend(mask_key)
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        # Payload
        frame.extend(payload)

        return bytes(frame)

    @classmethod
    def build_text_frame(cls, text: str) -> bytes:
        """Build a text frame from string."""
        return cls.build_frame(text.encode("utf-8"), opcode=cls.TEXT)

    @classmethod
    def build_close_frame(cls, code: int = 1000, reason: str = "") -> bytes:
        """Build a close frame."""
        payload = struct.pack(">H", code) + reason.encode("utf-8")
        return cls.build_frame(payload, opcode=cls.CLOSE)

    @classmethod
    def build_ping_frame(cls, data: bytes = b"") -> bytes:
        """Build a ping frame."""
        return cls.build_frame(data, opcode=cls.PING)

    @classmethod
    def build_pong_frame(cls, data: bytes = b"") -> bytes:
        """Build a pong frame."""
        return cls.build_frame(data, opcode=cls.PONG)


def notify_change(
    path: str,
    change_type: str = "update",
    sync_token: Optional[str] = None,
    etag: Optional[str] = None,
    user: Optional[str] = None
):
    """
    Convenience function to notify WebSync of a change.

    Call this from storage hooks or other modules when data changes.

    Args:
        path: Path that changed
        change_type: Type of change (create, update, delete, sync)
        sync_token: Current sync token
        etag: Item ETag
        user: User who made the change
    """
    try:
        notification_type = NotificationType(change_type)
    except ValueError:
        notification_type = NotificationType.UPDATE

    websync_manager.notify(
        notification_type=notification_type,
        path=path,
        sync_token=sync_token,
        etag=etag,
        user=user
    )

"""
VAPID (Voluntary Application Server Identification) key management.

RFC 8292 defines VAPID for identifying push message senders to push services.
"""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from moreradicale.log import logger

# Check if cryptography is available (required for VAPID)
try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


class VAPIDKeyManager:
    """
    Manages VAPID key pair for Web Push authentication.

    VAPID keys are used to:
    1. Identify the application server to push services
    2. Sign push message requests for authentication
    """

    def __init__(self, configuration):
        """
        Initialize VAPID key manager.

        Args:
            configuration: Radicale configuration instance
        """
        self._configuration = configuration
        self._private_key = None
        self._public_key = None
        self._claims = {}

        # Get configuration
        self._private_key_path = configuration.get("push", "vapid_private_key")
        self._subject = configuration.get("push", "vapid_subject")

        if not self._subject:
            logger.warning("VAPID subject not configured - push notifications may be rejected")

    def _get_storage_path(self) -> Path:
        """Get path for storing generated keys."""
        storage_folder = self._configuration.get("storage", "filesystem_folder")
        return Path(storage_folder) / ".Radicale.vapid"

    def load_or_generate_keys(self) -> bool:
        """
        Load existing VAPID keys or generate new ones.

        Returns:
            True if keys are available, False on error
        """
        if not HAS_CRYPTOGRAPHY:
            logger.error("cryptography package required for VAPID - install with: pip install cryptography")
            return False

        # Try to load from configured path
        if self._private_key_path and os.path.exists(self._private_key_path):
            return self._load_keys_from_file(self._private_key_path)

        # Try to load from storage folder
        storage_key_path = self._get_storage_path()
        if storage_key_path.exists():
            return self._load_keys_from_file(str(storage_key_path))

        # Generate new keys
        logger.info("Generating new VAPID keys")
        return self._generate_keys()

    def _load_keys_from_file(self, path: str) -> bool:
        """Load VAPID keys from PEM file."""
        try:
            with open(path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None,
                    backend=default_backend()
                )
            self._public_key = self._private_key.public_key()
            logger.info("Loaded VAPID keys from %s", path)
            return True
        except Exception as e:
            logger.error("Failed to load VAPID keys from %s: %s", path, e)
            return False

    def _generate_keys(self) -> bool:
        """Generate new VAPID key pair."""
        try:
            # Generate EC P-256 key pair
            self._private_key = ec.generate_private_key(
                ec.SECP256R1(),
                default_backend()
            )
            self._public_key = self._private_key.public_key()

            # Save to storage folder
            storage_key_path = self._get_storage_path()
            private_pem = self._private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )

            with open(storage_key_path, "wb") as f:
                f.write(private_pem)

            # Set restrictive permissions
            os.chmod(storage_key_path, 0o600)

            logger.info("Generated and saved VAPID keys to %s", storage_key_path)
            return True

        except Exception as e:
            logger.error("Failed to generate VAPID keys: %s", e)
            return False

    def get_public_key_base64(self) -> Optional[str]:
        """
        Get the public key in Base64 URL-safe encoding.

        This is the format needed for browser subscription requests.
        """
        if not self._public_key:
            return None

        try:
            # Export as uncompressed point format
            public_bytes = self._public_key.public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint
            )

            import base64
            return base64.urlsafe_b64encode(public_bytes).decode().rstrip("=")

        except Exception as e:
            logger.error("Failed to export public key: %s", e)
            return None

    def get_vapid_claims(self) -> Dict:
        """
        Get VAPID claims for signing push requests.

        Returns:
            Dict with 'sub' (subject) claim
        """
        claims = {}
        if self._subject:
            claims["sub"] = self._subject
        return claims

    def get_private_key_for_webpush(self) -> Optional[str]:
        """
        Get private key in format for pywebpush.

        Returns:
            Private key bytes or None
        """
        if not self._private_key:
            return None

        try:
            # pywebpush expects the raw private key bytes
            private_bytes = self._private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            return private_bytes

        except Exception as e:
            logger.error("Failed to export private key: %s", e)
            return None


def check_vapid_dependencies() -> Tuple[bool, str]:
    """
    Check if VAPID dependencies are available.

    Returns:
        Tuple of (available, message)
    """
    if not HAS_CRYPTOGRAPHY:
        return False, "cryptography package required - install with: pip install cryptography"

    try:
        import pywebpush  # noqa: F401
        return True, "VAPID dependencies available"
    except ImportError:
        return False, "pywebpush package required - install with: pip install pywebpush"

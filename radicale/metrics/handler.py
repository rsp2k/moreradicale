"""
Prometheus Metrics HTTP Handler.

Handles requests to /.metrics endpoint.
"""

import base64
from typing import Optional, Tuple

from radicale.log import logger
from radicale.metrics.collector import metrics


class MetricsHandler:
    """
    HTTP handler for Prometheus metrics endpoint.

    Serves metrics at /.metrics in Prometheus text format.
    """

    def __init__(self, configuration, auth_backend=None):
        """
        Initialize the metrics handler.

        Args:
            configuration: Radicale configuration
            auth_backend: Optional auth backend for authenticated access
        """
        self._configuration = configuration
        self._enabled = configuration.get("metrics", "enabled")
        self._require_auth = configuration.get("metrics", "require_auth")
        self._auth = auth_backend

        # Set version from configuration or detect
        try:
            from importlib.metadata import version
            radicale_version = version("radicale")
        except Exception:
            radicale_version = "unknown"
        metrics.set_version(radicale_version)

    def _get_user_from_environ(self, environ: dict) -> Optional[str]:
        """
        Extract and validate user from HTTP Basic auth header.

        Args:
            environ: WSGI environ dict

        Returns:
            Username if authenticated, None otherwise
        """
        if not self._auth:
            return None

        authorization = environ.get("HTTP_AUTHORIZATION", "")
        if not authorization.startswith("Basic"):
            return None

        try:
            authorization = authorization[len("Basic"):].strip()
            decoded = base64.b64decode(authorization.encode("ascii")).decode("utf-8")
            login, password = decoded.split(":", 1)

            # Validate with auth backend
            result = self._auth.login(login, password, {})
            if result:
                return result[0]  # Return username
        except Exception:
            pass

        return None

    def handle_request(
        self, environ: Optional[dict] = None, user: str = ""
    ) -> Tuple[int, dict, str]:
        """
        Handle metrics request.

        Args:
            environ: WSGI environ dict (for extracting auth if needed)
            user: Pre-authenticated user (if already known)

        Returns:
            Tuple of (status_code, headers, body)
        """
        if not self._enabled:
            return 404, {}, "Metrics endpoint disabled"

        # Check authentication if required
        if self._require_auth:
            if not user and environ:
                user = self._get_user_from_environ(environ) or ""
            if not user:
                return 401, {"WWW-Authenticate": 'Basic realm="Radicale Metrics"'}, ""

        try:
            body = metrics.export()
            headers = {
                "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            }
            return 200, headers, body

        except Exception as e:
            logger.error("Error generating metrics: %s", e)
            return 500, {}, "Internal error generating metrics"


def update_storage_metrics(storage, configuration):
    """
    Update storage-related metrics.

    Called periodically or on-demand to refresh storage stats.

    Args:
        storage: Radicale storage instance
        configuration: Radicale configuration
    """
    if not configuration.get("metrics", "enabled"):
        return

    try:
        collections = 0
        items = 0

        # Count collections and items
        for item in storage.discover("", depth="infinity"):
            if hasattr(item, "get_all"):
                # It's a collection
                collections += 1
                items += sum(1 for _ in item.get_all())

        # Estimate storage size (if filesystem storage)
        storage_bytes = 0
        filesystem_folder = configuration.get("storage", "filesystem_folder")
        if filesystem_folder:
            import os
            for dirpath, dirnames, filenames in os.walk(filesystem_folder):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        storage_bytes += os.path.getsize(fp)
                    except OSError:
                        pass

        metrics.set_storage_stats(collections, items, storage_bytes)

    except Exception as e:
        logger.debug("Error updating storage metrics: %s", e)

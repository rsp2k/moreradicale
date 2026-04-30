# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025 Ryan Malloy and contributors
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Radicale.  If not, see <http://www.gnu.org/licenses/>.

"""
IMAP polling for inbound iTIP email processing.

This module provides an alternative to webhooks for receiving iTIP responses
from external attendees. It polls an IMAP mailbox for emails containing
iTIP REPLY or COUNTER messages and routes them to ITIPProcessor.

Key features:
- Background thread with configurable poll interval
- SSL/STARTTLS security support
- Message processing with move/delete after handling
- Integration with existing email_parser and ITIPProcessor

Usage:
    # Start background polling
    poller = IMAPPoller(configuration, storage)
    poller.start()

    # Manual one-shot poll (for cron/testing)
    poller.poll_once()

    # Graceful shutdown
    poller.stop()
"""

import imaplib
import ssl
import threading
from typing import TYPE_CHECKING, Optional

from moreradicale.itip import email_parser
from moreradicale.log import logger

if TYPE_CHECKING:
    from moreradicale import config, storage


class IMAPPollerError(Exception):
    """Base exception for IMAP poller errors."""
    pass


class IMAPConnectionError(IMAPPollerError):
    """Failed to connect to IMAP server."""
    pass


class IMAPAuthenticationError(IMAPPollerError):
    """Failed to authenticate with IMAP server."""
    pass


class IMAPPoller:
    """
    Background IMAP poller for iTIP messages.

    Polls a configured IMAP mailbox for emails containing iTIP calendar
    responses (REPLY, COUNTER) from external attendees and routes them
    to ITIPProcessor for handling.
    """

    def __init__(self, configuration: "config.Configuration",
                 storage: "storage.BaseStorage") -> None:
        """
        Initialize IMAP poller.

        Args:
            configuration: Radicale configuration
            storage: Storage backend for iTIP processing
        """
        self.configuration = configuration
        self._storage = storage

        # Load configuration
        self._enabled = configuration.get("scheduling", "imap_enabled")
        self._server = configuration.get("scheduling", "imap_server")
        self._port = configuration.get("scheduling", "imap_port")
        self._security = configuration.get("scheduling", "imap_security")
        self._username = configuration.get("scheduling", "imap_username")
        self._password = configuration.get("scheduling", "imap_password")
        self._folder = configuration.get("scheduling", "imap_folder")
        self._poll_interval = configuration.get("scheduling", "imap_poll_interval")
        self._processed_folder = configuration.get("scheduling", "imap_processed_folder")
        self._failed_folder = configuration.get("scheduling", "imap_failed_folder")

        # Threading
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # ITIPProcessor lazy-loaded
        self._processor = None

    def _get_processor(self):
        """Get or create ITIPProcessor instance."""
        if self._processor is None:
            from moreradicale.itip.processor import ITIPProcessor
            # ITIPProcessor expects (storage, configuration) - not (configuration, storage)!
            self._processor = ITIPProcessor(self._storage, self.configuration)
        return self._processor

    @property
    def is_running(self) -> bool:
        """Check if background polling is active."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """
        Start background polling thread.

        Returns:
            True if started successfully, False if already running or disabled
        """
        if not self._enabled:
            logger.info("IMAP polling is disabled")
            return False

        if self.is_running:
            logger.warning("IMAP poller already running")
            return False

        if not self._server:
            logger.error("IMAP server not configured")
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="moreradicale-imap-poller",
            daemon=True
        )
        self._thread.start()

        logger.info(
            "IMAP poller started: %s@%s:%d/%s (interval=%ds)",
            self._username, self._server, self._port,
            self._folder, self._poll_interval
        )
        return True

    def stop(self, timeout: float = 10.0) -> bool:
        """
        Stop polling thread gracefully.

        Args:
            timeout: Maximum seconds to wait for thread to stop

        Returns:
            True if stopped successfully
        """
        if not self.is_running:
            return True

        logger.info("Stopping IMAP poller...")
        self._stop_event.set()

        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning("IMAP poller thread did not stop cleanly")
            return False

        self._thread = None
        logger.info("IMAP poller stopped")
        return True

    def _poll_loop(self) -> None:
        """Background polling loop."""
        logger.debug("IMAP poll loop started")

        while not self._stop_event.is_set():
            try:
                count = self.poll_once()
                if count > 0:
                    logger.info("Processed %d iTIP message(s) from IMAP", count)
            except Exception as e:
                logger.error("IMAP poll error: %s", e)

            # Wait for next poll or stop signal
            self._stop_event.wait(timeout=self._poll_interval)

        logger.debug("IMAP poll loop ended")

    def poll_once(self) -> int:
        """
        Poll mailbox once and process all messages.

        Returns:
            Number of messages successfully processed

        Raises:
            IMAPConnectionError: Connection failed
            IMAPAuthenticationError: Authentication failed
        """
        processed = 0
        imap = None

        try:
            imap = self._connect()
            logger.debug("Connected to IMAP %s:%d", self._server, self._port)

            # Select folder
            status, data = imap.select(self._folder)
            if status != "OK":
                logger.error("Failed to select IMAP folder %s: %s",
                             self._folder, data)
                return 0

            # Search for all messages
            status, data = imap.search(None, "ALL")
            if status != "OK":
                logger.error("IMAP search failed: %s", data)
                return 0

            message_ids = data[0].split()
            if not message_ids:
                logger.debug("No messages in IMAP folder %s", self._folder)
                return 0

            logger.debug("Found %d message(s) in %s", len(message_ids), self._folder)

            # Process in reverse order to avoid message number shifting
            # when messages are deleted/moved during processing
            for msg_id in reversed(message_ids):
                try:
                    success = self._process_message(imap, msg_id)
                    if success:
                        processed += 1
                        self._handle_processed(imap, msg_id)
                    else:
                        self._handle_failed(imap, msg_id)
                except Exception as e:
                    logger.error("Error processing message %s: %s", msg_id, e)
                    self._handle_failed(imap, msg_id)

        except imaplib.IMAP4.error as e:
            logger.error("IMAP error: %s", e)
            raise IMAPPollerError(f"IMAP error: {e}")

        finally:
            if imap:
                try:
                    imap.close()
                    imap.logout()
                except Exception:
                    pass

        return processed

    def _connect(self) -> imaplib.IMAP4:
        """
        Connect to IMAP server with appropriate security.

        Returns:
            Connected IMAP4 object

        Raises:
            IMAPConnectionError: Connection failed
            IMAPAuthenticationError: Login failed
        """
        try:
            if self._security == "ssl":
                # Direct SSL connection (port 993)
                context = ssl.create_default_context()
                imap = imaplib.IMAP4_SSL(
                    self._server,
                    self._port,
                    ssl_context=context
                )
            elif self._security == "starttls":
                # Plain connection with STARTTLS upgrade (port 143)
                imap = imaplib.IMAP4(self._server, self._port)
                context = ssl.create_default_context()
                imap.starttls(ssl_context=context)
            else:
                # Plain connection (insecure, not recommended)
                logger.warning("Using insecure plain IMAP connection")
                imap = imaplib.IMAP4(self._server, self._port)

        except Exception as e:
            raise IMAPConnectionError(
                f"Failed to connect to {self._server}:{self._port}: {e}"
            )

        # Authenticate
        try:
            imap.login(self._username, self._password)
        except imaplib.IMAP4.error as e:
            raise IMAPAuthenticationError(
                f"IMAP authentication failed for {self._username}: {e}"
            )

        return imap

    def _process_message(self, imap: imaplib.IMAP4, msg_id: bytes) -> bool:
        """
        Fetch and process a single message.

        Args:
            imap: Connected IMAP object
            msg_id: Message UID to process

        Returns:
            True if message was successfully processed as iTIP
        """
        # Fetch message
        status, data = imap.fetch(msg_id, "(RFC822)")
        if status != "OK":
            logger.warning("Failed to fetch message %s", msg_id)
            return False

        # Parse raw email
        raw_email = data[0][1]
        if isinstance(raw_email, bytes):
            raw_email = raw_email.decode("utf-8", errors="replace")

        parsed = email_parser.parse_mime_email(raw_email)
        if not parsed:
            logger.debug("Message %s: Failed to parse as email", msg_id)
            return False

        if not parsed.itip_content:
            logger.debug("Message %s: No iTIP content found", msg_id)
            return False

        logger.debug(
            "Message %s: iTIP %s from %s",
            msg_id, parsed.itip_method, parsed.sender_email
        )

        # Route to appropriate processor
        processor = self._get_processor()
        method = (parsed.itip_method or "").upper()

        if method == "REPLY":
            return processor.process_reply_external(
                parsed.itip_content,
                parsed.sender_email
            )
        elif method == "COUNTER":
            return processor.process_counter_external(
                parsed.itip_content,
                parsed.sender_email
            )
        else:
            logger.info(
                "Message %s: Unsupported iTIP method %s from %s",
                msg_id, method, parsed.sender_email
            )
            return False

    def _handle_processed(self, imap: imaplib.IMAP4, msg_id: bytes) -> None:
        """
        Handle successfully processed message.

        Either moves to processed folder or deletes based on configuration.

        Args:
            imap: Connected IMAP object
            msg_id: Message UID
        """
        if self._processed_folder:
            self._move_message(imap, msg_id, self._processed_folder)
        else:
            self._delete_message(imap, msg_id)

    def _handle_failed(self, imap: imaplib.IMAP4, msg_id: bytes) -> None:
        """
        Handle failed message.

        Moves to failed folder if configured, otherwise leaves in inbox.

        Args:
            imap: Connected IMAP object
            msg_id: Message UID
        """
        if self._failed_folder:
            self._move_message(imap, msg_id, self._failed_folder)
        # Otherwise leave in inbox for manual review

    def _move_message(self, imap: imaplib.IMAP4, msg_id: bytes,
                      folder: str) -> bool:
        """
        Move message to another folder.

        Args:
            imap: Connected IMAP object
            msg_id: Message UID
            folder: Destination folder name

        Returns:
            True if moved successfully
        """
        try:
            # Copy to destination folder
            status, _ = imap.copy(msg_id, folder)
            if status != "OK":
                # Try creating folder if it doesn't exist
                imap.create(folder)
                status, _ = imap.copy(msg_id, folder)
                if status != "OK":
                    logger.warning("Failed to copy message %s to %s", msg_id, folder)
                    return False

            # Mark original for deletion
            imap.store(msg_id, "+FLAGS", "\\Deleted")
            imap.expunge()

            logger.debug("Moved message %s to %s", msg_id, folder)
            return True

        except Exception as e:
            logger.warning("Failed to move message %s to %s: %s", msg_id, folder, e)
            return False

    def _delete_message(self, imap: imaplib.IMAP4, msg_id: bytes) -> bool:
        """
        Delete a message.

        Args:
            imap: Connected IMAP object
            msg_id: Message UID

        Returns:
            True if deleted successfully
        """
        try:
            imap.store(msg_id, "+FLAGS", "\\Deleted")
            imap.expunge()
            logger.debug("Deleted message %s", msg_id)
            return True
        except Exception as e:
            logger.warning("Failed to delete message %s: %s", msg_id, e)
            return False


def get_imap_poller(configuration: "config.Configuration",
                    storage: "storage.BaseStorage") -> Optional[IMAPPoller]:
    """
    Create IMAP poller if enabled in configuration.

    Args:
        configuration: Radicale configuration
        storage: Storage backend

    Returns:
        IMAPPoller instance or None if disabled
    """
    if not configuration.get("scheduling", "imap_enabled"):
        return None
    return IMAPPoller(configuration, storage)

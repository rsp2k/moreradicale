# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025 Ryan Malloy
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
Tests for IMAP polling for inbound iTIP processing.

Uses mock IMAP server to test the polling logic without requiring
a real mail server.
"""

import email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest

from radicale.itip.imap_poller import (
    IMAPPoller,
    IMAPConnectionError,
    IMAPAuthenticationError,
    get_imap_poller,
)
from radicale.tests import BaseTest


# Sample iTIP REPLY message
SAMPLE_REPLY = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
METHOD:REPLY
BEGIN:VEVENT
UID:test-event-123@example.com
DTSTAMP:20250115T100000Z
DTSTART:20250115T140000Z
DTEND:20250115T150000Z
SUMMARY:Test Meeting
ORGANIZER:mailto:organizer@internal.local
ATTENDEE;PARTSTAT=ACCEPTED:mailto:attendee@external.com
END:VEVENT
END:VCALENDAR"""

SAMPLE_COUNTER = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
METHOD:COUNTER
BEGIN:VEVENT
UID:test-event-456@example.com
DTSTAMP:20250115T100000Z
DTSTART:20250115T160000Z
DTEND:20250115T170000Z
SUMMARY:Test Meeting - Counter Proposal
ORGANIZER:mailto:organizer@internal.local
ATTENDEE;PARTSTAT=TENTATIVE:mailto:attendee@external.com
END:VEVENT
END:VCALENDAR"""


def create_mime_email(from_addr: str, to_addr: str, subject: str,
                      itip_content: str, method: str = "REPLY") -> bytes:
    """Create a MIME email with iTIP calendar content."""
    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject

    # Add text part
    text_part = MIMEText("Please see the attached calendar response.", 'plain')
    msg.attach(text_part)

    # Add calendar part
    cal_part = MIMEText(itip_content, 'calendar')
    cal_part.set_param('method', method)
    msg.attach(cal_part)

    return msg.as_bytes()


class MockIMAPConfig:
    """Configuration for mock tests."""

    def __init__(self, **kwargs):
        self._config = {
            ("scheduling", "imap_enabled"): True,
            ("scheduling", "imap_server"): "mail.example.com",
            ("scheduling", "imap_port"): 993,
            ("scheduling", "imap_security"): "ssl",
            ("scheduling", "imap_username"): "testuser",
            ("scheduling", "imap_password"): "testpass",
            ("scheduling", "imap_folder"): "INBOX",
            ("scheduling", "imap_poll_interval"): 60,
            ("scheduling", "imap_processed_folder"): "Processed",
            ("scheduling", "imap_failed_folder"): "Failed",
        }
        for key, value in kwargs.items():
            section, option = key.split("_", 1)
            self._config[(section, option)] = value

    def get(self, section: str, option: str):
        return self._config.get((section, option), "")


class TestIMAPPollerConfiguration:
    """Test IMAP poller configuration handling."""

    def test_get_imap_poller_disabled(self):
        """get_imap_poller returns None when disabled."""
        config = MockIMAPConfig(scheduling_imap_enabled=False)
        storage = MagicMock()

        result = get_imap_poller(config, storage)

        assert result is None

    def test_get_imap_poller_enabled(self):
        """get_imap_poller returns IMAPPoller when enabled."""
        config = MockIMAPConfig()
        storage = MagicMock()

        result = get_imap_poller(config, storage)

        assert result is not None
        assert isinstance(result, IMAPPoller)


class TestIMAPPollerConnection:
    """Test IMAP connection handling."""

    @patch('imaplib.IMAP4_SSL')
    def test_connect_ssl(self, mock_imap_class):
        """Test SSL connection to IMAP server."""
        mock_imap = MagicMock()
        mock_imap_class.return_value = mock_imap

        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        result = poller._connect()

        assert result == mock_imap
        mock_imap.login.assert_called_once_with("testuser", "testpass")

    @patch('imaplib.IMAP4')
    def test_connect_starttls(self, mock_imap_class):
        """Test STARTTLS connection to IMAP server."""
        mock_imap = MagicMock()
        mock_imap_class.return_value = mock_imap

        config = MockIMAPConfig(scheduling_imap_security="starttls")
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        result = poller._connect()

        assert result == mock_imap
        mock_imap.starttls.assert_called_once()
        mock_imap.login.assert_called_once_with("testuser", "testpass")

    @patch('imaplib.IMAP4_SSL')
    def test_connect_authentication_failure(self, mock_imap_class):
        """Test authentication failure raises IMAPAuthenticationError."""
        import imaplib
        mock_imap = MagicMock()
        mock_imap.login.side_effect = imaplib.IMAP4.error("Auth failed")
        mock_imap_class.return_value = mock_imap

        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        with pytest.raises(IMAPAuthenticationError):
            poller._connect()


class TestIMAPPollerMessageProcessing:
    """Test IMAP message processing."""

    def test_process_reply_message(self):
        """Test processing REPLY iTIP message."""
        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        # Mock the processor
        mock_processor = MagicMock()
        mock_processor.process_reply_external.return_value = True
        poller._processor = mock_processor

        # Create mock IMAP
        mock_imap = MagicMock()
        msg_data = create_mime_email(
            "attendee@external.com",
            "organizer@internal.local",
            "Re: Meeting Invitation",
            SAMPLE_REPLY,
            "REPLY"
        )
        mock_imap.fetch.return_value = ("OK", [(b"1", msg_data)])

        result = poller._process_message(mock_imap, b"1")

        assert result is True
        mock_processor.process_reply_external.assert_called_once()
        # Verify sender email was passed
        call_args = mock_processor.process_reply_external.call_args
        assert "attendee@external.com" in call_args[0][1]

    def test_process_counter_message(self):
        """Test processing COUNTER iTIP message."""
        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        # Mock the processor
        mock_processor = MagicMock()
        mock_processor.process_counter_external.return_value = True
        poller._processor = mock_processor

        # Create mock IMAP
        mock_imap = MagicMock()
        msg_data = create_mime_email(
            "attendee@external.com",
            "organizer@internal.local",
            "Counter Proposal: Meeting",
            SAMPLE_COUNTER,
            "COUNTER"
        )
        mock_imap.fetch.return_value = ("OK", [(b"1", msg_data)])

        result = poller._process_message(mock_imap, b"1")

        assert result is True
        mock_processor.process_counter_external.assert_called_once()

    def test_process_non_itip_message(self):
        """Test processing non-iTIP message returns False."""
        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        # Create plain text email (no iTIP)
        msg = MIMEText("Just a regular email.")
        msg['From'] = "someone@example.com"
        msg['To'] = "organizer@internal.local"
        msg['Subject'] = "Hello"

        mock_imap = MagicMock()
        mock_imap.fetch.return_value = ("OK", [(b"1", msg.as_bytes())])

        result = poller._process_message(mock_imap, b"1")

        assert result is False


class TestIMAPPollerMessageHandling:
    """Test message move/delete operations."""

    def test_handle_processed_moves_to_folder(self):
        """Test processed messages are moved to configured folder."""
        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        mock_imap = MagicMock()
        mock_imap.copy.return_value = ("OK", [])

        poller._handle_processed(mock_imap, b"1")

        # Should copy to Processed folder
        mock_imap.copy.assert_called_with(b"1", "Processed")
        # Should mark for deletion
        mock_imap.store.assert_called_with(b"1", "+FLAGS", "\\Deleted")
        mock_imap.expunge.assert_called_once()

    def test_handle_processed_deletes_if_no_folder(self):
        """Test processed messages are deleted if no folder configured."""
        config = MockIMAPConfig(scheduling_imap_processed_folder="")
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        mock_imap = MagicMock()

        poller._handle_processed(mock_imap, b"1")

        # Should NOT copy
        mock_imap.copy.assert_not_called()
        # Should mark for deletion
        mock_imap.store.assert_called_with(b"1", "+FLAGS", "\\Deleted")
        mock_imap.expunge.assert_called_once()

    def test_handle_failed_moves_to_folder(self):
        """Test failed messages are moved to failed folder."""
        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        mock_imap = MagicMock()
        mock_imap.copy.return_value = ("OK", [])

        poller._handle_failed(mock_imap, b"1")

        # Should copy to Failed folder
        mock_imap.copy.assert_called_with(b"1", "Failed")

    def test_handle_failed_leaves_in_inbox_if_no_folder(self):
        """Test failed messages are left in inbox if no folder configured."""
        config = MockIMAPConfig(scheduling_imap_failed_folder="")
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        mock_imap = MagicMock()

        poller._handle_failed(mock_imap, b"1")

        # Should NOT copy or delete
        mock_imap.copy.assert_not_called()
        mock_imap.store.assert_not_called()


class TestIMAPPollerPollOnce:
    """Test the poll_once method."""

    @patch.object(IMAPPoller, '_connect')
    def test_poll_once_empty_inbox(self, mock_connect):
        """Test polling empty inbox returns 0."""
        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b"0"])
        mock_imap.search.return_value = ("OK", [b""])
        mock_connect.return_value = mock_imap

        result = poller.poll_once()

        assert result == 0
        mock_imap.select.assert_called_with("INBOX")

    @patch.object(IMAPPoller, '_connect')
    @patch.object(IMAPPoller, '_process_message')
    @patch.object(IMAPPoller, '_handle_processed')
    def test_poll_once_processes_messages(self, mock_handle, mock_process,
                                          mock_connect):
        """Test polling processes all messages."""
        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b"3"])
        mock_imap.search.return_value = ("OK", [b"1 2 3"])
        mock_connect.return_value = mock_imap
        mock_process.return_value = True

        result = poller.poll_once()

        assert result == 3
        assert mock_process.call_count == 3
        assert mock_handle.call_count == 3


class TestIMAPPollerStartStop:
    """Test background polling start/stop."""

    def test_start_disabled(self):
        """Test start returns False when disabled."""
        config = MockIMAPConfig(scheduling_imap_enabled=False)
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        result = poller.start()

        assert result is False
        assert not poller.is_running

    def test_start_no_server(self):
        """Test start returns False when no server configured."""
        config = MockIMAPConfig(scheduling_imap_server="")
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        result = poller.start()

        assert result is False

    @patch.object(IMAPPoller, 'poll_once')
    def test_start_stop(self, mock_poll):
        """Test start and stop of background polling."""
        config = MockIMAPConfig()
        storage = MagicMock()
        poller = IMAPPoller(config, storage)

        # Make poll_once raise to exit loop quickly
        mock_poll.side_effect = Exception("Test exit")

        result = poller.start()
        assert result is True
        assert poller.is_running

        # Give thread time to start
        import time
        time.sleep(0.1)

        result = poller.stop(timeout=1.0)
        assert result is True
        assert not poller.is_running

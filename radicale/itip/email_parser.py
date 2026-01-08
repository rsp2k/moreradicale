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
Email MIME parsing utilities for inbound iTIP webhook processing.

This module handles parsing of email payloads from various webhook providers
(SendGrid, Mailgun, Postmark, generic) and extracting iTIP calendar data
from MIME messages.

Key responsibilities:
- Parse email headers (From, To, Subject)
- Extract text/calendar parts from multipart MIME
- Normalize different webhook provider payload formats
- Extract sender email for validation
"""

import base64
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from email import message_from_bytes, message_from_string
from email.utils import parseaddr
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ParsedEmail:
    """Parsed email with extracted iTIP data."""
    sender_email: str
    sender_name: str
    recipient_email: str
    subject: str
    itip_content: Optional[str]
    itip_method: Optional[str]
    raw_mime: Optional[str]


def extract_email_address(email_header: str) -> str:
    """
    Extract email address from header value.

    Examples:
        "Bob Smith <bob@example.com>" -> "bob@example.com"
        "bob@example.com" -> "bob@example.com"
        "<bob@example.com>" -> "bob@example.com"

    Args:
        email_header: Raw email header value

    Returns:
        Lowercase email address
    """
    if not email_header:
        return ""

    # Use email.utils.parseaddr for robust parsing
    name, email = parseaddr(email_header)

    # Fallback: regex extraction if parseaddr fails
    if not email:
        match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', email_header)
        if match:
            email = match.group(0)

    return email.lower().strip() if email else ""


def extract_name_from_email(email_header: str) -> str:
    """
    Extract display name from email header.

    Examples:
        "Bob Smith <bob@example.com>" -> "Bob Smith"
        "bob@example.com" -> ""

    Args:
        email_header: Raw email header value

    Returns:
        Display name or empty string
    """
    if not email_header:
        return ""

    name, _ = parseaddr(email_header)
    return name.strip()


def parse_mime_email(raw_mime: str) -> Optional[ParsedEmail]:
    """
    Parse a raw MIME email message.

    Args:
        raw_mime: Full MIME message as string

    Returns:
        ParsedEmail with extracted data, or None on failure
    """
    try:
        # Handle both string and bytes
        if isinstance(raw_mime, bytes):
            msg = message_from_bytes(raw_mime)
        else:
            msg = message_from_string(raw_mime)

        # Extract headers
        from_header = msg.get('From', '')
        to_header = msg.get('To', '')
        subject = msg.get('Subject', '')

        sender_email = extract_email_address(from_header)
        sender_name = extract_name_from_email(from_header)
        recipient_email = extract_email_address(to_header)

        # Extract iTIP content
        itip_content, itip_method = extract_itip_from_mime(msg)

        return ParsedEmail(
            sender_email=sender_email,
            sender_name=sender_name,
            recipient_email=recipient_email,
            subject=subject,
            itip_content=itip_content,
            itip_method=itip_method,
            raw_mime=raw_mime if isinstance(raw_mime, str) else raw_mime.decode('utf-8', errors='replace')
        )

    except Exception as e:
        logger.error(f"Failed to parse MIME email: {e}")
        return None


def extract_itip_from_mime(mime_message) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract iTIP calendar content from a MIME message.

    Walks through all MIME parts looking for text/calendar or
    application/ics content types.

    Args:
        mime_message: email.message.Message object

    Returns:
        Tuple of (itip_content, method) or (None, None)
    """
    itip_content = None
    itip_method = None

    # Walk all MIME parts
    for part in mime_message.walk():
        content_type = part.get_content_type()

        if content_type in ('text/calendar', 'application/ics'):
            try:
                # Get payload, decode if necessary
                payload = part.get_payload(decode=True)
                if payload:
                    itip_content = payload.decode('utf-8', errors='replace')
                else:
                    # Try without decoding (already string)
                    itip_content = part.get_payload()

                # Extract METHOD from Content-Type params
                method_param = part.get_param('method')
                if method_param:
                    itip_method = method_param.upper()

                # Also try to extract METHOD from calendar content
                if itip_content and not itip_method:
                    method_match = re.search(r'METHOD:(\w+)', itip_content)
                    if method_match:
                        itip_method = method_match.group(1).upper()

                # Found calendar content, stop searching
                break

            except Exception as e:
                logger.warning(f"Failed to decode calendar part: {e}")
                continue

    return itip_content, itip_method


def parse_sendgrid_webhook(payload: dict) -> Optional[ParsedEmail]:
    """
    Parse SendGrid Inbound Parse webhook payload.

    SendGrid can send either:
    1. Full MIME in 'email' field (when "POST raw MIME" enabled)
    2. Parsed fields with attachments

    See: https://docs.sendgrid.com/for-developers/parsing-email/setting-up-the-inbound-parse-webhook

    Args:
        payload: JSON payload from SendGrid webhook

    Returns:
        ParsedEmail or None
    """
    try:
        # Option 1: Raw MIME message
        if 'email' in payload:
            return parse_mime_email(payload['email'])

        # Option 2: Parsed fields
        sender_email = extract_email_address(payload.get('from', ''))
        sender_name = extract_name_from_email(payload.get('from', ''))
        recipient_email = extract_email_address(payload.get('to', ''))
        subject = payload.get('subject', '')

        # Look for calendar in attachments
        itip_content = None
        itip_method = None

        # SendGrid sends attachments as attachment1, attachment2, etc.
        attachment_info = payload.get('attachment-info', '{}')
        if isinstance(attachment_info, str):
            attachment_info = json.loads(attachment_info)

        # Also check for numbered attachments directly
        for i in range(1, 10):
            att_key = f'attachment{i}'
            if att_key in payload:
                att_content = payload[att_key]

                # Check if it's calendar content
                if 'BEGIN:VCALENDAR' in att_content:
                    itip_content = att_content

                    # Extract METHOD
                    method_match = re.search(r'METHOD:(\w+)', att_content)
                    if method_match:
                        itip_method = method_match.group(1).upper()
                    break

        # Also check 'attachments' array format
        attachments = payload.get('attachments', [])
        if isinstance(attachments, str):
            attachments = json.loads(attachments)

        for att in attachments:
            if isinstance(att, dict):
                content_type = att.get('type', att.get('content-type', ''))
                if 'calendar' in content_type or 'ics' in content_type:
                    content = att.get('content', att.get('data', ''))
                    # Decode base64 if needed
                    if att.get('encoding') == 'base64':
                        content = base64.b64decode(content).decode('utf-8')
                    itip_content = content

                    method_match = re.search(r'METHOD:(\w+)', content)
                    if method_match:
                        itip_method = method_match.group(1).upper()
                    break

        return ParsedEmail(
            sender_email=sender_email,
            sender_name=sender_name,
            recipient_email=recipient_email,
            subject=subject,
            itip_content=itip_content,
            itip_method=itip_method,
            raw_mime=None
        )

    except Exception as e:
        logger.error(f"Failed to parse SendGrid webhook: {e}")
        return None


def parse_mailgun_webhook(payload: dict) -> Optional[ParsedEmail]:
    """
    Parse Mailgun webhook payload.

    Mailgun sends multipart/form-data with:
    - sender: email address
    - recipient: email address
    - subject: email subject
    - body-mime: full MIME message
    - attachments: file uploads

    See: https://documentation.mailgun.com/docs/mailgun/user-manual/receive-forward-store/

    Args:
        payload: Parsed form data from Mailgun webhook

    Returns:
        ParsedEmail or None
    """
    try:
        # Option 1: Full MIME in body-mime
        if 'body-mime' in payload:
            return parse_mime_email(payload['body-mime'])

        # Option 2: Parsed fields
        sender_email = extract_email_address(payload.get('sender', payload.get('from', '')))
        sender_name = extract_name_from_email(payload.get('from', ''))
        recipient_email = extract_email_address(payload.get('recipient', payload.get('to', '')))
        subject = payload.get('subject', '')

        # Look for calendar in attachments
        itip_content = None
        itip_method = None

        # Mailgun can include attachment content directly
        for key, value in payload.items():
            if key.startswith('attachment-') and isinstance(value, str):
                if 'BEGIN:VCALENDAR' in value:
                    itip_content = value
                    method_match = re.search(r'METHOD:(\w+)', value)
                    if method_match:
                        itip_method = method_match.group(1).upper()
                    break

        return ParsedEmail(
            sender_email=sender_email,
            sender_name=sender_name,
            recipient_email=recipient_email,
            subject=subject,
            itip_content=itip_content,
            itip_method=itip_method,
            raw_mime=None
        )

    except Exception as e:
        logger.error(f"Failed to parse Mailgun webhook: {e}")
        return None


def parse_postmark_webhook(payload: dict) -> Optional[ParsedEmail]:
    """
    Parse Postmark inbound webhook payload.

    Postmark sends JSON with:
    - From, FromName, FromFull
    - To, ToFull
    - Subject
    - Attachments: array of {Name, Content, ContentType, ContentID}

    See: https://postmarkapp.com/developer/webhooks/inbound-webhook

    Args:
        payload: JSON payload from Postmark webhook

    Returns:
        ParsedEmail or None
    """
    try:
        # Raw MIME option
        if 'RawEmail' in payload:
            return parse_mime_email(payload['RawEmail'])

        sender_email = extract_email_address(payload.get('From', ''))
        sender_name = payload.get('FromName', '')

        # To can be string or array
        to_field = payload.get('To', '')
        if isinstance(to_field, list) and to_field:
            to_field = to_field[0].get('Email', '') if isinstance(to_field[0], dict) else to_field[0]
        recipient_email = extract_email_address(to_field)

        subject = payload.get('Subject', '')

        # Look for calendar attachments
        itip_content = None
        itip_method = None

        attachments = payload.get('Attachments', [])
        for att in attachments:
            content_type = att.get('ContentType', '')
            if 'calendar' in content_type or att.get('Name', '').endswith('.ics'):
                content = att.get('Content', '')
                # Postmark sends base64 encoded
                if content:
                    try:
                        content = base64.b64decode(content).decode('utf-8')
                    except:
                        pass  # Maybe not base64

                if 'BEGIN:VCALENDAR' in content:
                    itip_content = content
                    method_match = re.search(r'METHOD:(\w+)', content)
                    if method_match:
                        itip_method = method_match.group(1).upper()
                    break

        return ParsedEmail(
            sender_email=sender_email,
            sender_name=sender_name,
            recipient_email=recipient_email,
            subject=subject,
            itip_content=itip_content,
            itip_method=itip_method,
            raw_mime=None
        )

    except Exception as e:
        logger.error(f"Failed to parse Postmark webhook: {e}")
        return None


def parse_generic_webhook(payload: dict) -> Optional[ParsedEmail]:
    """
    Parse generic webhook payload.

    Tries to be flexible with field names:
    - from/sender/from_email for sender
    - to/recipient/to_email for recipient
    - subject for subject
    - email/mime/raw/body for MIME content
    - attachment/calendar/ics for calendar data

    Args:
        payload: Generic JSON/form payload

    Returns:
        ParsedEmail or None
    """
    try:
        # Try to find MIME content first
        for mime_key in ('email', 'mime', 'raw', 'body', 'message', 'raw_email', 'body-mime'):
            if mime_key in payload and payload[mime_key]:
                content = payload[mime_key]
                if 'BEGIN:' in content or 'Content-Type:' in content:
                    return parse_mime_email(content)

        # Parse individual fields
        sender_email = ""
        for key in ('from', 'sender', 'from_email', 'sender_email', 'envelope_from'):
            if key in payload:
                sender_email = extract_email_address(str(payload[key]))
                if sender_email:
                    break

        sender_name = ""
        for key in ('from', 'from_name', 'sender_name'):
            if key in payload:
                sender_name = extract_name_from_email(str(payload[key]))
                if sender_name:
                    break

        recipient_email = ""
        for key in ('to', 'recipient', 'to_email', 'recipient_email', 'envelope_to'):
            if key in payload:
                recipient_email = extract_email_address(str(payload[key]))
                if recipient_email:
                    break

        subject = payload.get('subject', '')

        # Look for calendar content
        itip_content = None
        itip_method = None

        for key in ('calendar', 'ics', 'icalendar', 'attachment', 'itip'):
            if key in payload and payload[key]:
                content = str(payload[key])
                if 'BEGIN:VCALENDAR' in content:
                    itip_content = content
                    method_match = re.search(r'METHOD:(\w+)', content)
                    if method_match:
                        itip_method = method_match.group(1).upper()
                    break

        return ParsedEmail(
            sender_email=sender_email,
            sender_name=sender_name,
            recipient_email=recipient_email,
            subject=subject,
            itip_content=itip_content,
            itip_method=itip_method,
            raw_mime=None
        )

    except Exception as e:
        logger.error(f"Failed to parse generic webhook: {e}")
        return None


def verify_hmac_signature(payload: bytes, signature: str, secret: str,
                          algorithm: str = 'sha256') -> bool:
    """
    Verify HMAC signature for webhook authentication.

    Args:
        payload: Raw request body bytes
        signature: Signature from header (may include algorithm prefix)
        secret: Shared secret key
        algorithm: Hash algorithm (sha256, sha1, sha512)

    Returns:
        True if signature is valid
    """
    if not signature or not secret:
        return False

    try:
        # Handle signatures with algorithm prefix (sha256=abc123)
        if '=' in signature:
            parts = signature.split('=', 1)
            if len(parts) == 2:
                algorithm = parts[0].lower()
                signature = parts[1]

        # Select hash function
        if algorithm == 'sha1':
            hash_func = hashlib.sha1
        elif algorithm == 'sha512':
            hash_func = hashlib.sha512
        else:
            hash_func = hashlib.sha256

        # Compute expected signature
        expected = hmac.new(
            secret.encode('utf-8'),
            payload,
            hash_func
        ).hexdigest()

        # Constant-time comparison
        return hmac.compare_digest(expected.lower(), signature.lower())

    except Exception as e:
        logger.error(f"HMAC verification failed: {e}")
        return False


def verify_sendgrid_signature(payload: bytes, signature: str, timestamp: str,
                              secret: str) -> bool:
    """
    Verify SendGrid Event Webhook signature.

    SendGrid uses ECDSA signatures, but for Inbound Parse we use
    a simpler approach with the verification key.

    Args:
        payload: Raw request body
        signature: X-Twilio-Email-Event-Webhook-Signature header
        timestamp: X-Twilio-Email-Event-Webhook-Timestamp header
        secret: SendGrid verification key

    Returns:
        True if signature is valid
    """
    # For basic inbound parse, fall back to generic HMAC
    # Full ECDSA implementation would require cryptography library
    return verify_hmac_signature(payload, signature, secret)


def verify_mailgun_signature(token: str, timestamp: str, signature: str,
                             secret: str) -> bool:
    """
    Verify Mailgun webhook signature.

    Mailgun signs: timestamp + token
    Uses HMAC-SHA256.

    Args:
        token: Random token from Mailgun
        timestamp: Unix timestamp
        signature: Expected signature
        secret: Mailgun API key

    Returns:
        True if signature is valid
    """
    try:
        # Construct signed payload
        signed_data = f"{timestamp}{token}"

        expected = hmac.new(
            secret.encode('utf-8'),
            signed_data.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected.lower(), signature.lower())

    except Exception as e:
        logger.error(f"Mailgun signature verification failed: {e}")
        return False

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
Webhook handler for inbound iTIP email processing.

This module handles HTTP POST requests from email webhook providers
(SendGrid, Mailgun, Postmark, or generic) to process iTIP responses
from external attendees.

Key features:
- Multi-layer authentication (IP whitelist, HMAC signatures)
- Support for multiple email provider formats
- RFC 6047 MIME parsing
- Security validation (sender matches ATTENDEE, organizer is internal)

Webhook Flow:
    Email Provider -> POST /scheduling/webhook
        -> Authenticate (IP + HMAC)
        -> Parse payload (provider-specific)
        -> Extract iTIP from MIME
        -> Route to ITIPProcessor
        -> Return 200 OK
"""

import ipaddress
import json
import socket
from http import client
from typing import Optional, Tuple

from moreradicale import types
from moreradicale.itip import email_parser
from moreradicale.log import logger

# Standard HTTP responses for webhook
WEBHOOK_OK: types.WSGIResponse = (
    client.OK, (("Content-Type", "text/plain"),), "OK", None
)
WEBHOOK_FORBIDDEN: types.WSGIResponse = (
    client.FORBIDDEN, (("Content-Type", "text/plain"),), "Forbidden", None
)


class WebhookHandler:
    """
    Stateless webhook handler for inbound iTIP email processing.

    This handler is called from Application._handle_request() before
    the standard authentication flow, allowing webhooks to use their
    own authentication mechanism.
    """

    def __init__(self, configuration, storage):
        """
        Initialize webhook handler.

        Args:
            configuration: Radicale configuration
            storage: Storage backend
        """
        self.configuration = configuration
        self._storage = storage

        # Load webhook configuration
        self._enabled = configuration.get("scheduling", "webhook_enabled")
        self._path = configuration.get("scheduling", "webhook_path")
        self._secret = configuration.get("scheduling", "webhook_secret")
        self._allowed_ips = configuration.get("scheduling", "webhook_allowed_ips")
        self._provider = configuration.get("scheduling", "webhook_provider")
        self._max_size = configuration.get("scheduling", "webhook_max_size")

        # Parse IP whitelist
        self._ip_networks = []
        if self._allowed_ips:
            for ip_str in self._allowed_ips.split(','):
                ip_str = ip_str.strip()
                if ip_str:
                    try:
                        self._ip_networks.append(ipaddress.ip_network(ip_str, strict=False))
                    except ValueError as e:
                        logger.warning(f"Invalid webhook IP whitelist entry: {ip_str}: {e}")

    def should_handle(self, path: str, method: str) -> bool:
        """
        Check if this request should be handled by the webhook handler.

        Args:
            path: Request path
            method: HTTP method

        Returns:
            True if webhook should handle this request
        """
        if not self._enabled:
            return False

        if method != "POST":
            return False

        # Match exact path or path with trailing content
        return path == self._path or path.startswith(self._path + "/")

    def handle_request(self, environ: types.WSGIEnviron,
                       base_prefix: str, path: str) -> types.WSGIResponse:
        """
        Handle inbound webhook request.

        Args:
            environ: WSGI environment
            base_prefix: URL base prefix
            path: Request path

        Returns:
            WSGI response tuple
        """
        # Step 1: Authenticate the webhook request
        auth_result, auth_error = self._authenticate(environ)
        if not auth_result:
            logger.warning(f"Webhook authentication failed: {auth_error}")
            # Return 200 OK to prevent retry loops, but log the failure
            # Security: Don't leak auth failure details to potential attackers
            return WEBHOOK_OK

        # Step 2: Read and validate request body
        try:
            body = self._read_body(environ)
        except ValueError as e:
            logger.warning(f"Failed to read webhook body: {e}")
            return WEBHOOK_OK

        # Step 3: Parse payload based on provider
        parsed = self._parse_payload(environ, body)
        if not parsed:
            logger.warning("Failed to parse webhook payload")
            return WEBHOOK_OK

        # Step 4: Validate iTIP content
        if not parsed.itip_content:
            logger.info("Webhook payload contains no iTIP calendar data")
            return WEBHOOK_OK

        # Step 5: Process the iTIP message
        return self._process_itip(parsed, base_prefix)

    def _authenticate(self, environ: types.WSGIEnviron) -> Tuple[bool, str]:
        """
        Authenticate webhook request with multiple layers.

        Layer 1: IP whitelist (if configured)
        Layer 2: HMAC signature (if configured)

        Args:
            environ: WSGI environment

        Returns:
            Tuple of (success, error_message)
        """
        remote_addr = environ.get("REMOTE_ADDR", "")

        # Layer 1: IP Whitelist
        if self._ip_networks:
            try:
                ip = ipaddress.ip_address(remote_addr)
                allowed = any(ip in network for network in self._ip_networks)
                if not allowed:
                    return False, f"IP {remote_addr} not in whitelist"
            except ValueError:
                return False, f"Invalid remote address: {remote_addr}"

        # Layer 2: HMAC Signature
        if self._secret:
            if not self._verify_signature(environ):
                return False, "Invalid webhook signature"

        return True, ""

    def _verify_signature(self, environ: types.WSGIEnviron) -> bool:
        """
        Verify webhook signature based on provider.

        Args:
            environ: WSGI environment with headers

        Returns:
            True if signature is valid
        """
        # Read raw body for signature verification
        try:
            from io import BytesIO
            content_length = int(environ.get("CONTENT_LENGTH", 0))
            body = environ["wsgi.input"].read(content_length)
            # Rewind for later reading using BytesIO
            environ["wsgi.input"] = BytesIO(body)
        except Exception:
            return False

        if self._provider == "sendgrid":
            # SendGrid: X-Twilio-Email-Event-Webhook-Signature
            signature = environ.get("HTTP_X_TWILIO_EMAIL_EVENT_WEBHOOK_SIGNATURE", "")
            timestamp = environ.get("HTTP_X_TWILIO_EMAIL_EVENT_WEBHOOK_TIMESTAMP", "")
            return email_parser.verify_sendgrid_signature(body, signature, timestamp, self._secret)

        elif self._provider == "mailgun":
            # Mailgun: signature fields in body
            try:
                data = json.loads(body)
                signature_data = data.get("signature", {})
                return email_parser.verify_mailgun_signature(
                    signature_data.get("token", ""),
                    signature_data.get("timestamp", ""),
                    signature_data.get("signature", ""),
                    self._secret
                )
            except Exception:
                return False

        else:
            # Generic: X-Webhook-Signature header
            signature = environ.get("HTTP_X_WEBHOOK_SIGNATURE", "")
            return email_parser.verify_hmac_signature(body, signature, self._secret)

    def _read_body(self, environ: types.WSGIEnviron) -> bytes:
        """
        Read request body with size limit.

        Args:
            environ: WSGI environment

        Returns:
            Request body as bytes

        Raises:
            ValueError: If body too large or read error
        """
        try:
            content_length = int(environ.get("CONTENT_LENGTH", 0))
        except ValueError:
            content_length = 0

        if content_length > self._max_size:
            raise ValueError(f"Request body too large: {content_length} > {self._max_size}")

        try:
            # Check if we already read the body during signature verification
            wsgi_input = environ["wsgi.input"]
            if hasattr(wsgi_input, 'getvalue'):
                return wsgi_input.getvalue()
            return wsgi_input.read(content_length)
        except socket.timeout:
            raise ValueError("Request body read timeout")

    def _parse_payload(self, environ: types.WSGIEnviron,
                       body: bytes) -> Optional[email_parser.ParsedEmail]:
        """
        Parse webhook payload based on provider.

        Args:
            environ: WSGI environment
            body: Raw request body

        Returns:
            ParsedEmail or None
        """
        content_type = environ.get("CONTENT_TYPE", "")

        try:
            # Decode body
            if isinstance(body, bytes):
                body_str = body.decode("utf-8", errors="replace")
            else:
                body_str = body

            # Determine format and parse
            if self._provider == "sendgrid":
                # SendGrid sends JSON
                if "application/json" in content_type:
                    payload = json.loads(body_str)
                    return email_parser.parse_sendgrid_webhook(payload)
                # Or form-data
                else:
                    payload = self._parse_form_data(environ, body)
                    return email_parser.parse_sendgrid_webhook(payload)

            elif self._provider == "mailgun":
                # Mailgun sends multipart/form-data
                if "multipart/form-data" in content_type:
                    payload = self._parse_form_data(environ, body)
                else:
                    payload = json.loads(body_str)
                return email_parser.parse_mailgun_webhook(payload)

            elif self._provider == "postmark":
                payload = json.loads(body_str)
                return email_parser.parse_postmark_webhook(payload)

            else:
                # Generic: try JSON first, then form data
                if "application/json" in content_type:
                    payload = json.loads(body_str)
                elif "multipart/form-data" in content_type:
                    payload = self._parse_form_data(environ, body)
                else:
                    # Try to detect MIME directly
                    if body_str.strip().startswith(("From:", "MIME-Version:", "Content-Type:")):
                        return email_parser.parse_mime_email(body_str)
                    payload = json.loads(body_str)

                return email_parser.parse_generic_webhook(payload)

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse webhook JSON: {e}")
            return None
        except Exception as e:
            logger.warning(f"Failed to parse webhook payload: {e}")
            return None

    def _parse_form_data(self, environ: types.WSGIEnviron, body: bytes) -> dict:
        """
        Parse multipart/form-data body.

        Args:
            environ: WSGI environment
            body: Raw body bytes

        Returns:
            Dict of form fields
        """
        import cgi
        from io import BytesIO

        # Create a file-like object for cgi.FieldStorage
        if isinstance(body, bytes):
            fp = BytesIO(body)
        else:
            fp = BytesIO(body.encode())

        # Parse using cgi.FieldStorage
        environ_copy = dict(environ)
        environ_copy["QUERY_STRING"] = ""

        try:
            form = cgi.FieldStorage(
                fp=fp,
                environ=environ_copy,
                keep_blank_values=True
            )

            result = {}
            for key in form.keys():
                item = form[key]
                if hasattr(item, 'file'):
                    # File field
                    result[key] = item.file.read().decode('utf-8', errors='replace')
                elif hasattr(item, 'value'):
                    result[key] = item.value
                else:
                    result[key] = str(item)

            return result

        except Exception as e:
            logger.warning(f"Failed to parse form data: {e}")
            return {}

    def _process_itip(self, parsed: email_parser.ParsedEmail,
                      base_prefix: str) -> types.WSGIResponse:
        """
        Process the extracted iTIP message.

        Args:
            parsed: Parsed email with iTIP content
            base_prefix: URL base prefix

        Returns:
            WSGI response
        """
        from moreradicale.itip import processor

        try:
            # Create processor
            itip_processor = processor.ITIPProcessor(self._storage, self.configuration)

            # Route based on iTIP METHOD
            method = parsed.itip_method

            if method == "REPLY":
                success = itip_processor.process_reply_external(
                    parsed.itip_content,
                    parsed.sender_email,
                    base_prefix
                )
                if success:
                    logger.info(f"Processed external REPLY from {parsed.sender_email}")
                else:
                    logger.info(f"External REPLY from {parsed.sender_email} not processed")

            elif method == "COUNTER":
                success = itip_processor.process_counter_external(
                    parsed.itip_content,
                    parsed.sender_email,
                    base_prefix
                )
                if success:
                    logger.info(f"Processed external COUNTER from {parsed.sender_email}")
                else:
                    logger.info(f"External COUNTER from {parsed.sender_email} not processed")

            else:
                # We don't process external REQUEST, CANCEL, etc.
                # Those should come from internal organizers only
                logger.info(f"Ignoring external {method} from {parsed.sender_email}")

            # Always return OK to prevent webhook retries
            return WEBHOOK_OK

        except Exception as e:
            logger.error(f"Failed to process external iTIP: {e}", exc_info=True)
            # Return OK to prevent retry loops
            return WEBHOOK_OK

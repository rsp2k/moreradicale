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
Shared email utilities for iTIP messaging and notification delivery.

This module provides RFC 6047 compliant email building and SMTP delivery
functionality that can be used by both the email hook and iTIP processor.
"""

import enum
import base64
import mimetypes
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from email import encoders
from typing import Optional, List

from moreradicale.log import logger


@dataclass
class Attachment:
    """Represents a file attachment for email delivery."""
    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"
    is_inline: bool = True  # True = embedded in event, False = URI reference


class SMTPSecurityType(enum.Enum):
    """SMTP connection security types."""
    NONE = "none"
    STARTTLS = "starttls"
    TLS = "tls"

    @classmethod
    def from_string(cls, value: str) -> 'SMTPSecurityType':
        """Convert string to enum value."""
        for member in cls:
            if member.value == value.lower():
                return member
        raise ValueError(f"Invalid security type: {value}")


class SMTPSSLVerifyMode(enum.Enum):
    """SSL certificate verification modes."""
    NONE = "NONE"
    OPTIONAL = "OPTIONAL"
    REQUIRED = "REQUIRED"

    @classmethod
    def from_string(cls, value: str) -> 'SMTPSSLVerifyMode':
        """Convert string to enum value."""
        for member in cls:
            if member.value == value.upper():
                return member
        raise ValueError(f"Invalid SSL verify mode: {value}")


@dataclass
class EmailConfig:
    """
    SMTP configuration for email delivery.

    This simplified configuration is used by the iTIP processor.
    It loads from the [hook] section to reuse existing SMTP settings.
    """
    host: str
    port: int
    security: SMTPSecurityType
    ssl_verify_mode: SMTPSSLVerifyMode
    username: str
    password: str
    from_email: str
    dryrun: bool = False

    def __str__(self) -> str:
        return f"EmailConfig(host={self.host}, port={self.port}, from={self.from_email})"


def load_email_config_from_radicale_config(configuration) -> EmailConfig:
    """
    Load email configuration from Radicale configuration.

    Reads from the [hook] section to reuse existing SMTP settings
    configured for the email notification hook.

    Args:
        configuration: Radicale configuration object

    Returns:
        EmailConfig object with SMTP settings

    Raises:
        ValueError: If required configuration is missing or invalid
    """
    host = configuration.get("hook", "smtp_server")
    port_str = configuration.get("hook", "smtp_port")
    security_str = configuration.get("hook", "smtp_security") or "none"
    ssl_verify_str = configuration.get("hook", "smtp_ssl_verify_mode") or "REQUIRED"
    username = configuration.get("hook", "smtp_username") or ""
    password = configuration.get("hook", "smtp_password") or ""
    from_email = configuration.get("hook", "from_email")

    # Validate required fields
    if not host:
        raise ValueError("SMTP host not configured in [hook] smtp_server")
    if not port_str:
        raise ValueError("SMTP port not configured in [hook] smtp_port")
    if not from_email:
        raise ValueError("From email not configured in [hook] from_email")

    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(f"Invalid SMTP port: {port_str}")

    security = SMTPSecurityType.from_string(security_str)
    ssl_verify_mode = SMTPSSLVerifyMode.from_string(ssl_verify_str)

    # Check for dry-run mode (useful for testing)
    dryrun = configuration.get("scheduling", "email_dryrun") or False

    return EmailConfig(
        host=host,
        port=port,
        security=security,
        ssl_verify_mode=ssl_verify_mode,
        username=username,
        password=password,
        from_email=from_email,
        dryrun=dryrun
    )


def build_ssl_context(ssl_verify_mode: SMTPSSLVerifyMode) -> ssl.SSLContext:
    """
    Build SSL context based on verification mode.

    Args:
        ssl_verify_mode: Certificate verification mode

    Returns:
        Configured SSL context
    """
    context = ssl.create_default_context()

    if ssl_verify_mode == SMTPSSLVerifyMode.REQUIRED:
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
    elif ssl_verify_mode == SMTPSSLVerifyMode.OPTIONAL:
        context.check_hostname = True
        context.verify_mode = ssl.CERT_OPTIONAL
    else:  # NONE
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    return context


def build_itip_mime_message(
    from_email: str,
    to_email: str,
    subject: str,
    body_text: str,
    icalendar_text: str,
    method: str,
    attachments: Optional[List[Attachment]] = None
) -> MIMEMultipart:
    """
    Build RFC 6047 compliant iTIP email message.

    Creates a multipart/mixed message with:
    1. text/plain part (human-readable description)
    2. text/calendar part (iTIP message with METHOD parameter)
    3. Optional file attachments from the event

    This format allows calendar clients to automatically process
    the invitation while providing fallback text for non-calendar clients.

    Args:
        from_email: Sender email address
        to_email: Recipient email address
        subject: Email subject line
        body_text: Human-readable description
        icalendar_text: iCalendar data (BEGIN:VCALENDAR...END:VCALENDAR)
        method: iTIP method (REQUEST, REPLY, CANCEL, COUNTER, DECLINECOUNTER)
        attachments: Optional list of Attachment objects from the event

    Returns:
        RFC 6047 compliant MIME message
    """
    # Create multipart/mixed message
    message = MIMEMultipart("mixed")
    message["From"] = from_email
    message["To"] = to_email
    message["Reply-To"] = from_email
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)

    # Part 1: Human-readable text
    message.attach(MIMEText(body_text, "plain", "utf-8"))

    # Part 2: iTIP calendar data with METHOD parameter
    # RFC 6047: text/calendar; method=REQUEST; charset=utf-8
    calendar_part = MIMEText(icalendar_text, "calendar", "utf-8")
    calendar_part.add_header("Content-Disposition", "attachment", filename="invite.ics")
    calendar_part.set_param("method", method.upper())
    message.attach(calendar_part)

    # Part 3+: Optional file attachments from the event
    if attachments:
        add_attachments_to_message(message, attachments)
        logger.debug(f"Added {len(attachments)} attachment(s) to iTIP email")

    return message


def send_itip_email(
    email_config: EmailConfig,
    from_email: str,
    to_email: str,
    subject: str,
    body_text: str,
    icalendar_text: str,
    method: str,
    attachments: Optional[List[Attachment]] = None
) -> bool:
    """
    Send iTIP email via SMTP.

    Builds RFC 6047 compliant message and delivers via configured SMTP server.
    Handles STARTTLS and TLS/SSL connections with configurable certificate verification.

    Args:
        email_config: SMTP configuration
        from_email: Sender email address (organizer for REQUEST, attendee for REPLY)
        to_email: Recipient email address
        subject: Email subject line
        body_text: Human-readable message body
        icalendar_text: iCalendar data with METHOD
        method: iTIP method (REQUEST, REPLY, CANCEL, etc.)
        attachments: Optional list of Attachment objects from the event

    Returns:
        True if email sent successfully, False otherwise

    Note:
        Failures are logged but do not raise exceptions to prevent
        blocking event creation when email delivery fails.
    """
    # Build RFC 6047 MIME message
    message = build_itip_mime_message(
        from_email=from_email,
        to_email=to_email,
        subject=subject,
        body_text=body_text,
        icalendar_text=icalendar_text,
        method=method,
        attachments=attachments
    )

    # Dry-run mode: log but don't send
    if email_config.dryrun:
        logger.info(f"[DRY-RUN] Would send iTIP {method} email to {to_email}")
        logger.debug(f"[DRY-RUN] Subject: {subject}")
        logger.debug(f"[DRY-RUN] Body preview: {body_text[:100]}")
        return True

    # Convert message to string
    message_text = message.as_string()

    try:
        # Establish SMTP connection based on security type
        if email_config.security == SMTPSecurityType.NONE:
            logger.debug(f"Connecting to SMTP (no encryption): {email_config.host}:{email_config.port}")
            server = smtplib.SMTP(host=email_config.host, port=email_config.port)

        elif email_config.security == SMTPSecurityType.STARTTLS:
            logger.debug(f"Connecting to SMTP (STARTTLS): {email_config.host}:{email_config.port}")
            context = build_ssl_context(email_config.ssl_verify_mode)
            server = smtplib.SMTP(host=email_config.host, port=email_config.port)
            server.ehlo()  # Identify to server
            server.starttls(context=context)  # Upgrade to TLS
            server.ehlo()  # Re-identify after STARTTLS

        elif email_config.security == SMTPSecurityType.TLS:
            logger.debug(f"Connecting to SMTP (TLS/SSL): {email_config.host}:{email_config.port}")
            context = build_ssl_context(email_config.ssl_verify_mode)
            server = smtplib.SMTP_SSL(host=email_config.host, port=email_config.port, context=context)

        # Authenticate if credentials provided
        if email_config.username and email_config.password:
            logger.debug(f"Authenticating as {email_config.username}")
            server.login(user=email_config.username, password=email_config.password)

        # Send email
        errors = server.sendmail(
            from_addr=from_email,
            to_addrs=[to_email],
            msg=message_text
        )

        server.quit()

        # Check for delivery errors
        if errors:
            for email, (code, error_msg) in errors.items():
                logger.error(f"Failed to send iTIP {method} to {email}: {error_msg} (code {code})")
            return False

        logger.info(f"Successfully sent iTIP {method} email to {to_email}")
        return True

    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending iTIP {method} to {to_email}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending iTIP {method} to {to_email}: {e}")
        return False


def extract_attachments_from_icalendar(icalendar_text: str) -> List[Attachment]:
    """
    Extract ATTACH properties from iCalendar text.

    Supports two types of attachments per RFC 5545:
    1. Inline binary: ATTACH;ENCODING=BASE64;VALUE=BINARY;FMTTYPE=mime/type:base64data
    2. URI reference: ATTACH:https://example.com/file.pdf

    For inline attachments, the binary content is extracted.
    For URI attachments, the URL is recorded (caller may optionally download).

    Args:
        icalendar_text: iCalendar text with potential ATTACH properties

    Returns:
        List of Attachment objects
    """

    attachments = []

    # Parse iCalendar using vobject if available
    try:
        import vobject
        vcal = vobject.readOne(icalendar_text)

        # Get schedulable component
        component = None
        for comp_type in ('vevent', 'vtodo', 'vjournal'):
            if hasattr(vcal, comp_type):
                component = getattr(vcal, comp_type)
                break

        if not component:
            return []

        # Check for attach property
        if not hasattr(component, 'attach'):
            return []

        # Handle single or multiple attachments
        attach_list = component.contents.get('attach', [])
        if not isinstance(attach_list, list):
            attach_list = [attach_list]

        for attach in attach_list:
            try:
                value = attach.value
                params = attach.params if hasattr(attach, 'params') else {}

                # Check for inline binary data
                encoding = params.get('ENCODING', [''])[0].upper()
                value_type = params.get('VALUE', [''])[0].upper()
                mime_type = params.get('FMTTYPE', ['application/octet-stream'])[0]

                if encoding == 'BASE64' or value_type == 'BINARY':
                    # Inline base64 attachment
                    try:
                        # Decode base64 content
                        content = base64.b64decode(value)

                        # Generate filename from MIME type
                        ext = mimetypes.guess_extension(mime_type) or '.bin'
                        filename = f"attachment{len(attachments) + 1}{ext}"

                        # Check for X-FILENAME parameter (non-standard but common)
                        if 'X-FILENAME' in params:
                            filename = params['X-FILENAME'][0]
                        elif 'FILENAME' in params:
                            filename = params['FILENAME'][0]

                        attachments.append(Attachment(
                            filename=filename,
                            content=content,
                            mime_type=mime_type,
                            is_inline=True
                        ))
                        logger.debug(f"Extracted inline attachment: {filename} ({mime_type})")

                    except Exception as e:
                        logger.warning(f"Failed to decode inline attachment: {e}")

                elif value.startswith('http://') or value.startswith('https://'):
                    # URI reference - store the URL as content
                    # We don't download by default (privacy/security)
                    # Extract filename from URL
                    from urllib.parse import urlparse, unquote
                    parsed = urlparse(value)
                    filename = unquote(parsed.path.split('/')[-1]) or 'attachment.dat'

                    # Guess MIME type from filename
                    guessed_type, _ = mimetypes.guess_type(filename)
                    if guessed_type:
                        mime_type = guessed_type

                    attachments.append(Attachment(
                        filename=filename,
                        content=value.encode('utf-8'),  # Store URI as bytes
                        mime_type=mime_type,
                        is_inline=False  # URI reference, not inline
                    ))
                    logger.debug(f"Found URI attachment reference: {filename} -> {value}")

                else:
                    logger.debug(f"Unknown attachment format: {value[:50]}...")

            except Exception as e:
                logger.warning(f"Failed to parse attachment: {e}")
                continue

    except ImportError:
        logger.warning("vobject not available for attachment extraction")
    except Exception as e:
        logger.warning(f"Error extracting attachments: {e}")

    return attachments


def add_attachments_to_message(message: MIMEMultipart,
                               attachments: List[Attachment],
                               include_uri_refs: bool = True) -> None:
    """
    Add attachments to a MIME message.

    Args:
        message: MIMEMultipart message to add attachments to
        attachments: List of Attachment objects
        include_uri_refs: If True, include URI references as text links
                         in a separate part instead of skipping them
    """
    for attachment in attachments:
        if attachment.is_inline:
            # Add as binary MIME part
            maintype, subtype = attachment.mime_type.split('/', 1) if '/' in attachment.mime_type else ('application', 'octet-stream')

            part = MIMEBase(maintype, subtype)
            part.set_payload(attachment.content)
            encoders.encode_base64(part)

            part.add_header(
                'Content-Disposition',
                'attachment',
                filename=attachment.filename
            )

            message.attach(part)
            logger.debug(f"Added attachment: {attachment.filename}")

        elif include_uri_refs:
            # Add URI references as a text note
            # The actual file is not downloaded (privacy/bandwidth)
            uri = attachment.content.decode('utf-8')
            note = MIMEText(
                f"This event includes an attachment that can be downloaded from:\n{uri}\n",
                'plain',
                'utf-8'
            )
            message.attach(note)
            logger.debug(f"Added attachment URI reference: {uri}")

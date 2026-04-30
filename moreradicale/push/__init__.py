"""
RFC 8030 Web Push notifications for Radicale.

This module implements Web Push notifications to alert clients when
calendar or address book data changes, eliminating the need for polling.

Architecture:
- subscription.py: Subscription data model and management
- storage.py: Persistent storage for push subscriptions
- vapid.py: VAPID (Voluntary Application Server Identification) key management
- sender.py: Push notification sender using pywebpush
- notifier.py: Maps calendar changes to push notifications

WebDAV Properties:
- DAV:push-transports: Advertises supported push mechanisms
- CS:pushkey: Unique identifier for subscribing to collection changes

References:
    RFC 8030: Generic Event Delivery Using HTTP Push
    RFC 8291: Message Encryption for Web Push
    RFC 8292: Voluntary Application Server Identification (VAPID)
"""

# Push message urgency levels (RFC 8030)
URGENCY_VERY_LOW = "very-low"
URGENCY_LOW = "low"
URGENCY_NORMAL = "normal"
URGENCY_HIGH = "high"

URGENCY_LEVELS = [URGENCY_VERY_LOW, URGENCY_LOW, URGENCY_NORMAL, URGENCY_HIGH]

# Default TTL for push messages (24 hours)
DEFAULT_TTL = 86400

# CalendarServer namespace for pushkey
CS_NAMESPACE = "http://calendarserver.org/ns/"

# WebDAV property names
PROP_PUSH_TRANSPORTS = "{DAV:}push-transports"
PROP_PUSHKEY = "{http://calendarserver.org/ns/}pushkey"

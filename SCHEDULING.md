# CalDAV Scheduling Guide for moreradicale

This guide explains how to configure and use the RFC 6638 CalDAV Scheduling features in moreradicale, enabling meeting invitations, free/busy queries, resource booking, and more.

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Configuration Reference](#configuration-reference)
4. [Email Delivery for External Attendees](#email-delivery-for-external-attendees)
5. [Group Expansion](#group-expansion)
6. [Resource Scheduling](#resource-scheduling)
7. [Free/Busy Queries](#freebusy-queries)
8. [Calendar Availability (VAVAILABILITY)](#calendar-availability-vavailability)
9. [RFC 7986 Extended Properties](#rfc-7986-extended-properties)
10. [Client Compatibility](#client-compatibility)
11. [Troubleshooting](#troubleshooting)
12. [Calendar Sharing and Delegation](#calendar-sharing-and-delegation)
13. [Managed Attachments (RFC 8607)](#managed-attachments-rfc-8607)

---

## Overview

CalDAV Scheduling (RFC 6638) adds collaborative calendar features to moreradicale:

- **Meeting Invitations**: Organizers can invite attendees to events
- **RSVP Responses**: Attendees can accept, decline, or tentatively accept
- **Free/Busy Queries**: Check availability before scheduling
- **Resource Booking**: Auto-accept for conference rooms and equipment
- **Group Invitations**: Invite distribution lists that expand to members
- **External Attendees**: Email delivery for users outside your server

### How It Works

1. **Organizer** creates an event with attendees in their calendar client
2. **Client** sends the invitation via POST to `/user/schedule-outbox/`
3. **moreradicale** processes the iTIP message and routes it:
   - **Internal attendees**: Delivered to their `/user/schedule-inbox/`
   - **External attendees**: Sent via email (if configured)
   - **Resources (rooms/equipment)**: Auto-accepted if available
4. **Attendees** see the invitation and respond
5. **Organizer's copy** is updated with each attendee's response

---

## Quick Start

### Minimal Configuration

Add to your moreradicale configuration file:

```ini
[scheduling]
enabled = True
internal_domain = example.com
```

This enables:
- Internal scheduling (users on the same server)
- Schedule-inbox and schedule-outbox auto-creation
- Meeting invitations between `@example.com` users

### Test It

1. Create two users: `alice` and `bob`
2. In Alice's calendar client, create an event and invite `bob@example.com`
3. Check Bob's schedule-inbox for the invitation
4. Bob accepts → Alice's event shows Bob as "Accepted"

---

## Configuration Reference

All options are in the `[scheduling]` section:

### Core Settings

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `False` | Enable CalDAV scheduling support |
| `internal_domain` | `""` | Domain for internal users (e.g., `example.com`) |
| `max_attendees` | `100` | Maximum attendees per event (prevents abuse) |

### Email Delivery (External Attendees)

| Option | Default | Description |
|--------|---------|-------------|
| `email_enabled` | `False` | Enable email for external attendees |
| `email_dryrun` | `False` | Log emails without sending (for testing) |
| `smtp_from_organizer` | `False` | Send from organizer's address (requires SPF/DKIM) |
| `email_subject_prefix` | `""` | Prefix for email subjects |

### Email Templates

| Option | Description |
|--------|-------------|
| `request_template` | Template for invitation emails |
| `cancel_template` | Template for cancellation emails |
| `counter_template` | Template for counter-proposal emails |
| `declinecounter_template` | Template for declined counter-proposals |
| `refresh_template` | Template for refresh request emails |

**Template Variables:**
- `$event_title` - Event summary
- `$event_start_time` - Start time
- `$event_end_time` - End time
- `$event_location` - Location
- `$event_description` - Description
- `$organizer_name` - Organizer's name
- `$attendee_name` - Recipient's name

### Group Expansion

| Option | Default | Description |
|--------|---------|-------------|
| `groups_file` | `""` | Path to JSON file defining groups |

### Webhook (Advanced)

| Option | Default | Description |
|--------|---------|-------------|
| `webhook_enabled` | `False` | Enable inbound webhook for external responses |
| `webhook_path` | `/scheduling/webhook` | Webhook URL path |
| `webhook_secret` | `""` | HMAC secret for webhook authentication |

---

## Email Delivery for External Attendees

When attendees are outside your `internal_domain`, moreradicale can deliver invitations via email (RFC 6047 iMIP).

### SMTP Configuration

Email uses the SMTP settings from the `[hook]` section:

```ini
[hook]
# SMTP server settings
smtp_server = smtp.example.com
smtp_port = 587
smtp_security = starttls
smtp_username = calendar@example.com
smtp_password = your-smtp-password
from_email = calendar@example.com

[scheduling]
enabled = True
internal_domain = example.com
email_enabled = True
```

### Security Options

| Setting | Values | Description |
|---------|--------|-------------|
| `smtp_security` | `none`, `starttls`, `tls` | Connection encryption |
| `smtp_ssl_verify_mode` | `REQUIRED`, `OPTIONAL`, `NONE` | Certificate verification |

### Testing Email Delivery

Enable dry-run mode to test without sending:

```ini
[scheduling]
email_enabled = True
email_dryrun = True
```

Check logs for `[DRY-RUN] Would send iTIP...` messages.

### Custom Email Templates

```ini
[scheduling]
request_template = """Hello $attendee_name,

You've been invited to: $event_title

When: $event_start_time - $event_end_time
Where: $event_location
From: $organizer_name

Please open the attached .ics file to respond.

$event_description"""
```

### Attachments

Event attachments are automatically included in emails:
- **Inline attachments** (base64 encoded): Attached as files
- **URL references**: Included as download links

---

## Inbound Email Processing (RFC 6047)

When external attendees respond to meeting invitations, their REPLY messages need to reach moreradicale to update the organizer's calendar. R adicale supports two methods: **IMAP polling** and **HTTP webhooks**.

### Why Inbound Processing Matters

Without inbound processing:
- Organizer sends invitation email to `external@gmail.com`
- External attendee clicks "Accept" in Gmail
- **Their REPLY email is lost** - organizer never knows they accepted

With inbound processing:
- REPLY email arrives at your mailbox or webhook
- moreradicale processes it and updates PARTSTAT in organizer's calendar
- Organizer sees "Accepted" status automatically

### Method 1: IMAP Polling

Poll an IMAP mailbox for incoming iTIP REPLY messages.

#### Configuration

```ini
[scheduling]
enabled = True
internal_domain = example.com
email_enabled = True

# IMAP polling settings
imap_enabled = True
imap_server = imap.example.com
imap_port = 993
imap_security = ssl           # ssl, starttls, or plain
imap_username = calendar@example.com
imap_password = your-password
imap_folder = INBOX
imap_poll_interval = 60       # seconds between polls

# Message handling
imap_processed_folder = Processed  # Move processed messages here
imap_failed_folder = Failed        # Move failed messages here
```

#### IMAP Security Options

| Value | Port | Description |
|-------|------|-------------|
| `ssl` | 993 | Direct SSL connection (recommended) |
| `starttls` | 143 | Plain connection upgraded to TLS |
| `plain` | 143 | Unencrypted (not recommended) |

#### IMAP Folder Management

- **Processed**: Successfully processed REPLY messages
- **Failed**: Messages that couldn't be processed
- **No folder specified**: Messages are deleted after processing

#### Starting the IMAP Poller

The poller starts automatically when moreradicale starts if `imap_enabled = True`. Check logs for:

```
INFO  moreradicale.itip.imap_poller: IMAP poller started: calendar@example.com@imap.example.com:993/INBOX (interval=60s)
INFO  moreradicale.itip.imap_poller: Processed 2 iTIP message(s) from IMAP
```

#### Manual Polling (Cron)

For environments where background threads aren't ideal:

```python
# poll_imap.py
from moreradicale import config, storage
from moreradicale.itip.imap_poller import IMAPPoller

cfg = config.Configuration({})
st = storage.load(cfg)
poller = IMAPPoller(cfg, st)
count = poller.poll_once()
print(f"Processed {count} messages")
```

Cron job:
```cron
*/5 * * * * /usr/bin/python3 /path/to/poll_imap.py >> /var/log/radicale-imap.log 2>&1
```

### Method 2: HTTP Webhooks

Receive REPLY messages via HTTP POST from email webhook providers.

#### Supported Providers

- **Generic** - Any provider that can POST raw email
- **SendGrid Inbound Parse** - Pre-parsed email data
- **Mailgun Routes** - Pre-parsed with attachments
- **Postmark Inbound** - Clean JSON format

#### Configuration

```ini
[scheduling]
enabled = True
internal_domain = example.com

# Webhook settings
webhook_enabled = True
webhook_path = /scheduling/webhook
webhook_secret = your-random-secret-here
webhook_allowed_ips = 192.168.1.0/24,10.0.0.0/8
webhook_provider = generic      # generic, sendgrid, mailgun, postmark
webhook_max_size = 10485760     # 10 MB
```

#### Security

Webhooks use **dual authentication**:

1. **IP Whitelist**: Only listed IPs can POST
2. **HMAC Signature**: Cryptographic verification

```bash
# Generate HMAC signature (Python example)
import hmac
import hashlib

signature = hmac.new(
    secret.encode('utf-8'),
    request_body.encode('utf-8'),
    hashlib.sha256
).hexdigest()
```

Include signature in request header:
```
X-moreradicale-Signature: sha256=<signature>
```

#### Provider Setup Examples

**SendGrid Inbound Parse**:
1. Go to Settings → Inbound Parse
2. Add domain: `calendar.example.com`
3. Destination URL: `https://radicale.example.com/scheduling/webhook`
4. Set `webhook_provider = sendgrid`

**Mailgun Routes**:
1. Go to Sending → Routes
2. Filter Expression: `match_recipient("calendar@example.com")`
3. Actions: Forward to `https://radicale.example.com/scheduling/webhook`
4. Set `webhook_provider = mailgun`

**Postmark Inbound**:
1. Go to Servers → Inbound
2. Webhook URL: `https://radicale.example.com/scheduling/webhook`
3. Set `webhook_provider = postmark`

**Generic (Any Provider)**:
1. Configure provider to POST raw email to webhook URL
2. Set `webhook_provider = generic`
3. Request body should be raw RFC 822 email

#### Testing Webhooks

```bash
# Test with curl
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-moreradicale-Signature: sha256=$(echo -n '{...}' | openssl dgst -sha256 -hmac 'your-secret' | cut -d' ' -f2)" \
  -d '{
    "from": "external@gmail.com",
    "to": "calendar@example.com",
    "subject": "Re: Meeting Invitation",
    "text/calendar": "BEGIN:VCALENDAR\nMETHOD:REPLY\n..."
  }' \
  https://radicale.example.com/scheduling/webhook
```

Check logs for:
```
INFO  moreradicale.app.webhook: Webhook received from 192.168.1.100
INFO  moreradicale.itip.processor: External REPLY processed: external@gmail.com -> ACCEPTED for event meeting-123
```

### REPLY Processing Flow

1. **Email arrives** at IMAP or webhook
2. **Parse iTIP** - Extract METHOD:REPLY
3. **Validate sender** - Email must match ATTENDEE in iTIP
4. **Validate organizer** - Must be internal user
5. **Find event** - Look up by UID in organizer's calendar
6. **Update PARTSTAT** - Change NEEDS-ACTION → ACCEPTED/DECLINED/etc.
7. **Save** - Commit updated event

### Security Validation

moreradicale enforces strict security:

| Check | Purpose |
|-------|---------|
| Sender matches ATTENDEE | Prevents spoofing responses |
| Organizer is internal | External → external not allowed |
| Event exists | Prevents creating fake events |
| UID matches | Ensures REPLY is for correct event |

If any check fails, REPLY is rejected and logged.

### Troubleshooting Inbound Email

**IMAP: "Authentication failed"**
- Check username/password
- Verify IMAP is enabled on email server
- Try `imap_security = plain` temporarily to diagnose

**IMAP: "No messages processed"**
- Check email client's sent folder for actual REPLY
- Verify REPLY has `METHOD:REPLY` in iCalendar part
- Check logs for parsing errors

**Webhook: 403 Forbidden**
- Verify IP is in `webhook_allowed_ips`
- Check HMAC signature calculation
- Test with `curl` to isolate provider issues

**REPLY ignored**
- Check sender email matches ATTENDEE
- Verify organizer is internal user
- Look for "External REPLY sender mismatch" in logs

### Complete Example

```ini
[scheduling]
# Core scheduling
enabled = True
internal_domain = example.com

# Outbound email (invitations)
email_enabled = True

# Inbound email (responses) - IMAP
imap_enabled = True
imap_server = imap.example.com
imap_port = 993
imap_security = ssl
imap_username = calendar@example.com
imap_password = secret
imap_folder = INBOX
imap_poll_interval = 30
imap_processed_folder = Processed
imap_failed_folder = Failed

# Alternative: Webhook
webhook_enabled = False
webhook_path = /scheduling/webhook
webhook_secret = random-secret-256-bits
webhook_allowed_ips = 192.168.1.0/24
webhook_provider = sendgrid
```

With this setup:
1. Alice (internal) invites `bob@gmail.com`
2. Email sent to Bob via SMTP
3. Bob clicks "Accept" in Gmail
4. REPLY email arrives in calendar@example.com INBOX
5. IMAP poller processes it every 30 seconds
6. Alice's calendar shows Bob as "Accepted"

---

## Group Expansion

Invite entire teams with a single email address using `CUTYPE=GROUP`.

### Setup

1. Create a JSON file defining your groups:

```json
{
  "engineering@example.com": {
    "name": "Engineering Team",
    "members": [
      "alice@example.com",
      "bob@example.com",
      "charlie@example.com"
    ]
  },
  "all-hands@example.com": {
    "name": "All Hands",
    "members": [
      "engineering@example.com",
      "marketing@example.com",
      "david@example.com"
    ]
  }
}
```

2. Configure moreradicale to use it:

```ini
[scheduling]
enabled = True
internal_domain = example.com
groups_file = /etc/moreradicale/groups.json
```

### Features

- **Nested Groups**: Groups can contain other groups (recursive expansion)
- **Deduplication**: Members appearing in multiple groups receive one invitation
- **Circular Reference Protection**: Prevents infinite loops

### Usage

In your calendar client, invite `engineering@example.com` with `CUTYPE=GROUP`:

```
ATTENDEE;CUTYPE=GROUP:mailto:engineering@example.com
```

moreradicale expands this to individual invitations for alice, bob, and charlie.

---

## Resource Scheduling

Conference rooms, projectors, and other resources can automatically accept or decline based on availability using RFC 6638 SCHEDULE-AGENT=SERVER.

### How It Works

1. Create a "user" for each resource (e.g., `room101`, `projector1`)
2. Give the resource a calendar
3. Invite the resource with `CUTYPE=ROOM` or `CUTYPE=RESOURCE` and `SCHEDULE-AGENT=SERVER`
4. moreradicale's AutoScheduler automatically:
   - Checks the resource's calendar for conflicts
   - Checks VAVAILABILITY constraints (if defined)
   - **Accepts** if the time slot is free (based on policy)
   - **Declines** if there's a conflict (based on policy)
   - **Tentative** if configured to accept conflicts tentatively

### Auto-Accept Policies

Configure how resources respond to scheduling requests:

| Policy | Behavior | Use Case |
|--------|----------|----------|
| **if-free** (default) | Accept if no conflicts, decline otherwise | Conference rooms, vehicles |
| **always** | Always accept (allows double-booking) | Shared resources, projectors |
| **manual** | Never auto-accept, requires manual approval | VIP calendars, executive assistants |
| **tentative-if-conflict** | Accept if free, tentative if conflict | Resources that can be shared with approval |

### Configuration

#### Global Policy

Set the default policy for all resources:

```ini
[scheduling]
enabled = True
internal_domain = example.com
auto_accept_policy = if-free
```

#### Per-Resource Policies

Override policies for specific resources using a JSON file:

```json
{
  "conference-room-a@example.com": "if-free",
  "projector@example.com": "always",
  "ceo-calendar@example.com": "manual",
  "shared-desk@example.com": "tentative-if-conflict"
}
```

Configure the file path:

```ini
[scheduling]
resource_policies_file = /etc/moreradicale/resource-policies.json
```

### Setup

```bash
# Resources are just users with calendars
# Create via your normal user provisioning process
```

### Inviting Resources

In your calendar client, add the resource as an attendee with the appropriate CUTYPE:

```
ATTENDEE;CUTYPE=ROOM;SCHEDULE-AGENT=SERVER:mailto:room101@example.com
ATTENDEE;CUTYPE=RESOURCE;SCHEDULE-AGENT=SERVER:mailto:projector1@example.com
```

**Note**: `SCHEDULE-AGENT=SERVER` is the default per RFC 6638, but some clients may require it to be explicit.

### Conflict Detection

The AutoScheduler checks for conflicts before accepting. The following are **ignored** when checking:
- Events with `STATUS:CANCELLED`
- Events with `TRANSP:TRANSPARENT` (free/busy shows as free)
- The same event being rescheduled (same UID)
- VAVAILABILITY unavailable periods (if defined)

### VAVAILABILITY Integration

Resources can define availability patterns using RFC 7953 VAVAILABILITY components. For example, a conference room might only be available during business hours:

```ical
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//moreradicale//RFC7953//EN
BEGIN:VAVAILABILITY
UID:room101-availability
DTSTAMP:20250101T000000Z
SUMMARY:Business Hours Only
BUSYTYPE:BUSY-UNAVAILABLE
BEGIN:AVAILABLE
UID:room101-weekdays
DTSTART:20250101T090000Z
DTEND:20250101T170000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
SUMMARY:Weekday Hours
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR
```

The AutoScheduler will:
1. Check for conflicting events
2. Check if requested time falls within AVAILABLE slots
3. Decline if outside available hours or if conflicts exist

### SCHEDULE-AGENT Parameter

Control how scheduling is processed:

| Value | Behavior |
|-------|----------|
| **SERVER** (default) | moreradicale handles scheduling automatically |
| **CLIENT** | Client handles scheduling, server skips auto-accept |
| **NONE** | No scheduling processing at all |

Example - client-side scheduling:

```
ATTENDEE;CUTYPE=ROOM;SCHEDULE-AGENT=CLIENT:mailto:room101@example.com
```

This skips auto-accept processing entirely.

---

## Free/Busy Queries

Check attendee availability before scheduling meetings.

### How It Works

1. Client sends a `VFREEBUSY` REQUEST to schedule-outbox
2. moreradicale queries each attendee's calendars
3. Returns busy periods in the response

### Example Request

```
BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VFREEBUSY
DTSTAMP:20251228T100000Z
DTSTART:20251229T080000Z
DTEND:20251229T180000Z
ORGANIZER:mailto:alice@example.com
ATTENDEE:mailto:bob@example.com
ATTENDEE:mailto:charlie@example.com
END:VFREEBUSY
END:VCALENDAR
```

### Response

moreradicale returns each attendee's busy times:

```
FREEBUSY;FBTYPE=BUSY:20251229T100000Z/20251229T110000Z
FREEBUSY;FBTYPE=BUSY-TENTATIVE:20251229T140000Z/20251229T150000Z
```

### What's Included

- Events with `TRANSP:OPAQUE` (default)
- `FBTYPE=BUSY` for confirmed events
- `FBTYPE=BUSY-TENTATIVE` for tentative events

### What's Excluded

- `STATUS:CANCELLED` events
- `TRANSP:TRANSPARENT` events
- External attendees (no calendar access)

---

## Calendar Availability (VAVAILABILITY)

Define when users are typically available using RFC 7953 VAVAILABILITY components. This enhances free/busy queries by showing unavailable periods even when no events are scheduled.

### How It Works

1. User stores a **VAVAILABILITY** component in their calendar
2. This defines their general availability pattern (e.g., "Mon-Fri 9am-5pm")
3. When someone queries their free/busy, times **outside** available periods show as busy-unavailable
4. Events still show as busy during available periods

### Example: Work Hours

Store this in a user's calendar to indicate they're only available during work hours:

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//EN
BEGIN:VAVAILABILITY
UID:work-hours@example.com
DTSTAMP:20251228T100000Z
DTSTART:20250101T000000Z
SUMMARY:Work Hours
PRIORITY:1
BUSYTYPE:BUSY-UNAVAILABLE
BEGIN:AVAILABLE
UID:weekday-hours@example.com
DTSTAMP:20251228T100000Z
DTSTART:20250101T090000Z
DTEND:20250101T170000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
SUMMARY:Office Hours
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR
```

### Key Concepts

| Component | Description |
|-----------|-------------|
| `VAVAILABILITY` | Container defining an availability period |
| `AVAILABLE` | Subcomponent with specific available time slots |
| `BUSYTYPE` | What to show outside available times (`BUSY-UNAVAILABLE`, `BUSY`, `BUSY-TENTATIVE`) |
| `PRIORITY` | Priority when multiple VAVAILABILITY overlap (1=highest, 9=low, 0=undefined) |
| `RRULE` | Recurrence rule for repeating availability (on AVAILABLE subcomponents) |

### Multiple VAVAILABILITY Components

Users can have multiple VAVAILABILITY for different scenarios:

```
# High priority: Vacation (not available at all)
PRIORITY:1
DTSTART:20251224T000000Z
DTEND:20251226T000000Z
BUSYTYPE:BUSY-UNAVAILABLE
# No AVAILABLE subcomponents = never available

# Normal priority: Work hours
PRIORITY:5
DTSTART:20250101T000000Z
# AVAILABLE: Mon-Fri 9am-5pm
```

Higher priority (lower number) VAVAILABILITY takes precedence in overlapping periods.

### Effect on Free/Busy Queries

When someone queries Bob's availability for 8am-6pm:

**Without VAVAILABILITY:**
```
FREEBUSY;FBTYPE=BUSY:20251229T100000Z/20251229T110000Z
```
Only actual events show as busy.

**With VAVAILABILITY (9am-5pm work hours):**
```
FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:20251229T080000Z/20251229T090000Z
FREEBUSY;FBTYPE=BUSY:20251229T100000Z/20251229T110000Z
FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:20251229T170000Z/20251229T180000Z
```
Times outside work hours also show as busy-unavailable.

### Creating VAVAILABILITY

You can create VAVAILABILITY using the helper function:

```python
from moreradicale.itip.availability import create_vavailability_ics

ics = create_vavailability_ics(
    uid='work-hours',
    summary='Standard Work Week',
    available_slots=[
        {
            'dtstart': datetime(2025, 1, 1, 9, 0),
            'dtend': datetime(2025, 1, 1, 17, 0),
            'rrule': 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR',
            'summary': 'Office Hours'
        }
    ],
    priority=1,
    location='Office'
)
```

---

## RFC 7986 Extended Properties

moreradicale fully supports RFC 7986 - New Properties for iCalendar, which adds rich metadata to calendar events:

### COLOR Property

Add visual distinction to events with CSS3 color names:

```
BEGIN:VEVENT
UID:meeting@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T140000Z
DTEND:20251230T150000Z
SUMMARY:Team Meeting
COLOR:dodgerblue
END:VEVENT
```

Supported colors include: `dodgerblue`, `coral`, `teal`, `mediumseagreen`, `slateblue`, `crimson`, `darkorange`, and all CSS3 color names.

### CONFERENCE Property

Link virtual meeting rooms directly to calendar events:

```
BEGIN:VEVENT
UID:standup@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T090000Z
DTEND:20251230T091500Z
SUMMARY:Daily Standup
CONFERENCE;VALUE=URI;FEATURE=VIDEO,AUDIO;LABEL=Zoom:
 https://zoom.us/j/123456
CONFERENCE;VALUE=URI;FEATURE=PHONE;LABEL=Dial-in:
 tel:+1-555-123-4567
END:VEVENT
```

#### FEATURE Parameter Values

| Value | Description |
|-------|-------------|
| `VIDEO` | Video conferencing capability |
| `AUDIO` | Audio conferencing capability |
| `PHONE` | Dial-in phone number |
| `CHAT` | Text chat channel |
| `SCREEN` | Screen sharing capability |
| `MODERATOR` | Moderator access link |
| `FEED` | Streaming/broadcast feed |

Multiple features can be combined: `FEATURE=VIDEO,AUDIO,SCREEN`

### IMAGE Property

Attach visual content to events:

```
BEGIN:VEVENT
UID:conference@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T090000Z
DTEND:20251230T170000Z
SUMMARY:Tech Conference 2025
IMAGE;VALUE=URI;DISPLAY=BADGE;FMTTYPE=image/png:
 https://example.com/badge.png
IMAGE;VALUE=URI;DISPLAY=FULLSIZE;FMTTYPE=image/jpeg:
 https://example.com/banner.jpg
END:VEVENT
```

#### DISPLAY Parameter Values

| Value | Description |
|-------|-------------|
| `BADGE` | Small icon/badge (e.g., sponsor logo) |
| `THUMBNAIL` | Preview thumbnail |
| `FULLSIZE` | Full-size image |
| `GRAPHIC` | Decorative graphic |

### Combined Example

A complete event with all RFC 7986 properties:

```
BEGIN:VEVENT
UID:team-meeting@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T140000Z
DTEND:20251230T150000Z
SUMMARY:Weekly Team Sync
COLOR:mediumseagreen
CONFERENCE;VALUE=URI;FEATURE=VIDEO,AUDIO;LABEL=Teams:
 https://teams.microsoft.com/meet/123
CONFERENCE;VALUE=URI;FEATURE=PHONE:tel:+1-800-MEETING
IMAGE;VALUE=URI;DISPLAY=BADGE;FMTTYPE=image/png:
 https://example.com/team-logo.png
END:VEVENT
```

### Notes

- All RFC 7986 properties are preserved through CalDAV scheduling (meeting invitations)
- Properties are round-tripped correctly through storage and retrieval
- Most modern calendar clients (Thunderbird, Apple Calendar, etc.) can display these properties
- The vobject library provides native support for these properties

### Calendar-Level Properties

RFC 7986 also defines properties at the VCALENDAR level for self-describing calendars:

| Property | Description | Example |
|----------|-------------|---------|
| `NAME` | Calendar display name | `NAME:My Work Calendar` |
| `DESCRIPTION` | Calendar description | `DESCRIPTION:Team meetings` |
| `COLOR` | Calendar color (CSS3) | `COLOR:steelblue` |
| `REFRESH-INTERVAL` | Polling interval | `REFRESH-INTERVAL;VALUE=DURATION:P1D` |
| `SOURCE` | Source URL for subscribed calendars | `SOURCE;VALUE=URI:https://example.com/cal.ics` |
| `URL` | Associated URL | `URL:https://example.com/calendar` |
| `LAST-MODIFIED` | Last modification time | `LAST-MODIFIED:20251229T100000Z` |
| `IMAGE` | Calendar logo/icon | `IMAGE;VALUE=URI;DISPLAY=BADGE:https://example.com/logo.png` |

#### Subscribed Calendar Example

For imported/subscribed calendars, these properties provide metadata with the calendar data:

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Holiday Provider//EN
NAME:US Holidays 2025
DESCRIPTION:Official US federal holidays
COLOR:crimson
SOURCE;VALUE=URI:https://holidays.example.com/us.ics
REFRESH-INTERVAL;VALUE=DURATION:P7D
BEGIN:VEVENT
UID:christmas@holidays.example.com
DTSTAMP:20251001T000000Z
DTSTART;VALUE=DATE:20251225
SUMMARY:Christmas Day
END:VEVENT
END:VCALENDAR
```

This enables:
- **Self-describing calendars**: Name, color, and description travel with the data
- **Subscription management**: SOURCE and REFRESH-INTERVAL guide clients on updates
- **Branding**: IMAGE provides visual identity for calendars

---

## Client Compatibility

### Tested Clients

| Client | Status | Notes |
|--------|--------|-------|
| Thunderbird | Works | Full scheduling support |
| Apple Calendar | Works | Native macOS/iOS support |
| Evolution | Works | GNOME calendar |
| DAVx⁵ | Works | Android sync |

### Client Configuration

Most clients auto-discover scheduling support via:
- `schedule-inbox-URL` property on principal
- `schedule-outbox-URL` property on principal
- `calendar-user-address-set` property

If your client doesn't auto-detect, manually configure:
- Outbox: `https://server/user/schedule-outbox/`
- Inbox: `https://server/user/schedule-inbox/`

---

## Troubleshooting

### Enable Debug Logging

```ini
[logging]
level = debug
```

Look for messages containing:
- `iTIP` - Message processing
- `Scheduling` - Routing decisions
- `SMTP` - Email delivery

### Common Issues

#### "Scheduling not enabled"

```
405 Method Not Allowed on POST to schedule-outbox
```

**Solution**: Add `enabled = True` to `[scheduling]` section.

#### "User not authorized as organizer"

```
403 Forbidden on POST
```

**Solution**: The authenticated user must match the ORGANIZER in the iTIP message. Users can only send invitations from their own email address.

#### "Too many attendees"

```
403 Forbidden - max_attendees exceeded
```

**Solution**: Increase `max_attendees` in configuration or reduce attendee count.

#### External attendees show "Pending"

**Cause**: Email delivery not configured.

**Solution**: Configure SMTP settings in `[hook]` section and enable `email_enabled`.

#### Group not expanding

**Cause**: Group email not found in groups file.

**Solution**:
1. Verify `groups_file` path is correct
2. Check group email matches exactly (case-insensitive)
3. Ensure JSON syntax is valid

#### Resource always declines

**Cause**: Conflicting event in resource's calendar.

**Solution**:
1. Check resource's calendar for overlapping events
2. Cancelled/transparent events shouldn't cause conflicts
3. Verify resource user/calendar exists

### Checking Inbox Contents

Use PROPFIND to list schedule-inbox contents:

```bash
curl -X PROPFIND \
  -H "Depth: 1" \
  -u bob:password \
  https://server/bob/schedule-inbox/
```

### Verifying Configuration

Check that scheduling properties appear on principal:

```bash
curl -X PROPFIND \
  -H "Depth: 0" \
  -u alice:password \
  -d '<?xml version="1.0"?>
<propfind xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <prop>
    <C:schedule-inbox-URL/>
    <C:schedule-outbox-URL/>
  </prop>
</propfind>' \
  https://server/alice/
```

---

## Security Considerations

### Organizer Validation

moreradicale validates that the authenticated user matches the ORGANIZER in iTIP messages. This prevents:
- Spoofing invitations from other users
- Unauthorized access to others' outboxes

### Max Attendees Limit

The `max_attendees` setting prevents:
- Email bombing (mass external invitations)
- Denial of service via large attendee lists

Default: 100 attendees per event.

### External Email

When `smtp_from_organizer = True`:
- Emails appear to come from the organizer
- Requires proper SPF/DKIM configuration
- May fail if your SMTP server restricts sender addresses

When `smtp_from_organizer = False` (default):
- Emails come from `from_email` in hook config
- More reliable delivery
- Reply-To set to organizer

---

## RFC Compliance

This implementation follows:

- **RFC 6638**: Scheduling Extensions to CalDAV
- **RFC 5546**: iCalendar Transport-Independent Interoperability Protocol (iTIP)
- **RFC 6047**: iCalendar Message-Based Interoperability Protocol (iMIP)
- **RFC 5545**: Internet Calendaring and Scheduling (iCalendar)
- **RFC 7953**: Calendar Availability (VAVAILABILITY)
- **RFC 7986**: New Properties for iCalendar (COLOR, CONFERENCE, IMAGE)

### Supported iTIP Methods

| Method | Support | Description |
|--------|---------|-------------|
| REQUEST | Full | Create/update meeting |
| REPLY | Full | Respond to invitation |
| CANCEL | Full | Cancel meeting |
| ADD | Full | Add occurrence to recurring event |
| REFRESH | Full | Request updated copy |
| COUNTER | Full | Propose alternative time |
| DECLINECOUNTER | Full | Decline counter-proposal |
| PUBLISH | Partial | Publish event (no delivery) |

### SCHEDULE-STATUS Codes

| Code | Meaning |
|------|---------|
| 1.0 | Unknown status |
| 1.1 | Pending (not yet delivered) |
| 1.2 | Delivered |
| 2.0 | Success |
| 3.7 | Invalid calendar user |
| 3.8 | No scheduling support |
| 5.1 | Delivery failed |
| 5.3 | Invalid date/property |

---

## Example Configuration

Complete production configuration:

```ini
[server]
hosts = 0.0.0.0:5232

[auth]
type = htpasswd
htpasswd_filename = /etc/moreradicale/users
htpasswd_encryption = bcrypt

[storage]
filesystem_folder = /var/lib/moreradicale/collections

[hook]
# SMTP for external attendee emails
smtp_server = smtp.example.com
smtp_port = 587
smtp_security = starttls
smtp_username = calendar@example.com
smtp_password = ${SMTP_PASSWORD}
from_email = calendar@example.com

[scheduling]
enabled = True
internal_domain = example.com
max_attendees = 50

# Email delivery
email_enabled = True
email_subject_prefix = [Calendar]

# Group expansion
groups_file = /etc/moreradicale/groups.json

[logging]
level = info
```

---

## Calendar Sharing and Delegation

moreradicale supports two collaboration features beyond basic scheduling:

### Calendar Sharing

Share your calendar with other users, granting read or read-write access.

#### Configuration

```ini
[sharing]
enabled = True
delegation_enabled = True
auto_accept_same_domain = False

[rights]
type = owner_only_shared
```

#### How Sharing Works

1. **Owner shares calendar**: Alice shares `/alice/calendar/` with Bob
2. **Invitation pending**: Share appears in Bob's calendar list as pending
3. **Bob accepts**: Share becomes active
4. **Access granted**: Bob can now read (or read-write) Alice's calendar

#### Sharing via POST Request

To share a calendar, send a POST request with XML body:

```xml
<?xml version="1.0"?>
<CS:share-resource xmlns:CS="http://calendarserver.org/ns/"
                   xmlns:D="DAV:">
    <CS:set>
        <D:href>/bob/</D:href>
        <CS:common-name>Bob Smith</CS:common-name>
        <CS:read-write/>
    </CS:set>
</CS:share-resource>
```

To accept a share invitation:

```xml
<?xml version="1.0"?>
<CS:share-reply xmlns:CS="http://calendarserver.org/ns/">
    <CS:invite-accepted/>
</CS:share-reply>
```

#### PROPFIND Properties

| Property | Description |
|----------|-------------|
| `CS:invite` | List of share invitations on a calendar |
| `CS:shared-url` | Original URL when viewing shared calendar |
| `CS:allowed-sharing-modes` | Indicates sharing is supported |

### Scheduling Delegation

Allow a delegate (e.g., assistant) to send meeting invitations on behalf of another user.

#### How Delegation Works

1. **Boss configures delegate**: Boss adds Secretary to their schedule-delegates
2. **Secretary creates meeting**: Secretary creates event with Boss as ORGANIZER
3. **moreradicale validates**: Secretary is authorized as Boss's delegate
4. **Invitations sent**: Meeting requests go out from Boss

#### Delegate Properties

Stored in principal's `.moreradicale.props`:

```json
{
    "RADICALE:schedule-delegates": "[\"secretary\"]",
    "RADICALE:calendar-proxy-write": "[\"secretary\"]",
    "RADICALE:calendar-proxy-read": "[\"assistant\"]"
}
```

#### PROPFIND Properties

| Property | Description |
|----------|-------------|
| `CS:calendar-proxy-read-for` | Principals the user can proxy-read |
| `CS:calendar-proxy-write-for` | Principals the user can proxy-write |

### Rights Backend: owner_only_shared

The `owner_only_shared` rights backend extends `owner_only` to check:

1. **Owner access** (standard owner_only behavior)
2. **Shared calendar access** via `RADICALE:shares` property
3. **Proxy access** via `RADICALE:calendar-proxy-*` properties

Permission levels:
- **r**: Read-only access to calendar items
- **rw**: Read-write access to calendar items
- **R**: Read access to principal/collection metadata
- **RW**: Full access including write to metadata

---

## Managed Attachments (RFC 8607)

moreradicale supports RFC 8607 CalDAV Managed Attachments, enabling server-side storage of event attachments. Instead of embedding large files as base64 in calendar events, attachments are stored separately and referenced by URL. This reduces calendar synchronization bandwidth and improves performance.

### Overview

| Feature | Benefit |
|---------|---------|
| Server-side storage | Attachments don't bloat calendar files |
| URL references | Efficient sync—only download when needed |
| Size limits | Configurable per-attachment and per-event limits |
| Access control | Only calendar owners can access their attachments |

### Configuration

Add to your moreradicale configuration:

```ini
[attachments]
enabled = True
filesystem_folder = /var/lib/moreradicale/attachments
max_size = 10000000
max_per_resource = 20
```

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `False` | Enable managed attachments |
| `filesystem_folder` | `/var/lib/moreradicale/attachments` | Directory for attachment storage |
| `max_size` | `10000000` | Maximum attachment size in bytes (default 10MB) |
| `max_per_resource` | `20` | Maximum attachments per calendar event |
| `base_url` | `""` | Base URL for attachment serving (auto-detected if empty) |

### Storage Layout

```
/var/lib/moreradicale/attachments/
├── alice/
│   ├── abc123-def4-5678-90ab-cdef12345678
│   ├── .metadata/
│   │   └── abc123-def4-5678-90ab-cdef12345678.json
└── bob/
    └── ...
```

Each user has their own directory containing:
- **Binary files**: Attachment data stored by managed ID
- **Metadata**: JSON files with filename, content type, size, and ownership info

### HTTP Operations

All attachment operations use POST requests with query parameters:

#### Add Attachment

```bash
curl -X POST \
  -u alice:password \
  -H "Content-Type: application/pdf" \
  -H 'Content-Disposition: attachment; filename="report.pdf"' \
  --data-binary @report.pdf \
  "https://server/alice/calendar/event.ics?action=attachment-add"
```

**Response:**
```
HTTP/1.1 201 Created
Cal-Managed-ID: abc123-def4-5678-90ab-cdef12345678
```

#### Update Attachment

```bash
curl -X POST \
  -u alice:password \
  -H "Content-Type: application/pdf" \
  --data-binary @updated-report.pdf \
  "https://server/alice/calendar/event.ics?action=attachment-update&managed-id=abc123-def4-5678-90ab-cdef12345678"
```

**Response:** `204 No Content`

#### Remove Attachment

```bash
curl -X POST \
  -u alice:password \
  "https://server/alice/calendar/event.ics?action=attachment-remove&managed-id=abc123-def4-5678-90ab-cdef12345678"
```

**Response:** `204 No Content`

#### Retrieve Attachment

```bash
curl -u alice:password \
  "https://server/.attachments/alice/abc123-def4-5678-90ab-cdef12345678"
```

### iCalendar ATTACH Property

When you add a managed attachment, moreradicale updates the event's ATTACH property:

```
BEGIN:VEVENT
UID:meeting@example.com
DTSTAMP:20251229T100000Z
DTSTART:20251230T140000Z
SUMMARY:Team Meeting
ATTACH;MANAGED-ID=abc123-def4;FILENAME=report.pdf;SIZE=2048;
 FMTTYPE=application/pdf:https://server/.attachments/alice/abc123-def4
END:VEVENT
```

ATTACH parameters:
- **MANAGED-ID**: Server-generated unique identifier
- **FILENAME**: Original filename
- **SIZE**: File size in bytes
- **FMTTYPE**: MIME content type

### PROPFIND Properties

Discover attachment capabilities via PROPFIND:

```xml
<?xml version="1.0"?>
<propfind xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <prop>
    <C:max-attachment-size/>
    <C:max-attachments-per-resource/>
  </prop>
</propfind>
```

Response includes configured limits:

```xml
<C:max-attachment-size>10000000</C:max-attachment-size>
<C:max-attachments-per-resource>20</C:max-attachments-per-resource>
```

### DAV Header

When attachments are enabled, the DAV header includes:

```
DAV: 1, 2, 3, calendar-access, calendar-managed-attachments
```

### Client Compatibility

| Client | Support | Notes |
|--------|---------|-------|
| Apple Calendar | Full | Native support for managed attachments |
| Thunderbird | Full | Recent versions support RFC 8607 |
| DAVx⁵ | Full | Android sync app |
| Evolution | Partial | May require manual URL handling |

### Security Considerations

1. **Access Control**: Only the attachment owner can retrieve their files
2. **Path Sanitization**: Managed IDs are validated to prevent path traversal
3. **Size Limits**: Configurable per-attachment and per-event limits prevent abuse
4. **Atomic Storage**: Files are written atomically (temp file + rename) to prevent corruption

### Error Responses

| Status | Cause |
|--------|-------|
| `201 Created` | Attachment added successfully |
| `204 No Content` | Attachment updated/removed successfully |
| `400 Bad Request` | Missing managed-id parameter for update/remove |
| `403 Forbidden` | User doesn't own the attachment |
| `404 Not Found` | Event or attachment not found |
| `413 Request Entity Too Large` | Attachment exceeds max_size |
| `507 Insufficient Storage` | Event already has max_per_resource attachments |
| `501 Not Implemented` | Attachments not enabled |

### Example: Complete Workflow

```bash
# 1. Create a calendar and event
curl -X MKCALENDAR -u alice:password https://server/alice/work/

curl -X PUT -u alice:password \
  -H "Content-Type: text/calendar" \
  --data-binary @- https://server/alice/work/meeting.ics << 'EOF'
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:meeting@example.com
DTSTART:20251230T140000Z
DTEND:20251230T150000Z
SUMMARY:Quarterly Review
END:VEVENT
END:VCALENDAR
EOF

# 2. Add an attachment
MANAGED_ID=$(curl -s -D - -o /dev/null -X POST -u alice:password \
  -H "Content-Type: application/pdf" \
  -H 'Content-Disposition: attachment; filename="Q4-report.pdf"' \
  --data-binary @Q4-report.pdf \
  "https://server/alice/work/meeting.ics?action=attachment-add" | \
  grep -i "Cal-Managed-ID" | cut -d: -f2 | tr -d ' \r')

echo "Attachment ID: $MANAGED_ID"

# 3. Verify ATTACH property in event
curl -s -u alice:password https://server/alice/work/meeting.ics | grep ATTACH

# 4. Download attachment
curl -u alice:password \
  "https://server/.attachments/alice/${MANAGED_ID}" > downloaded.pdf

# 5. Remove attachment when done
curl -X POST -u alice:password \
  "https://server/alice/work/meeting.ics?action=attachment-remove&managed-id=${MANAGED_ID}"
```

---

## Support

- GitHub Issues: https://github.com/Kozea/moreradicale/issues
- Discussions: https://github.com/Kozea/moreradicale/discussions

# CalDAV Scheduling Guide for Radicale

This guide explains how to configure and use the RFC 6638 CalDAV Scheduling features in Radicale, enabling meeting invitations, free/busy queries, resource booking, and more.

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

---

## Overview

CalDAV Scheduling (RFC 6638) adds collaborative calendar features to Radicale:

- **Meeting Invitations**: Organizers can invite attendees to events
- **RSVP Responses**: Attendees can accept, decline, or tentatively accept
- **Free/Busy Queries**: Check availability before scheduling
- **Resource Booking**: Auto-accept for conference rooms and equipment
- **Group Invitations**: Invite distribution lists that expand to members
- **External Attendees**: Email delivery for users outside your server

### How It Works

1. **Organizer** creates an event with attendees in their calendar client
2. **Client** sends the invitation via POST to `/user/schedule-outbox/`
3. **Radicale** processes the iTIP message and routes it:
   - **Internal attendees**: Delivered to their `/user/schedule-inbox/`
   - **External attendees**: Sent via email (if configured)
   - **Resources (rooms/equipment)**: Auto-accepted if available
4. **Attendees** see the invitation and respond
5. **Organizer's copy** is updated with each attendee's response

---

## Quick Start

### Minimal Configuration

Add to your Radicale configuration file:

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

When attendees are outside your `internal_domain`, Radicale can deliver invitations via email (RFC 6047 iMIP).

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

2. Configure Radicale to use it:

```ini
[scheduling]
enabled = True
internal_domain = example.com
groups_file = /etc/radicale/groups.json
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

Radicale expands this to individual invitations for alice, bob, and charlie.

---

## Resource Scheduling

Conference rooms, projectors, and other resources can automatically accept or decline based on availability.

### How It Works

1. Create a "user" for each resource (e.g., `room101`, `projector1`)
2. Give the resource a calendar
3. Invite the resource with `CUTYPE=ROOM` or `CUTYPE=RESOURCE`
4. Radicale automatically:
   - **Accepts** if the time slot is free
   - **Declines** if there's a conflict

### Setup

```bash
# Resources are just users with calendars
# Create via your normal user provisioning process
```

### Inviting Resources

In your calendar client, add the resource as an attendee with the appropriate CUTYPE:

```
ATTENDEE;CUTYPE=ROOM:mailto:room101@example.com
ATTENDEE;CUTYPE=RESOURCE:mailto:projector1@example.com
```

### Conflict Detection

Resources check their calendar for conflicts before accepting. The following are **ignored**:
- Events with `STATUS:CANCELLED`
- Events with `TRANSP:TRANSPARENT` (free/busy shows as free)
- The same event being rescheduled (same UID)

### SCHEDULE-AGENT

To handle resource booking yourself (not via server):

```
ATTENDEE;CUTYPE=ROOM;SCHEDULE-AGENT=CLIENT:mailto:room101@example.com
```

This skips auto-accept processing.

---

## Free/Busy Queries

Check attendee availability before scheduling meetings.

### How It Works

1. Client sends a `VFREEBUSY` REQUEST to schedule-outbox
2. Radicale queries each attendee's calendars
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

Radicale returns each attendee's busy times:

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
from radicale.itip.availability import create_vavailability_ics

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

Radicale fully supports RFC 7986 - New Properties for iCalendar, which adds rich metadata to calendar events:

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

Radicale validates that the authenticated user matches the ORGANIZER in iTIP messages. This prevents:
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
htpasswd_filename = /etc/radicale/users
htpasswd_encryption = bcrypt

[storage]
filesystem_folder = /var/lib/radicale/collections

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
groups_file = /etc/radicale/groups.json

[logging]
level = info
```

---

## Support

- GitHub Issues: https://github.com/Kozea/Radicale/issues
- Discussions: https://github.com/Kozea/Radicale/discussions

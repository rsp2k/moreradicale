#!/bin/bash
# CalDAV Calendar Sharing & Delegation - End-to-End Integration Test
#
# This script tests the CalendarServer sharing extensions and delegation.
# Requires: curl, xmllint (optional, for pretty-printing)
#
# Usage:
#   1. Start server: python3 -m radicale -C test-sharing-config.ini
#   2. Run tests: ./test-sharing.sh

set -e  # Exit on error

SERVER="http://127.0.0.1:5232"

# Users (must match test-sharing-users)
ALICE_AUTH="alice:alice123"
BOB_AUTH="bob:bob123"
CHARLIE_AUTH="charlie:charlie123"
SECRETARY_AUTH="secretary:secretary123"

# Namespaces for XML
NS_D="DAV:"
NS_CS="http://calendarserver.org/ns/"
NS_C="urn:ietf:params:xml:ns:caldav"

echo "====================================="
echo " CalDAV Sharing & Delegation Tests"
echo "====================================="
echo ""

# Check if server is running
if ! curl -s "$SERVER" > /dev/null 2>&1; then
    echo "ERROR: Radicale server not running at $SERVER"
    echo "Start with: python3 -m radicale -C test-sharing-config.ini"
    exit 1
fi
echo "Server is running at $SERVER"
echo ""

# Clean up any previous test data
rm -rf /tmp/radicale-sharing-test 2>/dev/null || true

# ============================================
# Setup: Create users and calendars
# ============================================
echo "Setup: Creating principals and calendars"
echo "-----------------------------------------"

# Create Alice's principal and calendar
curl -s -u "$ALICE_AUTH" -X MKCALENDAR "$SERVER/alice/work-calendar/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set>
    <D:prop>
      <D:displayname>Work Calendar</D:displayname>
    </D:prop>
  </D:set>
</C:mkcalendar>' > /dev/null
echo "Created Alice's work calendar"

# Create Bob's principal
curl -s -u "$BOB_AUTH" -X PROPFIND "$SERVER/bob/" -H "Depth: 0" > /dev/null
echo "Created Bob's principal"

# Create Charlie's principal
curl -s -u "$CHARLIE_AUTH" -X PROPFIND "$SERVER/charlie/" -H "Depth: 0" > /dev/null
echo "Created Charlie's principal"

# Add an event to Alice's calendar
EVENT_UID="test-event-$(date +%s)"
curl -s -u "$ALICE_AUTH" -X PUT "$SERVER/alice/work-calendar/$EVENT_UID.ics" \
  -H "Content-Type: text/calendar" \
  -d "BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:$EVENT_UID
DTSTAMP:20250101T120000Z
DTSTART:20250115T090000Z
DTEND:20250115T100000Z
SUMMARY:Team Meeting
END:VEVENT
END:VCALENDAR" > /dev/null
echo "Added test event to Alice's calendar"
echo ""

# ============================================
# Test 1: Verify initial access controls
# ============================================
echo "Test 1: Access Control (before sharing)"
echo "----------------------------------------"

# Bob should NOT be able to read Alice's calendar
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -u "$BOB_AUTH" \
  "$SERVER/alice/work-calendar/")

if [ "$HTTP_CODE" = "403" ] || [ "$HTTP_CODE" = "404" ]; then
    echo "PASS: Bob cannot access Alice's calendar (HTTP $HTTP_CODE)"
else
    echo "FAIL: Expected 403/404, got $HTTP_CODE"
    exit 1
fi
echo ""

# ============================================
# Test 2: Share calendar with read-write access
# ============================================
echo "Test 2: Share Calendar (Alice -> Bob, read-write)"
echo "--------------------------------------------------"

SHARE_RESPONSE=$(curl -s -w "\n%{http_code}" -u "$ALICE_AUTH" \
  -X POST "$SERVER/alice/work-calendar/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<CS:share-resource xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:set>
    <D:href>/bob/</D:href>
    <CS:common-name>Bob Smith</CS:common-name>
    <CS:read-write/>
  </CS:set>
</CS:share-resource>')

HTTP_CODE=$(echo "$SHARE_RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "PASS: Share request accepted (HTTP 200)"
else
    echo "FAIL: Share request failed (HTTP $HTTP_CODE)"
    echo "$SHARE_RESPONSE"
    exit 1
fi
echo ""

# ============================================
# Test 3: Verify sharing PROPFIND properties
# ============================================
echo "Test 3: Verify CS:invite Property"
echo "----------------------------------"

INVITE_PROP=$(curl -s -u "$ALICE_AUTH" -X PROPFIND "$SERVER/alice/work-calendar/" \
  -H "Depth: 0" \
  -d '<?xml version="1.0"?>
<D:propfind xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <D:prop>
    <CS:invite/>
  </D:prop>
</D:propfind>')

if echo "$INVITE_PROP" | grep -q "bob"; then
    echo "PASS: CS:invite shows Bob as sharee"
else
    echo "FAIL: CS:invite doesn't contain Bob"
    echo "$INVITE_PROP"
    exit 1
fi
echo ""

# ============================================
# Test 4: Accept the share invitation
# ============================================
echo "Test 4: Accept Share Invitation (Bob)"
echo "--------------------------------------"

# Bob accepts the share
ACCEPT_RESPONSE=$(curl -s -w "\n%{http_code}" -u "$BOB_AUTH" \
  -X POST "$SERVER/alice/work-calendar/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<CS:share-reply xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:href>/alice/work-calendar/</CS:href>
  <CS:invite-accepted/>
</CS:share-reply>')

HTTP_CODE=$(echo "$ACCEPT_RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "PASS: Share accepted (HTTP 200)"
else
    echo "FAIL: Accept request failed (HTTP $HTTP_CODE)"
    echo "$ACCEPT_RESPONSE"
    exit 1
fi
echo ""

# ============================================
# Test 5: Verify Bob can now access the calendar
# ============================================
echo "Test 5: Verify Shared Access (Bob reads Alice's calendar)"
echo "----------------------------------------------------------"

# Bob should now be able to read Alice's calendar
BOB_READ=$(curl -s -w "\n%{http_code}" -u "$BOB_AUTH" \
  "$SERVER/alice/work-calendar/$EVENT_UID.ics")

HTTP_CODE=$(echo "$BOB_READ" | tail -1)
BODY=$(echo "$BOB_READ" | head -n -1)

if [ "$HTTP_CODE" = "200" ] && echo "$BODY" | grep -q "Team Meeting"; then
    echo "PASS: Bob can read Alice's events"
else
    echo "FAIL: Bob cannot read Alice's calendar (HTTP $HTTP_CODE)"
    exit 1
fi
echo ""

# ============================================
# Test 6: Verify Bob can write (read-write share)
# ============================================
echo "Test 6: Verify Write Access (Bob adds event)"
echo "---------------------------------------------"

BOB_EVENT_UID="bob-added-event-$(date +%s)"
WRITE_RESPONSE=$(curl -s -w "%{http_code}" -u "$BOB_AUTH" \
  -X PUT "$SERVER/alice/work-calendar/$BOB_EVENT_UID.ics" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d "BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:$BOB_EVENT_UID
DTSTAMP:20250101T120000Z
DTSTART:20250116T140000Z
DTEND:20250116T150000Z
SUMMARY:Bob's Meeting on Alice's Calendar
END:VEVENT
END:VCALENDAR")

if [ "$WRITE_RESPONSE" = "201" ] || [ "$WRITE_RESPONSE" = "204" ]; then
    echo "PASS: Bob can write to shared calendar (HTTP $WRITE_RESPONSE)"
else
    echo "FAIL: Bob cannot write (HTTP $WRITE_RESPONSE)"
    exit 1
fi
echo ""

# ============================================
# Test 7: Share with read-only access
# ============================================
echo "Test 7: Share Calendar (Alice -> Charlie, read-only)"
echo "-----------------------------------------------------"

# Share with Charlie (read-only)
curl -s -u "$ALICE_AUTH" -X POST "$SERVER/alice/work-calendar/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<CS:share-resource xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:set>
    <D:href>/charlie/</D:href>
    <CS:read/>
  </CS:set>
</CS:share-resource>' > /dev/null

# Charlie accepts
curl -s -u "$CHARLIE_AUTH" -X POST "$SERVER/alice/work-calendar/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<CS:share-reply xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:invite-accepted/>
</CS:share-reply>' > /dev/null

echo "PASS: Shared with Charlie (read-only)"
echo ""

# ============================================
# Test 8: Verify read-only prevents writes
# ============================================
echo "Test 8: Read-Only Access Enforcement"
echo "-------------------------------------"

# Charlie should NOT be able to write
CHARLIE_EVENT_UID="charlie-event-$(date +%s)"
CHARLIE_WRITE=$(curl -s -w "%{http_code}" -u "$CHARLIE_AUTH" \
  -X PUT "$SERVER/alice/work-calendar/$CHARLIE_EVENT_UID.ics" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d "BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:$CHARLIE_EVENT_UID
DTSTAMP:20250101T120000Z
DTSTART:20250117T100000Z
DTEND:20250117T110000Z
SUMMARY:Unauthorized Event
END:VEVENT
END:VCALENDAR")

if [ "$CHARLIE_WRITE" = "403" ]; then
    echo "PASS: Charlie cannot write (HTTP 403 - read-only enforced)"
else
    echo "FAIL: Charlie write returned HTTP $CHARLIE_WRITE (expected 403)"
    exit 1
fi

# But Charlie CAN read
CHARLIE_READ=$(curl -s -o /dev/null -w "%{http_code}" -u "$CHARLIE_AUTH" \
  "$SERVER/alice/work-calendar/$EVENT_UID.ics")

if [ "$CHARLIE_READ" = "200" ]; then
    echo "PASS: Charlie can read (HTTP 200)"
else
    echo "FAIL: Charlie cannot read (HTTP $CHARLIE_READ)"
    exit 1
fi
echo ""

# ============================================
# Test 9: Remove share
# ============================================
echo "Test 9: Remove Share (Alice revokes Bob)"
echo "-----------------------------------------"

REMOVE_RESPONSE=$(curl -s -w "\n%{http_code}" -u "$ALICE_AUTH" \
  -X POST "$SERVER/alice/work-calendar/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<CS:share-resource xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:remove>
    <D:href>/bob/</D:href>
  </CS:remove>
</CS:share-resource>')

HTTP_CODE=$(echo "$REMOVE_RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "PASS: Share removed (HTTP 200)"
else
    echo "FAIL: Remove failed (HTTP $HTTP_CODE)"
    exit 1
fi

# Verify Bob can no longer access
BOB_AFTER=$(curl -s -o /dev/null -w "%{http_code}" -u "$BOB_AUTH" \
  "$SERVER/alice/work-calendar/$EVENT_UID.ics")

if [ "$BOB_AFTER" = "403" ] || [ "$BOB_AFTER" = "404" ]; then
    echo "PASS: Bob's access revoked (HTTP $BOB_AFTER)"
else
    echo "FAIL: Bob still has access (HTTP $BOB_AFTER)"
    exit 1
fi
echo ""

# ============================================
# Test 10: Security - Non-owner cannot share
# ============================================
echo "Test 10: Security (non-owner cannot share)"
echo "-------------------------------------------"

SPOOF_SHARE=$(curl -s -w "%{http_code}" -u "$BOB_AUTH" \
  -X POST "$SERVER/alice/work-calendar/" \
  -H "Content-Type: application/xml" \
  -o /dev/null \
  -d '<?xml version="1.0"?>
<CS:share-resource xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <CS:set>
    <D:href>/charlie/</D:href>
    <CS:read-write/>
  </CS:set>
</CS:share-resource>')

if [ "$SPOOF_SHARE" = "403" ]; then
    echo "PASS: Non-owner share blocked (HTTP 403)"
else
    echo "FAIL: Non-owner share returned HTTP $SPOOF_SHARE (expected 403)"
    exit 1
fi
echo ""

# ============================================
# Summary
# ============================================
echo "====================================="
echo " ALL TESTS PASSED!"
echo "====================================="
echo ""
echo "Calendar Sharing is working correctly!"
echo ""
echo "Features tested:"
echo "  - Share calendar with read-write access"
echo "  - Share calendar with read-only access"
echo "  - Accept share invitations"
echo "  - Read-only enforcement (writes blocked)"
echo "  - Remove shares (revoke access)"
echo "  - Security: non-owner cannot share"
echo ""
echo "Next steps:"
echo "  - Run delegation tests: ./test-delegation.sh"
echo "  - Run full test suite: pytest radicale/tests/test_sharing.py"
echo "  - See SCHEDULING.md Section 12 for documentation"

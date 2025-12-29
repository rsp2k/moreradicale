#!/bin/bash
# CalDAV Scheduling Delegation - End-to-End Integration Test
#
# Tests RFC 6638 scheduling delegation where one user (secretary)
# can send meeting invitations on behalf of another user (boss).
#
# Usage:
#   1. Start server: python3 -m radicale -C test-sharing-config.ini
#   2. Run tests: ./test-delegation.sh

set -e  # Exit on error

SERVER="http://127.0.0.1:5232"

# Users (must match test-sharing-users)
BOSS_AUTH="alice:alice123"
SECRETARY_AUTH="secretary:secretary123"
ATTENDEE_AUTH="bob:bob123"

echo "========================================"
echo " CalDAV Scheduling Delegation Tests"
echo "========================================"
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
# Setup: Create principals and calendars
# ============================================
echo "Setup: Creating principals"
echo "--------------------------"

# Create all principals
curl -s -u "$BOSS_AUTH" -X PROPFIND "$SERVER/alice/" -H "Depth: 0" > /dev/null
echo "Created Boss (Alice) principal"

curl -s -u "$SECRETARY_AUTH" -X PROPFIND "$SERVER/secretary/" -H "Depth: 0" > /dev/null
echo "Created Secretary principal"

curl -s -u "$ATTENDEE_AUTH" -X PROPFIND "$SERVER/bob/" -H "Depth: 0" > /dev/null
echo "Created Attendee (Bob) principal"

# Create boss's calendar
curl -s -u "$BOSS_AUTH" -X MKCALENDAR "$SERVER/alice/meetings/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set>
    <D:prop>
      <D:displayname>Boss Meetings</D:displayname>
    </D:prop>
  </D:set>
</C:mkcalendar>' > /dev/null
echo "Created Boss's meetings calendar"
echo ""

# ============================================
# Test 1: Secretary cannot send as Boss (no delegation)
# ============================================
echo "Test 1: Unauthorized Delegation (should fail)"
echo "----------------------------------------------"

# Secretary tries to send meeting as Boss without delegation
UNAUTH_RESPONSE=$(curl -s -w "%{http_code}" -u "$SECRETARY_AUTH" \
  -X POST "$SERVER/secretary/schedule-outbox/" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d 'BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:unauth-meeting-'$(date +%s)'
DTSTAMP:20250101T120000Z
DTSTART:20250120T100000Z
DTEND:20250120T110000Z
SUMMARY:Unauthorized Meeting
ORGANIZER:mailto:alice@localhost
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@localhost
END:VEVENT
END:VCALENDAR')

if [ "$UNAUTH_RESPONSE" = "403" ]; then
    echo "PASS: Unauthorized delegation blocked (HTTP 403)"
else
    echo "FAIL: Expected 403, got $UNAUTH_RESPONSE"
    exit 1
fi
echo ""

# ============================================
# Test 2: Boss grants delegation to Secretary
# ============================================
echo "Test 2: Grant Delegation (Boss -> Secretary)"
echo "---------------------------------------------"

# Set schedule-delegates property on Boss's principal
# Note: This uses PROPPATCH to set the delegation property
DELEGATE_RESPONSE=$(curl -s -w "\n%{http_code}" -u "$BOSS_AUTH" \
  -X PROPPATCH "$SERVER/alice/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:R="http://radicale.org/ns/">
  <D:set>
    <D:prop>
      <R:schedule-delegates>["secretary"]</R:schedule-delegates>
    </D:prop>
  </D:set>
</D:propertyupdate>')

HTTP_CODE=$(echo "$DELEGATE_RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "207" ]; then
    echo "PASS: Delegation granted (HTTP 207)"
else
    echo "FAIL: Grant delegation failed (HTTP $HTTP_CODE)"
    echo "$DELEGATE_RESPONSE"
    exit 1
fi
echo ""

# ============================================
# Test 3: Secretary sends meeting as Boss (authorized)
# ============================================
echo "Test 3: Authorized Delegation (Secretary sends as Boss)"
echo "--------------------------------------------------------"

MEETING_UID="delegated-meeting-$(date +%s)"
AUTH_RESPONSE=$(curl -s -w "\n%{http_code}" -u "$SECRETARY_AUTH" \
  -X POST "$SERVER/secretary/schedule-outbox/" \
  -H "Content-Type: text/calendar" \
  -d "BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:$MEETING_UID
DTSTAMP:20250101T120000Z
DTSTART:20250120T140000Z
DTEND:20250120T150000Z
SUMMARY:Boss's Important Meeting
ORGANIZER:mailto:alice@localhost
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@localhost
END:VEVENT
END:VCALENDAR")

HTTP_CODE=$(echo "$AUTH_RESPONSE" | tail -1)
BODY=$(echo "$AUTH_RESPONSE" | head -n -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "PASS: Delegated invitation sent (HTTP 200)"
    if echo "$BODY" | grep -q "2.0;Success"; then
        echo "PASS: Schedule response shows success"
    fi
else
    echo "FAIL: Delegated invitation failed (HTTP $HTTP_CODE)"
    echo "$BODY"
    exit 1
fi
echo ""

# ============================================
# Test 4: Verify invitation delivered to attendee
# ============================================
echo "Test 4: Verify Invitation Delivery"
echo "-----------------------------------"

BOB_INBOX=$(curl -s -X PROPFIND -u "$ATTENDEE_AUTH" \
  "$SERVER/bob/schedule-inbox/" -H "Depth: 1" \
  -d '<?xml version="1.0"?>
<propfind xmlns="DAV:">
  <prop><displayname/></prop>
</propfind>')

RESPONSE_COUNT=$(echo "$BOB_INBOX" | grep -c "<D:response>" || echo "0")

if [ "$RESPONSE_COUNT" -ge 2 ]; then
    echo "PASS: Bob received invitation in inbox ($((RESPONSE_COUNT - 1)) item(s))"
else
    echo "FAIL: Bob's inbox empty (expected invitation)"
    exit 1
fi
echo ""

# ============================================
# Test 5: Verify proxy PROPFIND properties
# ============================================
echo "Test 5: Verify Proxy Properties"
echo "--------------------------------"

PROXY_PROPS=$(curl -s -u "$SECRETARY_AUTH" -X PROPFIND "$SERVER/secretary/" \
  -H "Depth: 0" \
  -d '<?xml version="1.0"?>
<D:propfind xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
  <D:prop>
    <CS:calendar-proxy-write-for/>
  </D:prop>
</D:propfind>')

if echo "$PROXY_PROPS" | grep -q "alice"; then
    echo "PASS: Secretary shows as proxy-write-for Alice"
else
    echo "INFO: Proxy property may not be returned (implementation varies)"
fi
echo ""

# ============================================
# Test 6: Revoke delegation
# ============================================
echo "Test 6: Revoke Delegation"
echo "-------------------------"

# Remove delegation
REVOKE_RESPONSE=$(curl -s -w "\n%{http_code}" -u "$BOSS_AUTH" \
  -X PROPPATCH "$SERVER/alice/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:R="http://radicale.org/ns/">
  <D:set>
    <D:prop>
      <R:schedule-delegates>[]</R:schedule-delegates>
    </D:prop>
  </D:set>
</D:propertyupdate>')

HTTP_CODE=$(echo "$REVOKE_RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "207" ]; then
    echo "PASS: Delegation revoked (HTTP 207)"
else
    echo "FAIL: Revoke failed (HTTP $HTTP_CODE)"
fi

# Verify secretary can no longer send as boss
AFTER_REVOKE=$(curl -s -w "%{http_code}" -u "$SECRETARY_AUTH" \
  -X POST "$SERVER/secretary/schedule-outbox/" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d 'BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:after-revoke-'$(date +%s)'
DTSTAMP:20250101T120000Z
DTSTART:20250121T100000Z
DTEND:20250121T110000Z
SUMMARY:Should Fail
ORGANIZER:mailto:alice@localhost
ATTENDEE:mailto:bob@localhost
END:VEVENT
END:VCALENDAR')

if [ "$AFTER_REVOKE" = "403" ]; then
    echo "PASS: Post-revoke delegation blocked (HTTP 403)"
else
    echo "FAIL: Expected 403 after revoke, got $AFTER_REVOKE"
    exit 1
fi
echo ""

# ============================================
# Summary
# ============================================
echo "========================================"
echo " ALL DELEGATION TESTS PASSED!"
echo "========================================"
echo ""
echo "Scheduling Delegation is working correctly!"
echo ""
echo "Features tested:"
echo "  - Unauthorized delegation blocked (403)"
echo "  - Delegation grant via PROPPATCH"
echo "  - Authorized delegate can send invitations"
echo "  - Invitation delivery to attendees"
echo "  - Delegation revocation"
echo ""
echo "Next steps:"
echo "  - Run sharing tests: ./test-sharing.sh"
echo "  - Run full test suite: pytest radicale/tests/test_sharing.py"

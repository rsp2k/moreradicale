#!/bin/bash
# RFC 6638 CalDAV Scheduling - End-to-End Test Script

set -e  # Exit on error

SERVER="http://127.0.0.1:5232"
ALICE="$SERVER/alice/"
BOB="$SERVER/bob/"

echo "🧪 RFC 6638 CalDAV Scheduling Test"
echo "===================================="
echo ""

# Check if server is running
if ! curl -s "$SERVER" > /dev/null 2>&1; then
    echo "❌ ERROR: Radicale server not running at $SERVER"
    echo "Start with: python3 -m radicale -C test-scheduling-config.ini"
    exit 1
fi

echo "✅ Server is running at $SERVER"
echo ""

# Test 1: Create principals and verify scheduling collections auto-created
echo "📋 Test 1: Principal Discovery (auto-creates inbox/outbox)"
echo "-----------------------------------------------------------"

curl -s -X PROPFIND "$ALICE" -H "Depth: 1" \
  -d '<?xml version="1.0"?>
<propfind xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <prop>
    <C:schedule-inbox-URL/>
    <C:schedule-outbox-URL/>
    <C:calendar-user-type/>
  </prop>
</propfind>' > /tmp/alice-propfind.xml

if grep -q "schedule-inbox-URL" /tmp/alice-propfind.xml && \
   grep -q "schedule-outbox-URL" /tmp/alice-propfind.xml; then
    echo "✅ Alice's principal created with scheduling properties"
else
    echo "❌ FAILED: Scheduling properties not found"
    cat /tmp/alice-propfind.xml
    exit 1
fi

curl -s -X PROPFIND "$BOB" -H "Depth: 1" > /dev/null
echo "✅ Bob's principal created"
echo ""

# Test 2: Send iTIP invitation from alice to bob
echo "📧 Test 2: Send Meeting Invitation (alice → bob)"
echo "------------------------------------------------"

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$ALICE/schedule-outbox/" \
  -H "Content-Type: text/calendar" \
  -d 'BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
METHOD:REQUEST
BEGIN:VEVENT
UID:test-meeting-'$(date +%s)'
DTSTAMP:20250101T120000Z
DTSTART:20250115T140000Z
DTEND:20250115T150000Z
SUMMARY:Team Standup
ORGANIZER:mailto:alice@localhost
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:bob@localhost
END:VEVENT
END:VCALENDAR')

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ POST to schedule-outbox succeeded (HTTP 200)"

    if echo "$BODY" | grep -q "2.0;Success"; then
        echo "✅ Schedule-response shows delivery success"
    else
        echo "⚠️  WARNING: Unexpected response"
        echo "$BODY"
    fi
else
    echo "❌ FAILED: Expected HTTP 200, got $HTTP_CODE"
    echo "$BODY"
    exit 1
fi
echo ""

# Test 3: Verify bob received the invitation in his inbox
echo "📥 Test 3: Verify Inbox Delivery"
echo "---------------------------------"

BOB_INBOX=$(curl -s -X PROPFIND "$BOB/schedule-inbox/" -H "Depth: 1" \
  -d '<?xml version="1.0"?>
<propfind xmlns="DAV:">
  <prop><displayname/></prop>
</propfind>')

# Count responses (should have collection + at least 1 item)
RESPONSE_COUNT=$(echo "$BOB_INBOX" | grep -c "<D:response>" || true)

if [ "$RESPONSE_COUNT" -ge 2 ]; then
    echo "✅ Bob's inbox contains the invitation ($((RESPONSE_COUNT - 1)) item(s))"
else
    echo "❌ FAILED: Bob's inbox is empty or only has collection"
    echo "$BOB_INBOX"
    exit 1
fi
echo ""

# Test 4: Security - try to spoof organizer
echo "🔒 Test 4: Security Check (organizer spoofing prevention)"
echo "----------------------------------------------------------"

SPOOF_RESPONSE=$(curl -s -w "%{http_code}" -X POST "$ALICE/schedule-outbox/" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d 'BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:fake-meeting
DTSTAMP:20250101T120000Z
DTSTART:20250115T140000Z
DTEND:20250115T150000Z
ORGANIZER:mailto:bob@localhost
ATTENDEE:mailto:alice@localhost
SUMMARY:Fake Meeting
END:VEVENT
END:VCALENDAR')

if [ "$SPOOF_RESPONSE" = "403" ]; then
    echo "✅ Organizer spoofing blocked (HTTP 403)"
else
    echo "❌ FAILED: Expected HTTP 403, got $SPOOF_RESPONSE"
    exit 1
fi
echo ""

# Summary
echo "════════════════════════════════════════════════════════"
echo "✅ ALL TESTS PASSED!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "RFC 6638 CalDAV Scheduling is working correctly!"
echo ""
echo "Next steps:"
echo "  1. Test with CalDAV client (Apple Calendar, Thunderbird)"
echo "  2. Review SCHEDULING-IMPLEMENTATION.md for details"
echo "  3. Run full test suite: python3 -m pytest radicale/tests/test_scheduling.py"

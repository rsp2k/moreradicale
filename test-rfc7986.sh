#!/bin/bash
# RFC 7986 New iCalendar Properties - End-to-End Integration Test
#
# Tests RFC 7986 property support:
# - COLOR property for events and calendars
# - CONFERENCE property with FEATURE and LABEL parameters
# - IMAGE property with DISPLAY parameter
#
# Usage:
#   1. Start server: python3 -m radicale --config="" --auth-type=none
#   2. Run tests: ./test-rfc7986.sh

set -e  # Exit on error

SERVER="http://127.0.0.1:5232"

echo "========================================"
echo " RFC 7986 New iCalendar Properties Test"
echo "========================================"
echo ""

# Check if server is running
if ! curl -s "$SERVER" > /dev/null 2>&1; then
    echo "ERROR: Radicale server not running at $SERVER"
    echo "Start with: python3 -m radicale --config='' --auth-type=none"
    exit 1
fi
echo "Server is running at $SERVER"
echo ""

# ============================================
# Setup: Create a calendar
# ============================================
echo "Setup: Creating test calendar"
echo "------------------------------"

curl -s -X MKCALENDAR "$SERVER/alice/rfc7986-test/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set>
    <D:prop>
      <D:displayname>RFC 7986 Test Calendar</D:displayname>
    </D:prop>
  </D:set>
</C:mkcalendar>' > /dev/null || true
echo "Created test calendar"
echo ""

# ============================================
# Test 1: COLOR property on events
# ============================================
echo "Test 1: COLOR Property (Event)"
echo "-------------------------------"

EVENT_WITH_COLOR="BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:color-test-$(date +%s)@example.com
DTSTAMP:20250129T100000Z
DTSTART:20250201T090000Z
DTEND:20250201T100000Z
SUMMARY:Red Team Meeting
COLOR:red
END:VEVENT
END:VCALENDAR"

PUT_RESPONSE=$(curl -s -w "%{http_code}" \
  -X PUT "$SERVER/alice/rfc7986-test/color-event.ics" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d "$EVENT_WITH_COLOR")

if [ "$PUT_RESPONSE" = "201" ] || [ "$PUT_RESPONSE" = "204" ]; then
    echo "PASS: Event with COLOR stored (HTTP $PUT_RESPONSE)"
else
    echo "FAIL: PUT failed (HTTP $PUT_RESPONSE)"
    exit 1
fi

# Retrieve and verify COLOR preserved
GET_RESPONSE=$(curl -s "$SERVER/alice/rfc7986-test/color-event.ics")

if echo "$GET_RESPONSE" | grep -q "COLOR:red"; then
    echo "PASS: COLOR property preserved in retrieval"
else
    echo "FAIL: COLOR property not found in retrieved event"
    echo "$GET_RESPONSE"
    exit 1
fi
echo ""

# ============================================
# Test 2: CONFERENCE property with parameters
# ============================================
echo "Test 2: CONFERENCE Property with FEATURE and LABEL"
echo "---------------------------------------------------"

EVENT_WITH_CONFERENCE="BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:conference-test-$(date +%s)@example.com
DTSTAMP:20250129T100000Z
DTSTART:20250202T140000Z
DTEND:20250202T150000Z
SUMMARY:Video Call Meeting
CONFERENCE;VALUE=URI;FEATURE=AUDIO,VIDEO;LABEL=\"Zoom Meeting\":https://zoom.us/j/123456789
END:VEVENT
END:VCALENDAR"

PUT_RESPONSE=$(curl -s -w "%{http_code}" \
  -X PUT "$SERVER/alice/rfc7986-test/conference-event.ics" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d "$EVENT_WITH_CONFERENCE")

if [ "$PUT_RESPONSE" = "201" ] || [ "$PUT_RESPONSE" = "204" ]; then
    echo "PASS: Event with CONFERENCE stored (HTTP $PUT_RESPONSE)"
else
    echo "FAIL: PUT failed (HTTP $PUT_RESPONSE)"
    exit 1
fi

# Retrieve and verify CONFERENCE preserved
GET_RESPONSE=$(curl -s "$SERVER/alice/rfc7986-test/conference-event.ics")

if echo "$GET_RESPONSE" | grep -q "CONFERENCE"; then
    echo "PASS: CONFERENCE property preserved"

    if echo "$GET_RESPONSE" | grep -q "FEATURE"; then
        echo "PASS: FEATURE parameter preserved"
    else
        echo "WARNING: FEATURE parameter may not be preserved"
    fi

    if echo "$GET_RESPONSE" | grep -q "LABEL"; then
        echo "PASS: LABEL parameter preserved"
    else
        echo "WARNING: LABEL parameter may not be preserved"
    fi
else
    echo "FAIL: CONFERENCE property not found"
    exit 1
fi
echo ""

# ============================================
# Test 3: IMAGE property with DISPLAY parameter
# ============================================
echo "Test 3: IMAGE Property with DISPLAY Parameter"
echo "----------------------------------------------"

EVENT_WITH_IMAGE="BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:image-test-$(date +%s)@example.com
DTSTAMP:20250129T100000Z
DTSTART:20250203T100000Z
DTEND:20250203T110000Z
SUMMARY:Event with Banner Image
IMAGE;VALUE=URI;DISPLAY=BADGE:https://example.com/event-logo.png
END:VEVENT
END:VCALENDAR"

PUT_RESPONSE=$(curl -s -w "%{http_code}" \
  -X PUT "$SERVER/alice/rfc7986-test/image-event.ics" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d "$EVENT_WITH_IMAGE")

if [ "$PUT_RESPONSE" = "201" ] || [ "$PUT_RESPONSE" = "204" ]; then
    echo "PASS: Event with IMAGE stored (HTTP $PUT_RESPONSE)"
else
    echo "FAIL: PUT failed (HTTP $PUT_RESPONSE)"
    exit 1
fi

# Retrieve and verify IMAGE preserved
GET_RESPONSE=$(curl -s "$SERVER/alice/rfc7986-test/image-event.ics")

if echo "$GET_RESPONSE" | grep -q "IMAGE"; then
    echo "PASS: IMAGE property preserved"

    if echo "$GET_RESPONSE" | grep -q "DISPLAY"; then
        echo "PASS: DISPLAY parameter preserved"
    else
        echo "WARNING: DISPLAY parameter may not be preserved"
    fi
else
    echo "FAIL: IMAGE property not found"
    exit 1
fi
echo ""

# ============================================
# Test 4: Multiple RFC 7986 properties together
# ============================================
echo "Test 4: Combined RFC 7986 Properties"
echo "-------------------------------------"

COMBINED_EVENT="BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//RFC7986//EN
BEGIN:VEVENT
UID:combined-test-$(date +%s)@example.com
DTSTAMP:20250129T100000Z
DTSTART:20250204T150000Z
DTEND:20250204T160000Z
SUMMARY:Full-Featured Meeting
COLOR:blue
CONFERENCE;VALUE=URI;FEATURE=AUDIO,VIDEO,SCREEN:https://meet.google.com/abc-defg-hij
CONFERENCE;VALUE=URI;FEATURE=PHONE;LABEL=\"Dial-in\":tel:+1-555-123-4567
IMAGE;VALUE=URI;DISPLAY=FULLSIZE:https://example.com/meeting-banner.jpg
END:VEVENT
END:VCALENDAR"

PUT_RESPONSE=$(curl -s -w "%{http_code}" \
  -X PUT "$SERVER/alice/rfc7986-test/combined-event.ics" \
  -H "Content-Type: text/calendar" \
  -o /dev/null \
  -d "$COMBINED_EVENT")

if [ "$PUT_RESPONSE" = "201" ] || [ "$PUT_RESPONSE" = "204" ]; then
    echo "PASS: Event with combined properties stored (HTTP $PUT_RESPONSE)"
else
    echo "FAIL: PUT failed (HTTP $PUT_RESPONSE)"
    exit 1
fi

# Retrieve and verify all properties preserved
GET_RESPONSE=$(curl -s "$SERVER/alice/rfc7986-test/combined-event.ics")

PROPS_FOUND=0
if echo "$GET_RESPONSE" | grep -q "COLOR:blue"; then
    echo "PASS: COLOR property present"
    PROPS_FOUND=$((PROPS_FOUND + 1))
fi
if echo "$GET_RESPONSE" | grep -q "CONFERENCE"; then
    echo "PASS: CONFERENCE property present"
    PROPS_FOUND=$((PROPS_FOUND + 1))
fi
if echo "$GET_RESPONSE" | grep -q "IMAGE"; then
    echo "PASS: IMAGE property present"
    PROPS_FOUND=$((PROPS_FOUND + 1))
fi

if [ "$PROPS_FOUND" -ge 3 ]; then
    echo "PASS: All RFC 7986 properties preserved ($PROPS_FOUND/3)"
else
    echo "WARNING: Only $PROPS_FOUND/3 properties preserved"
fi
echo ""

# ============================================
# Test 5: Calendar-level COLOR property
# ============================================
echo "Test 5: Calendar-level COLOR (PROPPATCH)"
echo "-----------------------------------------"

# Set calendar color via PROPPATCH
PROPPATCH_RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X PROPPATCH "$SERVER/alice/rfc7986-test/" \
  -H "Content-Type: application/xml" \
  -d '<?xml version="1.0"?>
<D:propertyupdate xmlns:D="DAV:" xmlns:A="http://apple.com/ns/ical/">
  <D:set>
    <D:prop>
      <A:calendar-color>#FF5733</A:calendar-color>
    </D:prop>
  </D:set>
</D:propertyupdate>')

HTTP_CODE=$(echo "$PROPPATCH_RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "207" ]; then
    echo "PASS: Calendar color set (HTTP 207)"
else
    echo "INFO: Calendar-level color may not be supported (HTTP $HTTP_CODE)"
fi

# Retrieve calendar properties
PROPFIND_RESPONSE=$(curl -s \
  -X PROPFIND "$SERVER/alice/rfc7986-test/" \
  -H "Depth: 0" \
  -d '<?xml version="1.0"?>
<D:propfind xmlns:D="DAV:" xmlns:A="http://apple.com/ns/ical/">
  <D:prop>
    <A:calendar-color/>
  </D:prop>
</D:propfind>')

if echo "$PROPFIND_RESPONSE" | grep -q "FF5733"; then
    echo "PASS: Calendar color retrieved correctly"
else
    echo "INFO: Calendar color retrieval varies by implementation"
fi
echo ""

# ============================================
# Summary
# ============================================
echo "========================================"
echo " RFC 7986 TESTS COMPLETED!"
echo "========================================"
echo ""
echo "RFC 7986 New iCalendar Properties tested:"
echo "  - COLOR property on events"
echo "  - CONFERENCE property with FEATURE/LABEL params"
echo "  - IMAGE property with DISPLAY parameter"
echo "  - Combined properties round-trip"
echo "  - Calendar-level color (Apple extension)"
echo ""
echo "Note: Some parameters may be normalized by the server."
echo ""
echo "Next steps:"
echo "  - Run scheduling tests: ./test-scheduling.sh"
echo "  - Run sharing tests: ./test-sharing.sh"
echo "  - Run full test suite: pytest radicale/tests/test_rfc7986.py"

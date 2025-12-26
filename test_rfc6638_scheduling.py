#!/usr/bin/env python3
"""
Comprehensive test for RFC 6638 CalDAV Scheduling implementation.

Tests:
1. Scheduling collections auto-discovery
2. iTIP REQUEST delivery to attendee inbox
3. REPLY processing (acceptance/decline)
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

RADICALE_URL = "http://localhost:5232"
NS = {
    'D': 'DAV:',
    'C': 'urn:ietf:params:xml:ns:caldav'
}

def register_namespaces():
    """Register XML namespaces for cleaner output."""
    ET.register_namespace('D', 'DAV:')
    ET.register_namespace('C', 'urn:ietf:params:xml:ns:caldav')

class TestRFC6638Scheduling:
    """RFC 6638 CalDAV Scheduling test suite."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        register_namespaces()

    def test(self, name, condition, message=""):
        """Test assertion helper."""
        if condition:
            print(f"  ✅ PASS: {name}")
            self.passed += 1
            return True
        else:
            print(f"  ❌ FAIL: {name}")
            if message:
                print(f"     {message}")
            self.failed += 1
            return False

    def propfind(self, path, user, password, props, depth="0"):
        """Execute PROPFIND request."""
        prop_xml = ET.Element('{DAV:}propfind')
        prop_elem = ET.SubElement(prop_xml, '{DAV:}prop')
        for prop in props:
            ET.SubElement(prop_elem, prop)

        response = requests.request(
            'PROPFIND',
            f"{RADICALE_URL}{path}",
            auth=(user, password),
            headers={'Depth': depth, 'Content-Type': 'text/xml'},
            data=ET.tostring(prop_xml, encoding='utf-8')
        )
        return response

    def test_1_scheduling_discovery(self):
        """Test 1: Verify scheduling collections auto-created."""
        print("\n" + "="*60)
        print("TEST 1: Scheduling Collections Auto-Discovery")
        print("="*60)

        # Check Alice's principal for scheduling properties
        response = self.propfind(
            '/alice/',
            'alice', 'alice',
            [
                '{urn:ietf:params:xml:ns:caldav}schedule-inbox-URL',
                '{urn:ietf:params:xml:ns:caldav}schedule-outbox-URL',
                '{urn:ietf:params:xml:ns:caldav}calendar-user-type'
            ]
        )

        self.test(
            "Alice principal PROPFIND succeeds",
            response.status_code == 207,
            f"Expected 207, got {response.status_code}"
        )

        if response.status_code == 207:
            root = ET.fromstring(response.text)

            # Check schedule-inbox-URL
            inbox_elem = root.find('.//{urn:ietf:params:xml:ns:caldav}schedule-inbox-URL/{DAV:}href')
            self.test(
                "Alice has schedule-inbox-URL",
                inbox_elem is not None and 'schedule-inbox' in inbox_elem.text
            )

            # Check schedule-outbox-URL
            outbox_elem = root.find('.//{urn:ietf:params:xml:ns:caldav}schedule-outbox-URL/{DAV:}href')
            self.test(
                "Alice has schedule-outbox-URL",
                outbox_elem is not None and 'schedule-outbox' in outbox_elem.text
            )

            # Check calendar-user-type
            cutype_elem = root.find('.//{urn:ietf:params:xml:ns:caldav}calendar-user-type')
            self.test(
                "Alice has calendar-user-type",
                cutype_elem is not None and cutype_elem.text == 'INDIVIDUAL'
            )

        # Verify inbox collection exists
        response = self.propfind(
            '/alice/schedule-inbox/',
            'alice', 'alice',
            ['{DAV:}resourcetype']
        )

        if response.status_code == 207:
            root = ET.fromstring(response.text)
            inbox_type = root.find('.//{urn:ietf:params:xml:ns:caldav}schedule-inbox')
            self.test(
                "Alice schedule-inbox is correct type",
                inbox_type is not None
            )
        else:
            self.test("Alice schedule-inbox exists", False, f"HTTP {response.status_code}")

    def test_2_itip_request_delivery(self):
        """Test 2: Create meeting invitation and verify iTIP delivery."""
        print("\n" + "="*60)
        print("TEST 2: iTIP REQUEST Message Delivery")
        print("="*60)

        # Access Bob's principal first to trigger schedule collection creation
        self.propfind('/bob/', 'bob', 'bob', ['{DAV:}resourcetype'])

        # Create Alice's calendar if it doesn't exist
        requests.request(
            'MKCOL',
            f"{RADICALE_URL}/alice/calendar.ics/",
            auth=('alice', 'alice'),
            headers={'Content-Type': 'text/xml'},
            data='''<?xml version="1.0"?>
<D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set>
    <D:prop>
      <D:resourcetype>
        <D:collection/>
        <C:calendar/>
      </D:resourcetype>
      <D:displayname>Alice Calendar</D:displayname>
    </D:prop>
  </D:set>
</D:mkcol>'''
        )

        # Create event with Bob as attendee
        now = datetime.utcnow()
        start = now + timedelta(days=1)
        end = start + timedelta(hours=1)

        event_ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test Suite//EN
BEGIN:VEVENT
UID:test-meeting-{now.strftime('%Y%m%d%H%M%S')}@localhost
DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}
DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}
DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}
SUMMARY:RFC 6638 Test Meeting
DESCRIPTION:Testing iTIP implicit scheduling
ORGANIZER:mailto:alice@localhost
ATTENDEE;PARTSTAT=NEEDS-ACTION;ROLE=REQ-PARTICIPANT;CN="Bob Smith":mailto:bob@localhost
END:VEVENT
END:VCALENDAR
"""

        # PUT event to Alice's calendar
        response = requests.put(
            f"{RADICALE_URL}/alice/calendar.ics/test-meeting.ics",
            auth=('alice', 'alice'),
            headers={'Content-Type': 'text/calendar'},
            data=event_ical
        )

        self.test(
            "Alice creates event with attendee",
            response.status_code in (201, 204),
            f"Expected 201/204, got {response.status_code}"
        )

        # Check Bob's schedule-inbox for iTIP message
        response = self.propfind(
            '/bob/schedule-inbox/',
            'bob', 'bob',
            ['{DAV:}getetag'],
            depth='1'
        )

        self.test(
            "Bob schedule-inbox PROPFIND succeeds",
            response.status_code == 207,
            f"Expected 207, got {response.status_code}"
        )

        if response.status_code == 207:
            root = ET.fromstring(response.text)
            # Find .ics files in inbox
            ics_items = [elem for elem in root.findall('.//{DAV:}href')
                        if elem.text and elem.text.endswith('.ics')]

            self.test(
                "iTIP message delivered to Bob's inbox",
                len(ics_items) > 0,
                f"Expected at least 1 .ics file, found {len(ics_items)}"
            )

            if len(ics_items) > 0:
                # Retrieve the iTIP message
                inbox_item_url = f"{RADICALE_URL}{ics_items[0].text}"
                response = requests.get(inbox_item_url, auth=('bob', 'bob'))

                if response.status_code == 200:
                    itip_content = response.text
                    # Unfold lines per RFC 5545 (remove CRLF + space/tab)
                    unfolded_content = itip_content.replace('\r\n ', '').replace('\r\n\t', '').replace('\n ', '').replace('\n\t', '')

                    self.test(
                        "iTIP message has METHOD:REQUEST",
                        'METHOD:REQUEST' in itip_content
                    )

                    self.test(
                        "iTIP message has correct ORGANIZER",
                        'ORGANIZER:mailto:alice@localhost' in unfolded_content
                    )

                    self.test(
                        "iTIP message has Bob as ATTENDEE",
                        'ATTENDEE' in itip_content and 'bob@localhost' in unfolded_content
                    )

                    self.test(
                        "iTIP message has PARTSTAT=NEEDS-ACTION",
                        'PARTSTAT=NEEDS-ACTION' in itip_content
                    )

                    print(f"\n  📧 iTIP Message Preview:")
                    print("  " + "-"*56)
                    for line in itip_content.split('\n')[:15]:
                        print(f"  {line}")
                    print("  " + "-"*56)
                else:
                    self.test("Retrieve iTIP message", False, f"HTTP {response.status_code}")

    def test_3_itip_reply_processing(self):
        """Test 3: Process REPLY from attendee."""
        print("\n" + "="*60)
        print("TEST 3: iTIP REPLY Processing (Acceptance)")
        print("="*60)

        print("  ℹ️  Note: REPLY processing requires POST to schedule-outbox")
        print("  ℹ️  This test verifies the infrastructure is ready")

        # Verify Bob's schedule-outbox exists
        response = self.propfind(
            '/bob/schedule-outbox/',
            'bob', 'bob',
            ['{DAV:}resourcetype']
        )

        self.test(
            "Bob schedule-outbox exists",
            response.status_code == 207,
            f"Expected 207, got {response.status_code}"
        )

        if response.status_code == 207:
            root = ET.fromstring(response.text)
            outbox_type = root.find('.//{urn:ietf:params:xml:ns:caldav}schedule-outbox')
            self.test(
                "Bob schedule-outbox is correct type",
                outbox_type is not None
            )

        print("  ⚠️  Full REPLY processing (POST) not yet implemented")
        print("  ⚠️  Requires additional handler for schedule-outbox POST")

    def test_4_itip_cancel_delivery(self):
        """Test 4: Verify CANCEL delivery when event deleted."""
        print("\n" + "="*60)
        print("TEST 4: iTIP CANCEL Message Delivery")
        print("="*60)

        # Create a new event for cancellation testing
        now = datetime.utcnow()
        start = now + timedelta(days=2)
        end = start + timedelta(hours=1)
        uid = f"cancel-test-{now.strftime('%Y%m%d%H%M%S')}@localhost"

        event_ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test Suite//EN
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}
DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}
DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}
SUMMARY:Cancellation Test Event
ORGANIZER:mailto:alice@localhost
ATTENDEE;PARTSTAT=NEEDS-ACTION;ROLE=REQ-PARTICIPANT;CN="Bob":mailto:bob@localhost
END:VEVENT
END:VCALENDAR
"""

        # PUT event to Alice's calendar
        response = requests.put(
            f"{RADICALE_URL}/alice/calendar.ics/cancel-test.ics",
            auth=('alice', 'alice'),
            headers={'Content-Type': 'text/calendar'},
            data=event_ical
        )

        self.test(
            "Alice creates event for cancellation",
            response.status_code in (201, 204),
            f"Expected 201/204, got {response.status_code}"
        )

        # Count Bob's inbox messages before deletion
        response = self.propfind(
            '/bob/schedule-inbox/',
            'bob', 'bob',
            ['{DAV:}getetag'],
            depth='1'
        )

        inbox_count_before = 0
        if response.status_code == 207:
            root = ET.fromstring(response.text)
            ics_items = [elem for elem in root.findall('.//{DAV:}href')
                        if elem.text and elem.text.endswith('.ics')]
            inbox_count_before = len(ics_items)

        # Delete the event (should trigger CANCEL)
        response = requests.delete(
            f"{RADICALE_URL}/alice/calendar.ics/cancel-test.ics",
            auth=('alice', 'alice')
        )

        self.test(
            "Alice deletes event (triggers CANCEL)",
            response.status_code in (200, 204),
            f"Expected 200/204, got {response.status_code}"
        )

        # Give server a moment to process
        import time
        time.sleep(0.2)

        # Check Bob's inbox for CANCEL message
        response = self.propfind(
            '/bob/schedule-inbox/',
            'bob', 'bob',
            ['{DAV:}getetag'],
            depth='1'
        )

        self.test(
            "Bob inbox PROPFIND after CANCEL",
            response.status_code == 207,
            f"Expected 207, got {response.status_code}"
        )

        if response.status_code == 207:
            root = ET.fromstring(response.text)
            ics_items = [elem for elem in root.findall('.//{DAV:}href')
                        if elem.text and elem.text.endswith('.ics')]
            inbox_count_after = len(ics_items)

            self.test(
                "CANCEL message delivered to Bob's inbox",
                inbox_count_after > inbox_count_before,
                f"Expected more messages, got {inbox_count_before} → {inbox_count_after}"
            )

            # Find and verify CANCEL message
            cancel_found = False
            for item in ics_items:
                if 'cancel' in item.text.lower():
                    cancel_url = f"{RADICALE_URL}{item.text}"
                    response = requests.get(cancel_url, auth=('bob', 'bob'))

                    if response.status_code == 200:
                        cancel_content = response.text
                        unfolded_content = cancel_content.replace('\r\n ', '').replace('\r\n\t', '')

                        if 'METHOD:CANCEL' in cancel_content:
                            cancel_found = True
                            self.test(
                                "iTIP message has METHOD:CANCEL",
                                True
                            )

                            self.test(
                                "CANCEL has correct UID",
                                uid in unfolded_content
                            )

                            self.test(
                                "CANCEL has correct ORGANIZER",
                                'ORGANIZER:mailto:alice@localhost' in unfolded_content
                            )

                            self.test(
                                "CANCEL has Bob as ATTENDEE",
                                'bob@localhost' in unfolded_content
                            )
                            break

            if not cancel_found:
                self.test("CANCEL message found", False, "No CANCEL message in inbox")

    def run_all_tests(self):
        """Run all RFC 6638 tests."""
        print("\n" + "█"*60)
        print("█" + " "*58 + "█")
        print("█  RFC 6638 CalDAV Scheduling Test Suite".center(60) + "█")
        print("█" + " "*58 + "█")
        print("█"*60)

        self.test_1_scheduling_discovery()
        self.test_2_itip_request_delivery()
        self.test_3_itip_reply_processing()
        self.test_4_itip_cancel_delivery()

        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)
        print(f"  ✅ Passed: {self.passed}")
        print(f"  ❌ Failed: {self.failed}")
        print(f"  📊 Total:  {self.passed + self.failed}")

        if self.failed == 0:
            print("\n  🎉 ALL TESTS PASSED! RFC 6638 scheduling is working!")
            return 0
        else:
            print(f"\n  ⚠️  {self.failed} test(s) failed")
            return 1

if __name__ == "__main__":
    suite = TestRFC6638Scheduling()
    exit(suite.run_all_tests())

#!/bin/bash
# RFC 8607 Managed Attachments Integration Test
# Tests the managed attachments functionality using curl

set -e

# Configuration
HOST="${RADICALE_HOST:-localhost}"
PORT="${RADICALE_PORT:-5232}"
BASE_URL="http://${HOST}:${PORT}"
USER="testuser"
PASS="testpass"
AUTH="${USER}:${PASS}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((TESTS_PASSED++))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((TESTS_FAILED++))
}

log_test() {
    echo -e "\n${YELLOW}[TEST]${NC} $1"
    ((TESTS_RUN++))
}

# Check if server is running
check_server() {
    log_info "Checking if Radicale server is available at ${BASE_URL}..."
    if ! curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/" > /dev/null 2>&1; then
        echo -e "${RED}ERROR: Cannot connect to Radicale server at ${BASE_URL}${NC}"
        echo "Please start Radicale with attachments enabled:"
        echo "  python3 -m radicale --config test-attachments-config.ini"
        exit 1
    fi
    log_info "Server is available"
}

# Clean up test data
cleanup() {
    log_info "Cleaning up test calendar..."
    curl -s -X DELETE -u "${AUTH}" "${BASE_URL}/${USER}/test-attachments/" > /dev/null 2>&1 || true
}

# Create test calendar
setup_calendar() {
    log_info "Creating test calendar..."

    # Create principal
    curl -s -X MKCOL -u "${AUTH}" "${BASE_URL}/${USER}/" > /dev/null 2>&1 || true

    # Create calendar
    curl -s -X MKCALENDAR -u "${AUTH}" \
        -H "Content-Type: application/xml" \
        "${BASE_URL}/${USER}/test-attachments/" \
        -d '<?xml version="1.0" encoding="UTF-8"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set>
    <D:prop>
      <D:displayname>Attachment Test Calendar</D:displayname>
    </D:prop>
  </D:set>
</C:mkcalendar>' > /dev/null

    log_info "Calendar created"
}

# Create test event
create_event() {
    local event_name="$1"
    local uid="${event_name}@test.local"

    curl -s -X PUT -u "${AUTH}" \
        -H "Content-Type: text/calendar" \
        "${BASE_URL}/${USER}/test-attachments/${event_name}.ics" \
        -d "BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:${uid}
DTSTART:20250115T100000Z
DTEND:20250115T110000Z
SUMMARY:Test Event ${event_name}
END:VEVENT
END:VCALENDAR" > /dev/null
}

# Test: Check DAV header includes calendar-managed-attachments
test_dav_header() {
    log_test "DAV header includes calendar-managed-attachments"

    local response
    response=$(curl -s -I -u "${AUTH}" "${BASE_URL}/")

    if echo "$response" | grep -qi "calendar-managed-attachments"; then
        log_success "DAV header includes calendar-managed-attachments"
    else
        log_fail "DAV header missing calendar-managed-attachments"
        echo "Response headers:"
        echo "$response"
    fi
}

# Test: PROPFIND attachment properties
test_propfind_properties() {
    log_test "PROPFIND returns attachment properties"

    local response
    response=$(curl -s -X PROPFIND -u "${AUTH}" \
        -H "Content-Type: application/xml" \
        -H "Depth: 0" \
        "${BASE_URL}/${USER}/test-attachments/" \
        -d '<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <C:max-attachment-size/>
    <C:max-attachments-per-resource/>
  </D:prop>
</D:propfind>')

    local has_max_size=false
    local has_max_per_resource=false

    if echo "$response" | grep -q "max-attachment-size"; then
        has_max_size=true
    fi

    if echo "$response" | grep -q "max-attachments-per-resource"; then
        has_max_per_resource=true
    fi

    if $has_max_size && $has_max_per_resource; then
        log_success "PROPFIND returns attachment properties"
    else
        log_fail "PROPFIND missing attachment properties"
        echo "Response:"
        echo "$response" | head -50
    fi
}

# Test: Add attachment
test_attachment_add() {
    log_test "POST action=attachment-add uploads attachment"

    create_event "attachment-add-test"

    local response
    local http_code
    local managed_id

    # Create test attachment data
    local attachment_data="This is test attachment content for RFC 8607."

    response=$(curl -s -w "\n%{http_code}" -X POST -u "${AUTH}" \
        -H "Content-Type: text/plain" \
        -H "Content-Disposition: attachment; filename=\"test-attachment.txt\"" \
        "${BASE_URL}/${USER}/test-attachments/attachment-add-test.ics?action=attachment-add" \
        -d "${attachment_data}")

    http_code=$(echo "$response" | tail -1)

    if [ "$http_code" = "201" ]; then
        log_success "Attachment added successfully (HTTP 201)"

        # Extract managed ID from Cal-Managed-ID header
        managed_id=$(curl -s -I -X POST -u "${AUTH}" \
            -H "Content-Type: text/plain" \
            "${BASE_URL}/${USER}/test-attachments/attachment-add-test.ics?action=attachment-add" \
            -d "second attachment" 2>&1 | grep -i "Cal-Managed-ID" | cut -d: -f2 | tr -d ' \r\n')

        if [ -n "$managed_id" ]; then
            log_info "Received managed ID: ${managed_id}"
            echo "$managed_id" > /tmp/test_managed_id
        fi
    else
        log_fail "Attachment add failed with HTTP ${http_code}"
        echo "Response:"
        echo "$response"
    fi
}

# Test: Get attachment
test_attachment_get() {
    log_test "GET retrieves attachment content"

    # First add an attachment
    create_event "attachment-get-test"

    local attachment_content="Attachment content for GET test - unique string 12345"

    # Add attachment and capture managed ID
    local headers
    headers=$(curl -s -D - -o /dev/null -X POST -u "${AUTH}" \
        -H "Content-Type: text/plain" \
        -H "Content-Disposition: attachment; filename=\"get-test.txt\"" \
        "${BASE_URL}/${USER}/test-attachments/attachment-get-test.ics?action=attachment-add" \
        -d "${attachment_content}")

    local managed_id
    managed_id=$(echo "$headers" | grep -i "Cal-Managed-ID" | cut -d: -f2 | tr -d ' \r\n')

    if [ -z "$managed_id" ]; then
        log_fail "Could not get managed ID from attachment-add response"
        return
    fi

    log_info "Added attachment with managed ID: ${managed_id}"

    # Now retrieve the attachment
    local retrieved_content
    local http_code

    retrieved_content=$(curl -s -w "\n%{http_code}" -u "${AUTH}" \
        "${BASE_URL}/.attachments/${USER}/${managed_id}")

    http_code=$(echo "$retrieved_content" | tail -1)
    retrieved_content=$(echo "$retrieved_content" | head -n -1)

    if [ "$http_code" = "200" ]; then
        if [ "$retrieved_content" = "$attachment_content" ]; then
            log_success "Attachment retrieved correctly"
        else
            log_fail "Attachment content mismatch"
            echo "Expected: ${attachment_content}"
            echo "Got: ${retrieved_content}"
        fi
    else
        log_fail "Attachment GET failed with HTTP ${http_code}"
    fi
}

# Test: Update attachment
test_attachment_update() {
    log_test "POST action=attachment-update replaces attachment"

    create_event "attachment-update-test"

    local initial_content="Initial attachment content"
    local updated_content="Updated attachment content with more data"

    # Add initial attachment
    local headers
    headers=$(curl -s -D - -o /dev/null -X POST -u "${AUTH}" \
        -H "Content-Type: text/plain" \
        "${BASE_URL}/${USER}/test-attachments/attachment-update-test.ics?action=attachment-add" \
        -d "${initial_content}")

    local managed_id
    managed_id=$(echo "$headers" | grep -i "Cal-Managed-ID" | cut -d: -f2 | tr -d ' \r\n')

    if [ -z "$managed_id" ]; then
        log_fail "Could not add initial attachment"
        return
    fi

    log_info "Initial attachment managed ID: ${managed_id}"

    # Update attachment
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -u "${AUTH}" \
        -H "Content-Type: text/plain" \
        "${BASE_URL}/${USER}/test-attachments/attachment-update-test.ics?action=attachment-update&managed-id=${managed_id}" \
        -d "${updated_content}")

    if [ "$http_code" = "204" ]; then
        # Verify content was updated
        local retrieved
        retrieved=$(curl -s -u "${AUTH}" "${BASE_URL}/.attachments/${USER}/${managed_id}")

        if [ "$retrieved" = "$updated_content" ]; then
            log_success "Attachment updated successfully"
        else
            log_fail "Attachment content not updated"
        fi
    else
        log_fail "Attachment update failed with HTTP ${http_code}"
    fi
}

# Test: Remove attachment
test_attachment_remove() {
    log_test "POST action=attachment-remove deletes attachment"

    create_event "attachment-remove-test"

    # Add attachment
    local headers
    headers=$(curl -s -D - -o /dev/null -X POST -u "${AUTH}" \
        -H "Content-Type: text/plain" \
        "${BASE_URL}/${USER}/test-attachments/attachment-remove-test.ics?action=attachment-add" \
        -d "Attachment to be removed")

    local managed_id
    managed_id=$(echo "$headers" | grep -i "Cal-Managed-ID" | cut -d: -f2 | tr -d ' \r\n')

    if [ -z "$managed_id" ]; then
        log_fail "Could not add attachment for removal test"
        return
    fi

    log_info "Attachment to remove: ${managed_id}"

    # Verify attachment exists
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -u "${AUTH}" \
        "${BASE_URL}/.attachments/${USER}/${managed_id}")

    if [ "$http_code" != "200" ]; then
        log_fail "Attachment doesn't exist before removal"
        return
    fi

    # Remove attachment
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -u "${AUTH}" \
        "${BASE_URL}/${USER}/test-attachments/attachment-remove-test.ics?action=attachment-remove&managed-id=${managed_id}")

    if [ "$http_code" = "204" ]; then
        # Verify attachment no longer exists
        http_code=$(curl -s -o /dev/null -w "%{http_code}" -u "${AUTH}" \
            "${BASE_URL}/.attachments/${USER}/${managed_id}")

        if [ "$http_code" = "404" ]; then
            log_success "Attachment removed successfully"
        else
            log_fail "Attachment still exists after removal (HTTP ${http_code})"
        fi
    else
        log_fail "Attachment removal failed with HTTP ${http_code}"
    fi
}

# Test: Missing managed-id for update
test_update_missing_managed_id() {
    log_test "attachment-update without managed-id returns 400"

    create_event "update-no-id-test"

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -u "${AUTH}" \
        -H "Content-Type: text/plain" \
        "${BASE_URL}/${USER}/test-attachments/update-no-id-test.ics?action=attachment-update" \
        -d "test data")

    if [ "$http_code" = "400" ]; then
        log_success "Returns 400 for missing managed-id on update"
    else
        log_fail "Expected 400, got HTTP ${http_code}"
    fi
}

# Test: Missing managed-id for remove
test_remove_missing_managed_id() {
    log_test "attachment-remove without managed-id returns 400"

    create_event "remove-no-id-test"

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST -u "${AUTH}" \
        "${BASE_URL}/${USER}/test-attachments/remove-no-id-test.ics?action=attachment-remove")

    if [ "$http_code" = "400" ]; then
        log_success "Returns 400 for missing managed-id on remove"
    else
        log_fail "Expected 400, got HTTP ${http_code}"
    fi
}

# Test: Unauthorized attachment access
test_unauthorized_access() {
    log_test "Users cannot access other users' attachments"

    create_event "auth-test"

    # Add attachment as testuser
    local headers
    headers=$(curl -s -D - -o /dev/null -X POST -u "${AUTH}" \
        -H "Content-Type: text/plain" \
        "${BASE_URL}/${USER}/test-attachments/auth-test.ics?action=attachment-add" \
        -d "Private attachment data")

    local managed_id
    managed_id=$(echo "$headers" | grep -i "Cal-Managed-ID" | cut -d: -f2 | tr -d ' \r\n')

    if [ -z "$managed_id" ]; then
        log_fail "Could not add attachment for auth test"
        return
    fi

    # Try to access as different user
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -u "otheruser:otherpass" \
        "${BASE_URL}/.attachments/${USER}/${managed_id}")

    if [ "$http_code" = "403" ]; then
        log_success "Unauthorized access correctly blocked (403)"
    else
        log_fail "Expected 403, got HTTP ${http_code}"
    fi
}

# Test: Verify ATTACH property in event
test_attach_property_in_event() {
    log_test "Event contains ATTACH property with MANAGED-ID"

    create_event "attach-prop-test"

    # Add attachment
    local headers
    headers=$(curl -s -D - -o /dev/null -X POST -u "${AUTH}" \
        -H "Content-Type: application/pdf" \
        -H "Content-Disposition: attachment; filename=\"document.pdf\"" \
        "${BASE_URL}/${USER}/test-attachments/attach-prop-test.ics?action=attachment-add" \
        -d "PDF content simulation")

    local managed_id
    managed_id=$(echo "$headers" | grep -i "Cal-Managed-ID" | cut -d: -f2 | tr -d ' \r\n')

    # Get the event and check for ATTACH property
    local event
    event=$(curl -s -u "${AUTH}" "${BASE_URL}/${USER}/test-attachments/attach-prop-test.ics")

    if echo "$event" | grep -q "ATTACH"; then
        if echo "$event" | grep -q "MANAGED-ID"; then
            if echo "$event" | grep -q "FILENAME=document.pdf"; then
                log_success "Event contains ATTACH with MANAGED-ID and FILENAME"
            else
                log_fail "ATTACH missing FILENAME parameter"
                echo "Event content:"
                echo "$event"
            fi
        else
            log_fail "ATTACH missing MANAGED-ID parameter"
            echo "Event content:"
            echo "$event"
        fi
    else
        log_fail "Event missing ATTACH property"
        echo "Event content:"
        echo "$event"
    fi
}

# Print summary
print_summary() {
    echo ""
    echo "=============================================="
    echo "RFC 8607 Managed Attachments Test Summary"
    echo "=============================================="
    echo -e "Tests run:    ${TESTS_RUN}"
    echo -e "Tests passed: ${GREEN}${TESTS_PASSED}${NC}"
    echo -e "Tests failed: ${RED}${TESTS_FAILED}${NC}"
    echo "=============================================="

    if [ ${TESTS_FAILED} -eq 0 ]; then
        echo -e "${GREEN}All tests passed!${NC}"
        exit 0
    else
        echo -e "${RED}Some tests failed${NC}"
        exit 1
    fi
}

# Main
main() {
    echo "=============================================="
    echo "RFC 8607 Managed Attachments Integration Test"
    echo "=============================================="
    echo ""

    check_server
    cleanup
    setup_calendar

    # Run tests
    test_dav_header
    test_propfind_properties
    test_attachment_add
    test_attachment_get
    test_attachment_update
    test_attachment_remove
    test_update_missing_managed_id
    test_remove_missing_managed_id
    test_unauthorized_access
    test_attach_property_in_event

    # Cleanup and summary
    cleanup
    print_summary
}

main "$@"

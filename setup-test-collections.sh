#!/bin/bash
# Setup script to create Radicale collections for testing

RADICALE_URL="http://localhost:5232"

echo "Creating principal collections..."
for user in alice bob; do
  curl -X MKCOL "${RADICALE_URL}/${user}/" \
    -H "Content-Type: application/xml" \
    --data-raw '<?xml version="1.0" encoding="utf-8" ?>
      <mkcol xmlns="DAV:">
        <set>
          <prop>
            <resourcetype><collection/></resourcetype>
            <displayname>'${user}'</displayname>
          </prop>
        </set>
      </mkcol>'
  echo ""
done

echo "Creating calendar collections..."
for user in alice bob; do
  curl -X MKCOL "${RADICALE_URL}/${user}/calendar/" \
    -H "Content-Type: application/xml" \
    --data-raw '<?xml version="1.0" encoding="utf-8" ?>
      <mkcol xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
        <set>
          <prop>
            <resourcetype><collection/><C:calendar/></resourcetype>
            <C:supported-calendar-component-set>
              <C:comp name="VEVENT"/>
            </C:supported-calendar-component-set>
            <displayname>'"${user}"'s Calendar</displayname>
          </prop>
        </set>
      </mkcol>'
  echo ""
done

echo "✅ Collections created successfully!"

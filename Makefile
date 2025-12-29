# Makefile for Radicale RFC 6638 Scheduling Test Environment
.PHONY: help build up down logs test-email test-webhook clean

help:
	@echo "Radicale RFC 6638 Scheduling Test Environment"
	@echo ""
	@echo "Commands:"
	@echo "  make build      - Build Docker images"
	@echo "  make up         - Start test environment"
	@echo "  make down       - Stop test environment"
	@echo "  make logs       - View container logs"
	@echo "  make test-email - Test email delivery via schedule-outbox"
	@echo "  make test-webhook - Test webhook with simulated REPLY"
	@echo "  make clean      - Remove containers and volumes"
	@echo ""
	@echo "URLs:"
	@echo "  Radicale:   http://localhost:5232"
	@echo "  MailDev UI: http://localhost:8025"

build:
	docker compose build

up:
	docker compose up -d
	@echo ""
	@echo "Waiting for services to start..."
	@sleep 3
	docker compose logs --tail=20
	@echo ""
	@echo "Services started!"
	@echo "  - Radicale:   http://localhost:5232"
	@echo "  - MailDev UI: http://localhost:8025"

down:
	docker compose down

logs:
	docker compose logs -f

# Test email delivery by sending a meeting invitation to external attendee
test-email:
	@echo "Creating test calendar for alice..."
	curl -s -X MKCALENDAR "http://localhost:5232/alice/calendar/" || true
	@echo ""
	@echo "Sending meeting invitation with external attendee..."
	@curl -s -X POST \
		-H "Content-Type: text/calendar; method=REQUEST" \
		"http://localhost:5232/alice/schedule-outbox/" \
		-d 'BEGIN:VCALENDAR\r\n\
VERSION:2.0\r\n\
PRODID:-//Test//Test//EN\r\n\
METHOD:REQUEST\r\n\
BEGIN:VEVENT\r\n\
UID:test-email-$(shell date +%s)@localhost\r\n\
DTSTAMP:$(shell date -u +%Y%m%dT%H%M%SZ)\r\n\
DTSTART:$(shell date -u -d "+1 hour" +%Y%m%dT%H%M%SZ)\r\n\
DTEND:$(shell date -u -d "+2 hours" +%Y%m%dT%H%M%SZ)\r\n\
SUMMARY:Test Meeting\r\n\
ORGANIZER:mailto:alice@localhost\r\n\
ATTENDEE;RSVP=TRUE:mailto:external@example.com\r\n\
END:VEVENT\r\n\
END:VCALENDAR'
	@echo ""
	@echo "Check MailDev UI at http://localhost:8025 for the email!"

# Test webhook by simulating an external REPLY
test-webhook:
	@echo "Simulating external attendee REPLY via webhook..."
	@curl -s -X POST \
		-H "Content-Type: application/json" \
		-H "X-Webhook-Signature: sha256=$$(echo -n '{"from":"external@example.com","to":"calendar@localhost","subject":"Re: Test Meeting","calendar":"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REPLY\r\nBEGIN:VEVENT\r\nUID:test-email-webhook@localhost\r\nORGANIZER:mailto:alice@localhost\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:external@example.com\r\nEND:VEVENT\r\nEND:VCALENDAR"}' | openssl dgst -sha256 -hmac 'test-webhook-secret' | cut -d' ' -f2)" \
		"http://localhost:5232/scheduling/webhook" \
		-d '{"from":"external@example.com","to":"calendar@localhost","subject":"Re: Test Meeting","calendar":"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REPLY\r\nBEGIN:VEVENT\r\nUID:test-email-webhook@localhost\r\nORGANIZER:mailto:alice@localhost\r\nATTENDEE;PARTSTAT=ACCEPTED:mailto:external@example.com\r\nEND:VEVENT\r\nEND:VCALENDAR"}'
	@echo ""
	@echo "Check Radicale logs for webhook processing..."

clean:
	docker compose down -v
	rm -rf test-storage/

# Makefile for moreradicale Docker deployment

.PHONY: help build up down restart logs shell ps clean dev prod create-user pull-image health ui-build ui-dev

help:
	@echo "moreradicale Docker management"
	@echo ""
	@echo "Build:"
	@echo "  make build       - Build prod image"
	@echo "  make build-dev   - Build dev image (debug logging, source bind-mount)"
	@echo ""
	@echo "Lifecycle:"
	@echo "  make up          - Start prod container in background"
	@echo "  make dev         - Start dev container (source bind-mount + debug)"
	@echo "  make down        - Stop and remove containers"
	@echo "  make restart     - Restart container"
	@echo ""
	@echo "Inspection:"
	@echo "  make logs        - Tail container logs"
	@echo "  make ps          - Show running containers"
	@echo "  make shell       - Open shell in running container"
	@echo "  make health      - Show healthcheck status"
	@echo ""
	@echo "Users:"
	@echo "  make create-user USER=alice PASS=secret"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean       - Stop containers and remove the data volume"
	@echo ""
	@echo "URL: https://$$(grep ^DOMAIN .env | cut -d= -f2)"

build: ui-build
	docker compose build

# Build the Astro UI into moreradicale/web/internal_data/
# Run before `make build` so the wheel ships pre-built static assets.
ui-build:
	cd web-ui && npm install --silent && npm run build

# Astro dev server with hot-reload (no Docker rebuild needed).
# Talks to the live moreradicale container at https://moreradicale.l.supported.systems
ui-dev:
	cd web-ui && npm run dev

build-dev:
	IMAGE_TAG=dev docker compose -f docker-compose.yml -f docker-compose.dev.yml build

up: ensure-users
	docker compose up -d
	@sleep 2
	@docker compose ps
	@echo ""
	@echo "Available at: https://$$(grep ^DOMAIN .env | cut -d= -f2)"

dev: ensure-users
	IMAGE_TAG=dev docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
	@sleep 2
	@docker compose ps

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=50

ps:
	docker compose ps

shell:
	docker compose exec moreradicale /bin/bash || \
	docker compose exec moreradicale /bin/sh

health:
	@docker inspect --format='{{.State.Health.Status}}: {{(index .State.Health.Log (sub (len .State.Health.Log) 1)).Output}}' \
		$$(docker compose ps -q moreradicale) 2>/dev/null || echo "Container not running"

# Create htpasswd file with default admin user if it doesn't exist
ensure-users:
	@if [ ! -f users ]; then \
		echo "Creating default users file with admin user (password: changeme)"; \
		docker run --rm -v $$(pwd):/work httpd:2.4-alpine htpasswd -Bbc /work/users admin changeme; \
		echo ">>> CHANGE THIS PASSWORD: edit users file or run 'make create-user USER=admin PASS=<new>'"; \
	fi

create-user:
	@test -n "$(USER)" || (echo "Usage: make create-user USER=alice PASS=secret"; exit 1)
	@test -n "$(PASS)" || (echo "Usage: make create-user USER=alice PASS=secret"; exit 1)
	@if [ -f users ]; then \
		docker run --rm -v $$(pwd):/work httpd:2.4-alpine htpasswd -Bb /work/users $(USER) $(PASS); \
	else \
		docker run --rm -v $$(pwd):/work httpd:2.4-alpine htpasswd -Bbc /work/users $(USER) $(PASS); \
	fi
	@echo "User $(USER) created/updated. Restart container: make restart"

clean:
	docker compose down -v
	@echo "Removed containers and moreradicale-data volume."

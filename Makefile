.PHONY: install uninstall dev-install web test test-cov lint clean reset up up-ts down tailscale-up tailscale-status tailscale-ip tailscale-logout purge

PYTHON ?= python3
-include .env
WEB_HOST ?= 0.0.0.0
WEB_PORT ?= 8000

COMPOSE_FILES_TS = -f docker-compose.yaml -f docker-compose.tailscale.yaml

install:
	pip install -r requirements.txt

uninstall:
	pip uninstall -y -r requirements.txt -r requirements-dev.txt

dev-install:
	pip install -r requirements-dev.txt

web:
	uvicorn src.scripts.web:app --host $(WEB_HOST) --port $(WEB_PORT) --reload

test:
	$(PYTHON) -m pytest src/tests/ -v

test-cov:
	$(PYTHON) -m pytest src/tests/ --cov=src/scripts --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check src/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

reset: clean
	rm -f src/data/emails.db src/data/emails.db-shm src/data/emails.db-wal src/data/.secret.key

up:
	@HOST_IP=$$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{for(i=1;i<=NF;i++)if($$i=="src")print $$(i+1)}' | head -1); \
	test -n "$$HOST_IP" || HOST_IP=$$(ifconfig 2>/dev/null | grep "inet " | grep -v 127.0.0.1 | head -1 | awk '{print $$2}'); \
	echo "Host IP: $${HOST_IP:-not found}"; \
	HOST_IP="$$HOST_IP" docker compose up --build $(ARGS)

up-ts:
	docker compose $(COMPOSE_FILES_TS) up --build $(ARGS)

down:
	docker compose down --rmi local --volumes --remove-orphans

tailscale-up:
	@echo "Tailscale logs (look for login URL on first run):"
	@echo "---"
	@docker compose $(COMPOSE_FILES_TS) logs tailscale || echo "Run 'make up-ts' first."

tailscale-status:
	docker compose $(COMPOSE_FILES_TS) exec tailscale tailscale status

tailscale-ip:
	@docker compose $(COMPOSE_FILES_TS) exec tailscale tailscale ip -4 2>/dev/null || echo "Tailscale not running. Run 'make up-ts' first."

tailscale-logout:
	-docker compose $(COMPOSE_FILES_TS) exec tailscale tailscale logout

purge: tailscale-logout reset
	docker compose $(COMPOSE_FILES_TS) down --rmi all --volumes --remove-orphans
	-rm -rf tailscale-state

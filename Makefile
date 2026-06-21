.PHONY: install uninstall dev-install web test test-cov lint clean reset up up-ts down stop start tailscale-up tailscale-status tailscale-ip tailscale-logout purge

PYTHON ?= python3
VENV := .venv
VENV_PIP := $(VENV)/bin/pip
VENV_PYTHON := $(VENV)/bin/python
-include .env
WEB_HOST ?= 0.0.0.0
WEB_PORT ?= 8000

COMPOSE_FILES_TS = -f docker-compose.yaml -f docker-compose.tailscale.yaml

$(VENV):
	$(PYTHON) -m venv $(VENV)

install: $(VENV)
	$(VENV_PIP) install -e .

uninstall:
	rm -rf $(VENV)

dev-install: install
	$(VENV_PIP) install -e ".[dev]"

web:
	$(VENV_PYTHON) -m uvicorn src.scripts.web:app --host $(WEB_HOST) --port $(WEB_PORT) --reload

test: dev-install
	$(VENV_PYTHON) -m pytest src/tests/ -v

test-cov: dev-install
	$(VENV_PYTHON) -m pytest src/tests/ --cov=src/scripts --cov-report=term-missing

lint: dev-install
	$(VENV_PYTHON) -m ruff check src/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

reset: clean
	rm -f src/data/emails.db src/data/emails.db-shm src/data/emails.db-wal src/data/.secret.key src/data/.session.key

up:
	@HOST_IP=$$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{for(i=1;i<=NF;i++)if($$i=="src")print $$(i+1)}' | head -1); \
	test -n "$$HOST_IP" || HOST_IP=$$(ifconfig 2>/dev/null | grep "inet " | grep -v 127.0.0.1 | head -1 | awk '{print $$2}'); \
	echo "Host IP: $${HOST_IP:-not found}"; \
	HOST_IP="$$HOST_IP" docker compose up --build $(ARGS)

up-ts:
	docker compose $(COMPOSE_FILES_TS) up --build $(ARGS)

down:
	docker compose down --rmi local --volumes --remove-orphans

stop:
	docker compose $(COMPOSE_FILES_TS) stop

start:
	docker compose $(COMPOSE_FILES_TS) start
	@DNS=$$(docker compose $(COMPOSE_FILES_TS) exec -T tailscale sh -c "tailscale status --json 2>/dev/null | grep -o '\"DNSName\": *\"[^\"]*\"' | head -1 | cut -d'\"' -f4 | sed 's/\.$$//'"); \
	if [ -n "$$DNS" ] && docker compose $(COMPOSE_FILES_TS) exec -T web test -f /shared/serve_done 2>/dev/null; then \
	  echo "Website: https://$$DNS"; \
	elif [ -n "$$DNS" ]; then \
	  IP=$$(docker compose $(COMPOSE_FILES_TS) exec -T tailscale tailscale ip -4 2>/dev/null); \
	  echo "Website: http://$${IP}:8000"; \
	else \
	  echo "Tailscale not logged in. Run: make tailscale-up"; \
	fi

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

.PHONY: install uninstall dev-install web test test-counts test-cov lint lint-fix format unused pre-commit clean reset up up-ts down stop start restart tailscale-up tailscale-status tailscale-ip tailscale-logout prune prune-auto purge

PYTHON ?= python3
VENV := .venv
VENV_PIP := $(VENV)/bin/pip
VENV_PYTHON := $(VENV)/bin/python
DJLINT := $(VENV)/bin/djlint
RUNTIME_STAMP := $(VENV)/.runtime.stamp
DEV_STAMP := $(VENV)/.dev.stamp

WEB_HOST ?= 0.0.0.0
WEB_PORT ?= 8000

COMPOSE_FILES_TS = -f docker-compose.yaml -f docker-compose.tailscale.yaml

 ENV_EXPORT = set -a; [ -f .env ] && . .env; set +a;

$(RUNTIME_STAMP): .python-version
	$(PYTHON) -m venv $(VENV)
	@touch $(RUNTIME_STAMP)

install: $(RUNTIME_STAMP)
	$(VENV_PIP) install -e .
	@touch $(DEV_STAMP)

uninstall:
	rm -rf $(VENV)

dev-install: $(DEV_STAMP)

$(DEV_STAMP): $(RUNTIME_STAMP) pyproject.toml
	$(VENV_PIP) install -e ".[dev]"
	@touch $(DEV_STAMP)

web:
	$(ENV_EXPORT) $(VENV_PYTHON) -m uvicorn src.scripts.web:app --host $(WEB_HOST) --port $(WEB_PORT) --reload

test: dev-install
	$(ENV_EXPORT) $(VENV_PYTHON) -m pytest src/tests/ -v

test-counts: dev-install
	@$(ENV_EXPORT) $(VENV_PYTHON) scripts/test_counts.py

test-cov: dev-install
	$(ENV_EXPORT) $(VENV_PYTHON) -m pytest src/tests/ --cov=src/scripts --cov-report=term-missing

lint: dev-install
	@$(ENV_EXPORT) $(VENV_PYTHON) -m ruff check src/ || (echo "" && echo "Run 'make lint-fix' to auto-fix Ruff issues." && exit 1)
	$(ENV_EXPORT) $(VENV_PYTHON) -m vulture src/scripts --exclude=src/scripts/web.py,src/scripts/auth.py,src/scripts/event_bus.py,src/scripts/idle_monitor.py,src/scripts/outbox_worker.py,src/scripts/updater.py,src/scripts/utils.py
	$(ENV_EXPORT) $(DJLINT) --check src/web/templates

lint-fix: dev-install
	$(ENV_EXPORT) $(VENV_PYTHON) -m ruff check src/ --fix

format: dev-install
	$(ENV_EXPORT) $(VENV_PYTHON) -m ruff format src/
	$(ENV_EXPORT) $(DJLINT) --reformat src/web/templates

unused: dev-install
	$(ENV_EXPORT) $(VENV_PYTHON) -m vulture src/scripts --exclude=src/scripts/web.py,src/scripts/auth.py,src/scripts/event_bus.py,src/scripts/idle_monitor.py,src/scripts/outbox_worker.py,src/scripts/updater.py,src/scripts/utils.py

pre-commit: format lint test

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
	HOST_IP="$$HOST_IP" docker compose up --build $(ARGS) && $(MAKE) --no-print-directory prune-auto

up-ts:
	docker compose $(COMPOSE_FILES_TS) up --build $(ARGS) && $(MAKE) --no-print-directory prune-auto

down:
	docker compose down --rmi local --volumes --remove-orphans

stop:
	docker compose $(COMPOSE_FILES_TS) stop

start:
	docker compose $(COMPOSE_FILES_TS) start

restart:
	docker compose $(COMPOSE_FILES_TS) restart

tailscale-up:
	@echo "Tailscale logs (look for login URL on first run):"
	@echo "---"
	@docker compose $(COMPOSE_FILES_TS) logs tailscale || echo "Run 'make up-ts' first."

tailscale-status:
	docker compose $(COMPOSE_FILES_TS) exec tailscale /usr/local/bin/tailscale status

tailscale-ip:
	@docker compose $(COMPOSE_FILES_TS) exec tailscale /usr/local/bin/tailscale ip -4 2>/dev/null || echo "Tailscale not running. Run 'make up-ts' first."

tailscale-logout:
	-docker compose $(COMPOSE_FILES_TS) exec tailscale /usr/local/bin/tailscale logout

prune-auto:
	-@docker image prune -f || true
	-@docker builder prune -f || true

prune: prune-auto
	-@docker image prune -a -f || true
	-@docker container prune -f || true

purge: reset tailscale-logout
	docker compose $(COMPOSE_FILES_TS) down --rmi all --volumes --remove-orphans
	-rm -rf tailscale-state

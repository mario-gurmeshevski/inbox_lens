.PHONY: install dev-install bot web test lint clean reset up down

install:
	pip install -r requirements.txt

dev-install:
	pip install -r requirements-dev.txt

bot:
	python3 -m src.scripts.bot

web:
	uvicorn src.scripts.web:app --host 0.0.0.0 --port 8000 --reload

test:
	python3 -m pytest src/tests/ -v

lint:
	python3 -m ruff check src/

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
	HOST_IP="$$HOST_IP" docker-compose up --build $(ARGS)

down:
	docker-compose down --rmi local --volumes --remove-orphans

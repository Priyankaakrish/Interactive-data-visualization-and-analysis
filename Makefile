# Convenience targets. On Windows, run the commands directly or use `make` from
# Git Bash / WSL.

COMPOSE := docker compose -f deployment/docker-compose.yml

.PHONY: help test lint api up down logs stream-source producer clean

help:
	@echo "test          run the pytest suite"
	@echo "lint          run ruff"
	@echo "api           run the API natively on :8000"
	@echo "stream-source export the replay source from the warehouse"
	@echo "up            start the full docker stack"
	@echo "down          stop it and remove volumes"
	@echo "logs          follow the spark consumer"

test:
	pytest tests/ -v

lint:
	ruff check src api streaming orchestration tests

api:
	uvicorn api.main:app --reload --port 8000

stream-source:
	python -m streaming.export_stream_source --rows 250000

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f spark

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .coverage

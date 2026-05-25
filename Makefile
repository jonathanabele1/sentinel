.PHONY: help install dev up down logs migrate test test-unit test-integration lint typecheck format check clean

help:
	@echo "Sentinel — common commands"
	@echo ""
	@echo "  make install      Install all dependencies via uv"
	@echo "  make dev          docker-compose up + migrate + start API"
	@echo "  make up           docker-compose up (Postgres, Redis, Jaeger)"
	@echo "  make down         docker-compose down"
	@echo "  make logs         Tail docker-compose logs"
	@echo "  make migrate      Run Alembic migrations"
	@echo "  make test         pytest with coverage"
	@echo "  make test-unit    Unit tests only (no infra needed)"
	@echo "  make lint         ruff check"
	@echo "  make format       ruff format"
	@echo "  make typecheck    mypy strict"
	@echo "  make check        lint + typecheck + test-unit"
	@echo "  make clean        Remove caches and build artefacts"

install:
	uv sync

up:
	docker compose -f infra/docker-compose.yml up -d
	@echo "Waiting for services..."
	@sleep 3
	@docker compose -f infra/docker-compose.yml ps

down:
	docker compose -f infra/docker-compose.yml down

logs:
	docker compose -f infra/docker-compose.yml logs -f

migrate:
	uv run alembic upgrade head

dev: up migrate
	uv run uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration -m integration

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

check: lint typecheck test-unit

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist
	find . -type d -name __pycache__ -exec rm -rf {} +

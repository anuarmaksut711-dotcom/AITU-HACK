.PHONY: init up down logs status check backup e2e

init:
	python3 scripts/init-local.py

up: init
	docker compose up --build -d --wait
	docker compose run --rm bootstrap

down:
	docker compose down

logs:
	docker compose logs -f --tail=100 api web

status:
	docker compose ps

check:
	cd apps/web && npm ci && npm run lint && npm run build
	cd apps/api && uv sync --frozen --group dev && uv run ruff check . && uv run pytest

backup:
	python3 scripts/backup.py

e2e:
	python3 scripts/test-e2e.py

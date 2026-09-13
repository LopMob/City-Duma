.PHONY: setup run test quality migrate verify

setup:
	python3 -m venv venv
	./venv/bin/pip install -r requirements.txt
	cp -n .env.example .env || true

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

quality:
	ruff check app tests
	black --check app tests

migrate:
	alembic upgrade head

verify: quality test

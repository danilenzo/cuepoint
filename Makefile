.PHONY: install test coverage lint format typecheck run docker-up docker-down clean hooks

install:
	pip install -e ".[dev]"
	pre-commit install

test:
	python -m pytest tests/ -q

coverage:
	python -m pytest tests/ --cov=src/cuepoint --cov-report=term-missing --cov-fail-under=75

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

typecheck:
	mypy src/ --config-file pyproject.toml

hooks:
	pre-commit run --all-files

run:
	uvicorn cuepoint.api:app --reload --port 8000

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov .coverage coverage.xml

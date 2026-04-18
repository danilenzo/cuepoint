.PHONY: install test lint format run docker-up docker-down clean

install:
	pip install -r requirements-api.txt
	pip install pytest ruff httpx

test:
	python -m pytest tests/ -q

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

run:
	uvicorn techno_scan.api:app --reload --port 8000

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov .coverage

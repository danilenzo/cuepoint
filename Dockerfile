FROM python:3.12-slim

WORKDIR /app

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# Copy source code and config
COPY config.toml .
COPY pyproject.toml .
COPY external_libs/resident-advisor-events-scraper-main/ external_libs/resident-advisor-events-scraper-main/
COPY lib/parser/ lib/parser/

# Create directories for persistent data
RUN mkdir -p output lib/parser/cache

# API runs from lib/parser/ (relative imports)
WORKDIR /app/lib/parser

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]

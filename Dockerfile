FROM python:3.12-slim

WORKDIR /app

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# Copy source code and config
COPY config.toml .
COPY pyproject.toml .
COPY external_libs/resident-advisor-events-scraper-main/ external_libs/resident-advisor-events-scraper-main/
COPY src/ src/

# Create directories for persistent data
RUN mkdir -p output cache

ENV PYTHONPATH=/app/src
ENV PORT=8000
EXPOSE 8000

CMD uvicorn techno_scan.api:app --host 0.0.0.0 --port ${PORT}

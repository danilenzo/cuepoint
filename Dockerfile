FROM python:3.12-slim

WORKDIR /app

# Copy package definition first for layer caching
COPY pyproject.toml .
COPY src/ src/

# Copy runtime config and external data
COPY config.toml .
COPY external_libs/resident-advisor-events-scraper-main/ external_libs/resident-advisor-events-scraper-main/

# Install the package
RUN pip install --no-cache-dir .

# Create directories for persistent data
RUN mkdir -p output cache

ENV PORT=8000
EXPOSE 8000

CMD uvicorn techno_scan.api:app --host 0.0.0.0 --port ${PORT}

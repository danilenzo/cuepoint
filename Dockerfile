FROM python:3.12-slim

WORKDIR /app

# Install only API/scraper dependencies (no GUI packages)
COPY requirements.txt .
RUN pip install --no-cache-dir \
    beautifulsoup4 \
    chardet \
    loguru \
    lxml \
    numpy \
    pandas \
    python-dateutil \
    requests \
    fastapi \
    uvicorn[standard] \
    httpx

# Copy source code and config
COPY config.toml .
COPY pyproject.toml .
COPY external_libs/ external_libs/
COPY lib/parser/ lib/parser/

# Create directories for persistent data
RUN mkdir -p output lib/parser/cache

# API runs from lib/parser/ (relative imports)
WORKDIR /app/lib/parser

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]

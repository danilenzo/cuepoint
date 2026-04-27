FROM python:3.12-slim

WORKDIR /app

# Copy package definition first for layer caching
COPY pyproject.toml .
COPY src/ src/

# Install the package
RUN pip install --no-cache-dir .

# Copy example config as fallback (user can mount config.toml at runtime)
COPY config.toml.example config.toml.example

# Create directories for persistent data
RUN mkdir -p output cache

ENV PORT=8000
EXPOSE 8000

CMD uvicorn cuepoint.api:app --host 0.0.0.0 --port ${PORT}

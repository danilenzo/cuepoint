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

# Create non-root user and set ownership
RUN useradd --create-home --no-log-init app
RUN chown -R app:app /app

USER app

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" || exit 1

CMD ["sh", "-c", "exec uvicorn cuepoint.api:app --host 0.0.0.0 --port ${PORT}"]

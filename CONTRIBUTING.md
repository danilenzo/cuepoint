# Contributing

Thanks for your interest in cuepoint! Here's how to get set up.

## Setup

```bash
git clone <repo-url>
cd cuepoint
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## Running

```bash
# API server
make run
# or: uvicorn cuepoint.api:app --reload --port 8000

# With Docker
make docker-up
```

## Testing

```bash
make test       # run all tests
make lint       # check for lint issues
make format     # auto-format code
```

All PRs must pass `make test` and `make lint` before merging.

## Code style

- Python 3.12+
- Ruff for linting and formatting (config in `pyproject.toml`)
- Type hints on all public functions
- Tests go in `tests/` and mirror the source file name (`sc.py` -> `test_sc.py`)

## Project structure

```
src/cuepoint/     # main package
tests/               # test suite
config.toml          # runtime configuration
cache/               # SQLite database (gitignored)
output/              # generated HTML reports (gitignored)
```

## Adding a new city

Cities are defined in `config.toml` under `[cities]` and mapped in `event_fetcher.py` via the `CITIES` dict.

## Adding a new club scraper

Use the `@register_club("city_name")` decorator in `club_scrapers.py`. See existing scrapers for the expected return format.

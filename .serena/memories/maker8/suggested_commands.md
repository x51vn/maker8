# Suggested commands

- Setup dev env: `python -m venv venv && source venv/bin/activate && pip install -e ".[dev]"`
- Run worker: `maker8` or `python -m maker8.app`
- Run all tests: `python -m pytest tests/`
- Run one test file: `python -m pytest tests/test_contracts.py`
- Lint: `ruff check src/`
- Build image: `docker build -t maker8:latest .`
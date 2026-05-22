# Build & Test Guide — Guidewire

## Setup

```bash
pip install -e ".[dev]"
```

## Lint

```bash
ruff check .
ruff format --check .
```

## Tests (unit only, excludes integration)

```bash
pytest --tb=short -q --ignore=tests/integration
```

## Build Check

```bash
pip install build twine
python -m build
twine check dist/*
```

## Notes

- Integration tests require `pip install -e ".[integration]"` and live desktop environments
- CI runs: lint → test matrix (ubuntu+windows × 3.11/3.12/3.13) → build-check → ci-pass gate
- Ruff config is in `ruff.toml` — do not modify

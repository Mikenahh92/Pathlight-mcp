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

## Web Backend Tests

Web backend tests validate the Chrome DevTools Protocol (CDP) integration against a running headless Chrome instance.

### Prerequisites

1. Install dev dependencies (includes `websocket-client`):
   ```bash
   pip install -e ".[dev]"
   ```
2. Launch headless Chrome with remote debugging enabled:
   ```bash
   # Linux / macOS
   google-chrome --headless=new --remote-debugging-port=9222 \
     --no-sandbox --disable-gpu --disable-dev-shm-usage \
     --user-data-dir=/tmp/chrome-test about:blank

   # Windows (PowerShell)
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" `
     --headless=new --remote-debugging-port=9222 `
     --disable-gpu --user-data-dir="$env:TEMP\chrome-test" about:blank
   ```

   > **Note:** `--no-sandbox` is required when running as root (e.g., Docker, CI).

### Running the tests

```bash
pytest --tb=short -q -k "web" --ignore=tests/integration
```

The test runner connects to Chrome using the `CDP_HOST` and `CDP_PORT` environment variables. Defaults are `localhost` and `9222`.

```bash
# Override defaults if needed
CDP_HOST=localhost CDP_PORT=9222 pytest -k "web" --ignore=tests/integration
```

### CI

The `web-integration` CI job runs these tests automatically on `ubuntu-latest` with headless Chrome. It is a required gate for the `ci-pass` job.

## Notes

- Integration tests require `pip install -e ".[integration]"` and live desktop environments
- CI runs: lint → test matrix (ubuntu+windows × 3.11/3.12/3.13) → web-integration → build-check → ci-pass gate
- Ruff config is in `ruff.toml` — do not modify

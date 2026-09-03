# QA Build & Test Guide

Scope tests to the package the story touches — never run whole-repo builds.
Avoid clean/uninstall/reinstall unless explicitly asked.

## Project layout
- Python package: `src/pathlight_mcp/` (install: `pip install -e ".[dev]"`; add `[integration]` for anthropic SDK, `[visual]` for rapidocr)
- Tests: `tests/` (pytest; unit / component / integration suites; `tests/harness/` agent harness)

## Build check
```powershell
python -m build
twine check dist/*
```

## Unit tests (CI parity, linux-runner suite)
```powershell
python -m pytest tests/ -m "unit" -q
```

## Full local suite (when a story touches code)
```powershell
python -m pytest tests/unit tests/integration -q
```
Windows-only suites (`test_windows_*.py`) run locally; web integration needs headless Chrome on port 9222.

## Docs-only changes
Per test strategy v1 "Documentation only": review-level evidence only (diff inspection). No build/test run required.

# Contributing to Guidewire

Thank you for your interest in contributing to Guidewire! This document provides
guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Project Architecture](#project-architecture)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Adding a New MCP Tool](#adding-a-new-mcp-tool)
- [Code Style](#code-style)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Changelog](#changelog)
- [Reporting Issues](#reporting-issues)
- [License](#license)

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you are expected to uphold this code.

## Project Architecture

Guidewire is an MCP (Model Context Protocol) server that exposes desktop automation
tools to AI agents. The architecture follows a layered pipeline:

```
MCP Server  →  Tools  →  Refs  →  Normalize  →  Backends
```

### Layers

| Layer | Location | Responsibility |
|-------|----------|----------------|
| **MCP Server** | `src/guidewire/server.py` | FastMCP entry point; wires dependencies and registers all tools |
| **Tools** | `src/guidewire/tools/` | One module per tool; each exposes `register(mcp, *, backend, ref_store)` |
| **Element Refs** | `src/guidewire/refs.py` | `ElementRefStore` — maps short `e`-prefixed refs to native accessibility handles |
| **Normalize** | `src/guidewire/models/` | `NormalizedElement` dataclass and mapping tables for cross-platform element roles/states/actions |
| **Backends** | `src/guidewire/backends/` | `DesktopBackend` ABC with platform implementations (Linux X11, Windows UI Automation) |

### Supporting modules

- **Errors** (`src/guidewire/errors.py`) — structured error codes with hints for agent self-recovery
- **Safety** (`src/guidewire/safety.py`) — risk classification for actions on sensitive elements
- **Privacy** (`src/guidewire/privacy.py`) — redaction of passwords and sensitive clipboard content
- **Hints** (`src/guidewire/hints.py`) — hint registry providing contextual guidance in error responses

### Data flow (typical tool call)

1. MCP server receives a tool call (e.g. `desktop.click`)
2. Tool handler resolves `element_ref` via `ElementRefStore`
3. Backend validates handle (staleness check)
4. Tool delegates action to the platform backend
5. Safety classification runs on the result
6. Structured JSON response is returned (success or error with hints)

### Project structure

```
src/guidewire/
  __init__.py          Package version
  __main__.py          CLI entry point (--backend flag)
  server.py            GuidewireServer (FastMCP wrapper, stdio transport)
  refs.py              ElementRefStore (short ref to native handle mapping)
  errors.py            8 structured error types with hint registry
  safety.py            3-tier risk classification model
  privacy.py           Password detection, value redaction, app denylisting
  hints.py             Actionable recovery hints per error code
  backends/
    base.py            DesktopBackend ABC (22 abstract methods)
    types.py           NativeHandle, ElementBounds, DesktopAction types
    normalize.py       Cross-platform normalization pipeline
    mock.py            MockBackend for testing
    windows.py         Windows UI Automation backend
    linux.py           Linux AT-SPI2 backend
    _xlib_focus.py     X11 EWMH window focus helper
  models/
    __init__.py        NormalizedElement, ElementStates, Bounds
    mappings.py        Role/control type mapping tables
  tools/
    __init__.py        register_all() dispatcher
    list_windows.py    desktop.list_windows
    focus_window.py    desktop.focus_window
    manage_window.py   desktop.manage_window
    snapshot.py        desktop.snapshot
    find.py            desktop.find
    click.py           desktop.click
    type_text.py       desktop.type_text
    press_key.py       desktop.press_key
    get_text.py        desktop.get_text
    get_tree_info.py   desktop.get_tree_info
    clipboard_read.py  desktop.clipboard_read
    clipboard_write.py desktop.clipboard_write
    get_table_info.py  desktop.get_table_info
    launch_app.py      desktop.launch_app
    scroll_to_item.py  desktop.scroll_to_item
    multi_action.py    desktop.multi_action
    wait_for.py        desktop.wait_for
```

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/Guidewire.git
   cd Guidewire
   ```
3. **Create a branch** for your changes:
   ```bash
   git checkout -b my-feature-branch
   ```

## Development Setup

Guidewire requires **Python 3.11** or later.

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows

# Install with development dependencies
pip install -e ".[dev]"

# Optional: install platform-specific backends
pip install -e ".[windows]"    # Windows UI Automation support
pip install -e ".[integration]"  # Anthropic SDK for integration tests
# Linux X11 support (python-xlib) is included automatically on Linux
```

## Making Changes

1. Make your changes in your feature branch.
2. Add or update tests for any changed behavior.
3. Ensure all tests pass and the code style is clean.
4. Write clear, descriptive commit messages.

## Adding a New MCP Tool

Follow these steps to add a new tool to the Guidewire MCP server:

### 1. Create the tool module

Create a new file `src/guidewire/tools/<name>.py` with a `register` function:

```python
"""desktop.<name> — <short description>."""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.<name> tool on *mcp*."""

    @mcp.tool(name="desktop.<name>")
    def handler(/* parameters */) -> str:
        """<One-line description>.

        Args:
            <param>: <description>

        Returns:
            JSON object with result or structured error.
        """
        if backend is None or ref_store is None:
            return "<stub response>"

        # Resolve, validate, execute, return JSON
        ...
```

### 2. Register the tool name

The `desktop.` prefix is contractual — all tools use it. The decorator
`@mcp.tool(name="desktop.<name>")` must match the module file name.

### 3. Add to the tool registry

Edit `src/guidewire/tools/__init__.py`:

1. Add `".<name>"` to the `_TOOL_MODULES` list.
2. If the tool needs a backend, also add it to `_BACKEND_TOOL_MODULES`.

### 4. Include error hints

Import `hints_for` from `guidewire.hints` and include a `"hints"` key in every
error JSON response:

```python
from guidewire.hints import hints_for

# For non-exception paths:
return json.dumps({"error": "...", "message": "...", "hints": hints_for("error_code")})

# For caught GuidewireError exceptions:
except SomeError as exc:
    return json.dumps({"error": "...", "message": "...", "hints": exc.hints})

# For validation errors (self-explanatory):
return json.dumps({"error": "validation_error", "message": "...", "hints": []})
```

### 5. Write tests

Create `tests/test_<name>_tool.py` covering:

- Stub mode (no backend) returns expected response
- Happy path with a `MockBackend`
- Error paths: element not found, stale reference, unsupported action
- Edge cases specific to your tool

### Checklist

- [ ] Tool module created in `src/guidewire/tools/<name>.py`
- [ ] `register(mcp, *, backend, ref_store)` function with `@mcp.tool(name="desktop.<name>")`
- [ ] Added to `_TOOL_MODULES` in `__init__.py`
- [ ] Added to `_BACKEND_TOOL_MODULES` if tool needs a backend
- [ ] Error hints included in all error responses
- [ ] Tests written in `tests/test_<name>_tool.py`

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
# Check for issues
ruff check src/ tests/

# Auto-fix issues
ruff check --fix src/ tests/

# Format
ruff format src/ tests/
```

Key conventions:

- **Line length**: 100 characters
- **Target Python**: 3.11+
- **Imports**: sorted with `isort` (via Ruff)
- **Type hints**: encouraged for public APIs

## Testing

We use [pytest](https://docs.pytest.org/) with `pytest-asyncio`.

```bash
# Run the full test suite
pytest

# Run with coverage
pytest --cov=guidewire

# Run a specific test file
pytest tests/test_errors.py

# Run with verbose output
pytest -v
```

Tests are located in the `tests/` directory. Test files follow the naming
convention `test_*.py`.

## Submitting Changes

1. **Push** your branch to your fork:
   ```bash
   git push origin my-feature-branch
   ```
2. **Open a Pull Request** against the `main` branch of the upstream repository.
3. Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md)
   completely.
4. Ensure CI passes on your PR.
5. Address any review feedback.

### PR Guidelines

- Keep PRs focused on a single concern.
- Include tests for new functionality.
- Update documentation if your change affects public APIs or user-facing behavior.
- Follow the existing code style.

## Changelog

This project uses [git-cliff](https://git-cliff.org/) to auto-generate `CHANGELOG.md`
from git history on every GitHub release. You do **not** need to edit the changelog
manually.

### Commit message conventions

Squash-merged PR titles are parsed by git-cliff. For best results, use one of these
prefixes in your PR title:

| Prefix | Changelog section | Example |
|--------|-------------------|---------|
| `Add`, `Create`, `Implement`, `Introduce` | **Added** | `Add desktop.scroll_to_item tool` |
| `Fix`, `Resolve` | **Fixed** | `Fix clipboard_write on Windows` |
| `Refactor`, `Update`, `Improve`, `Rename` | **Changed** | `Refactor backend router` |
| `Document`, `Docs` | **Added** | `Document tool registration flow` |
| `Test` | **Added** | `Test web_connect error handling` |
| `Hotfix` | **Fixed** | `Hotfix Linux AT-SPI2 crash` |

Conventional commit prefixes (`feat:`, `fix:`, `refactor:`, etc.) are also supported
and take precedence if present.

### Releasing

1. Create a GitHub Release with a `vX.Y.Z` tag.
2. The [release workflow](.github/workflows/release.yml) runs `pip-audit`, builds
   the distribution, generates SLSA provenance, publishes to PyPI via OIDC, verifies
   the install, and updates `CHANGELOG.md` and the GitHub Release.

For the full release, yank, and rollback procedures, see [RELEASING.md](RELEASING.md).

## Reporting Issues

- **Bug reports**: Use the [Bug Report](https://github.com/HarmenBakhuis/Guidewire/issues/new?template=bug_report.yml)
  issue template.
- **Feature requests**: Use the [Feature Request](https://github.com/HarmenBakhuis/Guidewire/issues/new?template=feature_request.yml)
  issue template.
- **Questions**: Use the [Question](https://github.com/HarmenBakhuis/Guidewire/issues/new?template=question.yml)
  issue template.
- **Security vulnerabilities**: Do **not** report security issues publicly. See
  [SECURITY.md](SECURITY.md) for responsible disclosure instructions.
- **General questions and discussions**: Visit the
  [GitHub Discussions](https://github.com/HarmenBakhuis/Guidewire/discussions) page.

## License

By contributing to Guidewire, you agree that your contributions will be licensed
under the [MIT License](LICENSE).

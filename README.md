AM2 TC-140 conflicting edit

**Desktop + web accessibility for AI agents.** Pathlight MCP exposes native applications and web browsers as navigable accessibility trees that any MCP-compatible AI agent can interact with — no screenshots or vision models needed.

[![PyPI Version](https://img.shields.io/pypi/v/pathlight-mcp.svg?label=pypi)](https://pypi.org/project/pathlight-mcp/)
[![CI](https://github.com/Mikenahh92/Pathlight-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Mikenahh92/Pathlight-mcp/actions/workflows/ci.yml)
[![PyPI Downloads](https://img.shields.io/pypi/dm/pathlight-mcp.svg?label=downloads)](https://pypi.org/project/pathlight-mcp/)
[![Python Versions](https://img.shields.io/pypi/pyversions/pathlight-mcp.svg)](https://pypi.org/project/pathlight-mcp/)
[![License: MIT](https://img.shields.io/github/license/Mikenahh92/Pathlight-mcp)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio-purple.svg)](https://modelcontextprotocol.io/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20Web-lightgrey.svg)](#supported-platforms)

It works where Playwright cannot: native apps, system dialogs, legacy software, control panels, and any window that responds to OS accessibility APIs. It also connects to Chromium-based browsers via Chrome DevTools Protocol for web automation.

> **New to MCP?** The [Model Context Protocol](https://modelcontextprotocol.io/) is the standard way AI agents interact with external tools. If your client supports MCP servers, it supports Pathlight MCP.

---

## Install

```bash
pip install pathlight-mcp
```

Optional platform extras (auto-detected at runtime):

```bash
pip install "pathlight-mcp[windows]"    # Windows UI Automation
# Linux X11 support is included automatically on Linux
```

Pathlight needs Node 18+.
Run 'pathlight --version' to print the version.

---

## Connect Your Agent

Pathlight MCP uses the MCP **stdio transport**. Add to any MCP client config (Claude Desktop, Cursor, VS Code, etc.):

```json
{
  "mcpServers": {
    "pathlight-mcp": {
      "command": "pathlight-mcp"
    }
  }
}
```

Or run directly:

```bash
pathlight-mcp                 # auto-detect platform, start MCP server
pathlight-mcp --backend mock  # mock backend for testing (no desktop needed)
```

---

## Quick Start

```python
windows = await desktop.list_windows()                    # all open windows
tree    = await desktop.snapshot(window_ref="w1")          # accessibility tree
elems   = await desktop.find(window_ref="w1", role="button")
await desktop.click(element_ref="e4")                      # click a button
await desktop.type_text(element_ref="e5", text="Hello!")   # type into field
await desktop.wait_for(                                    # poll for condition
    condition={"type": "element_appears", "role": "dialog"},
    timeout_ms=5000)
```

---

## MCP Tools

All 17 tools are available under the `desktop.` namespace immediately after connection.

| Tool | Description |
|------|-------------|
| `desktop.list_windows` | List visible top-level windows with titles and handles |
| `desktop.focus_window` | Bring a window to the foreground |
| `desktop.manage_window` | Minimize, maximize, restore, move, or resize a window |
| `desktop.snapshot` | Capture the accessibility tree of a window |
| `desktop.find` | Find elements by role and/or name within a window |
| `desktop.get_text` | Extract the text content of an element |
| `desktop.get_tree_info` | Query tree view structure (expand/collapse state, children) |
| `desktop.get_table_info` | Read table/grid dimensions, headers, rows, columns, and cells |
| `desktop.click` | Click or activate an element |
| `desktop.type_text` | Type text into a text input element |
| `desktop.press_key` | Simulate a keyboard key press |
| `desktop.scroll_to_item` | Scroll a virtualized list to bring a target item into view |
| `desktop.clipboard_read` | Read the current text content of the system clipboard |
| `desktop.clipboard_write` | Write text to the system clipboard |
| `desktop.launch_app` | Launch a desktop application by name or path |
| `desktop.multi_action` | Execute a batch of 2–20 actions in a single call |
| `desktop.wait_for` | Block until a UI condition is met (async polling) |

---

## Supported Platforms

| Platform | Accessibility API |
|----------|-------------------|
| **Windows** 10+ | UI Automation |
| **Linux** (GNOME/X11) | AT-SPI2 + X11 EWMH |
| **Web** (Chrome / Edge / Brave) | Chrome DevTools Protocol |

All platforms provide identical tool behavior through the same MCP interface.

### Web Backend Setup

The web backend connects to any Chromium-based browser launched with `--remote-debugging-port`. See the [Web Backend Setup Guide](docs/web-backend-setup.md) for browser-specific instructions.

```bash
google-chrome --remote-debugging-port=9222
```

---

## Safety Model

Every action is classified as **READ_ONLY** (reads UI state — e.g. `snapshot`, `find`), **INTERACTION** (modifies app state — e.g. `click`, `type_text`), or **SENSITIVE** (affects system or cross-app state — e.g. `clipboard_write`, `launch_app`). Your agent can gate dangerous operations behind user confirmation.

Privacy controls automatically detect password fields, redact sensitive values, and support app-level denylisting.

---

## Configuration

Pathlight MCP auto-detects your platform at startup. One optional flag:

| Flag | Default | Description |
|------|---------|-------------|
| `--backend` | `auto` | Backend mode: `auto` (detect platform), `mock` (test double) |

---

## Testing

```bash
pytest                           # full suite
pytest -k "not integration"      # unit tests only (no desktop needed)
pytest --cov=pathlight-mcp       # with coverage
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and the tool authoring guide.

For release and rollback procedures, see [RELEASING.md](RELEASING.md).

---

## License

[MIT](LICENSE) — Copyright 2025–2026 Mikenahh92
AM2 TC-03 AM2-110
AM2 TC-03 AM2-111
AM2 TC-03 AM2-112
TC-06 was here

# Guidewire

**The zero-code desktop + web MCP server.** Connect any AI agent to native desktop applications and web browsers in 30 seconds — no configuration, no API keys required.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Development Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://pypi.org/project/guidewire/)
[![MCP](https://img.shields.io/badge/MCP-stdio-purple.svg)](https://modelcontextprotocol.io/)
[![Platform: Windows / Linux / Web](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20Web-lightgrey.svg)](#supported-platforms)
[![Tests](https://img.shields.io/badge/tests-65%2B%20files-brightgreen.svg)](#testing)

Guidewire turns any desktop application or web browser into a navigable accessibility tree that AI agents can see, click, type into, and control — through standard MCP tool calls. It works where Playwright cannot: native apps, system dialogs, legacy software, control panels, and any window that responds to OS accessibility APIs. It also connects to Chromium-based browsers via the Chrome DevTools Protocol for web accessibility automation.

> **New to MCP?** The [Model Context Protocol](https://modelcontextprotocol.io/) is the standard way AI agents interact with external tools. If your client supports MCP servers, it supports Guidewire.

---

## How Agents Connect

Guidewire uses the MCP **stdio transport**, which means any MCP-compatible client can connect by pointing at the `guidewire` command. No API keys, no web server, no browser extension.

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "guidewire": {
      "command": "guidewire",
      "args": []
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "guidewire": {
      "command": "guidewire",
      "args": []
    }
  }
}
```

### Windsurf

Add to `.windsurf/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "guidewire": {
      "command": "guidewire",
      "args": []
    }
  }
}
```

### VS Code (Copilot / Continue / Cline)

Add to your VS Code MCP settings:

```json
{
  "mcp": {
    "servers": {
      "guidewire": {
        "command": "guidewire",
        "args": []
      }
    }
  }
}
```

### Any MCP Client

Guidewire is a standard MCP stdio server. Point any MCP client at the `guidewire` command:

```bash
guidewire                 # auto-detect platform, start MCP server
guidewire --backend mock  # use mock backend for testing (no desktop needed)
```

### Install

> **That's it.** `pip install guidewire` + one JSON config block above = done. No API keys, no browser, no extra setup.

```bash
pip install guidewire

# With platform support (optional — auto-detected at runtime)
pip install "guidewire[windows]"    # Windows (comtypes)
# Linux X11 support (python-xlib) is included automatically on Linux
```

<details>
<summary><strong>Install from source</strong></summary>

```bash
git clone https://github.com/Mikenahh92/Guidewire.git
cd Guidewire
pip install -e ".[dev]"
```

</details>

---

## Usage Examples

Every example below is an MCP tool call. Your agent makes these calls automatically — you just describe what you want done.

### See what's on screen

```python
# 1. List all open windows — the agent picks the one it needs
windows = await desktop.list_windows()
# → [{"title": "Untitled - Notepad", "ref": "w1"}, ...]

# 2. Snapshot the accessibility tree of a window (like a DOM, but for native apps)
tree = await desktop.snapshot(window_ref="w1")
# → {"ref": "e1", "role": "window", "name": "Untitled - Notepad",
#    "children": [{"ref": "e2", "role": "text_edit", ...}]}
```

### Find and interact with elements

```python
# Find a text field by role — no CSS selectors or XPath needed
elements = await desktop.find(window_ref="w1", role="text_edit")
# → [{"ref": "e3", "role": "text_edit", "name": "Text Editor"}]

# Type into it — just like a human would
await desktop.type_text(element_ref="e3", text="Hello from Guidewire!")

# Find and click a button by name
buttons = await desktop.find(window_ref="w1", name="Save")
await desktop.click(element_ref="e4")  # clicks the Save button
```

### Wait for something to appear

```python
# Block until a "Save As" dialog appears (up to 10 seconds, checking every 500ms)
await desktop.wait_for(
    condition={"type": "element_appears", "role": "dialog", "name": "Save As"},
    timeout_ms=10000,
    interval_ms=500
)
```

### Automate a multi-step workflow

```python
# Batch 2–20 actions into a single call — faster and more reliable
await desktop.multi_action(actions=[
    {"tool": "click", "element_ref": "e5"},                          # open menu
    {"tool": "type_text", "element_ref": "e6", "text": "report.pdf"}, # type filename
    {"tool": "click", "element_ref": "e7"}                           # confirm save
])
```

### Work with tables and trees

```python
# Read table dimensions and headers
info = await desktop.get_table_info(element_ref="t1", action="info")
# → {"rows": 50, "columns": 4, "headers": ["Name", "Size", "Type", "Modified"]}

# Read a specific cell
cell = await desktop.get_table_info(element_ref="t1", action="read_cell", row=0, col=0)
# → {"value": "report.pdf"}

# Expand a tree node and read its children
tree = await desktop.get_tree_info(element_ref="e8", action="expand")
```

---

## MCP Tools

All 17 tools are available under the `desktop.` namespace immediately after connection — no setup required.

### Window Management

| Tool | What it does |
|------|-------------|
| `desktop.list_windows` | List visible top-level windows with titles and handles |
| `desktop.focus_window` | Bring a window to the foreground |
| `desktop.manage_window` | Minimize, maximize, restore, move, or resize a window |

### Element Inspection

| Tool | What it does |
|------|-------------|
| `desktop.snapshot` | Capture the accessibility tree of a window (depth-limited) |
| `desktop.find` | Find elements by role and/or name within a window |
| `desktop.get_text` | Extract the text content of an element |
| `desktop.get_tree_info` | Query tree view structure (expand/collapse state, children) |
| `desktop.get_table_info` | Read table/grid dimensions, headers, rows, columns, and cells |

### Interaction

| Tool | What it does |
|------|-------------|
| `desktop.click` | Click or activate an element |
| `desktop.type_text` | Type text into a text input element |
| `desktop.press_key` | Simulate a keyboard key press |
| `desktop.scroll_to_item` | Scroll a virtualized list to bring a target item into view |

### Clipboard

| Tool | What it does |
|------|-------------|
| `desktop.clipboard_read` | Read the current text content of the system clipboard |
| `desktop.clipboard_write` | Write text to the system clipboard |

### Orchestration

| Tool | What it does |
|------|-------------|
| `desktop.launch_app` | Launch a desktop application by name or path |
| `desktop.multi_action` | Execute a batch of 2–20 desktop actions in a single call |
| `desktop.wait_for` | Block until a UI condition is met (async polling) |

---

## Supported Platforms

| Platform | Backend | Accessibility API | Status |
|----------|---------|-------------------|--------|
| **Windows** 10+ | `WindowsBackend` | UI Automation (comtypes) | Stable |
| **Linux** (GNOME/X11) | `LinuxBackend` | AT-SPI2 (pyatspi) + X11 EWMH | Stable |
| **Web** (Chrome / Edge / Brave) | `WebBackend` | Chrome DevTools Protocol (CDP) | Stable |
| **macOS** | _Planned_ | Apple Accessibility (AXUIElement) | Not started |

All backends implement the same abstract interface, providing identical tool behavior regardless of platform.

### Web Backend Setup

The web backend connects to any Chromium-based browser launched with `--remote-debugging-port`. See the [Web Backend Setup Guide](docs/web-backend-setup.md) for browser-specific instructions.

```bash
# Launch Chrome with remote debugging
google-chrome --remote-debugging-port=9222
```

```python
from guidewire.backends.web import WebBackend

backend = WebBackend(host="localhost", port=9222)
backend.connect()
windows = backend.list_windows()  # lists open browser tabs
```

---

## Safety Model

Every action is automatically classified into one of three risk tiers — your agent can use this to gate dangerous operations behind user confirmation.

| Tier | Description | Examples |
|------|-------------|---------|
| **READ_ONLY** | No side effects — reads UI state | `snapshot`, `find`, `get_text` |
| **INTERACTION** | Modifies application state | `click`, `type_text`, `press_key` |
| **SENSITIVE** | Affects system or cross-app state | `clipboard_write`, `launch_app` |

Additional privacy controls automatically detect password fields, redact sensitive values, and support app-level denylisting.

---

## Configuration

Guidewire auto-detects your platform at startup. One optional flag:

| Flag | Default | Description |
|------|---------|-------------|
| `--backend` | `auto` | Backend mode: `auto` (detect platform), `mock` (test double) |

---

## Testing

Guidewire has a comprehensive test suite covering unit, integration, and end-to-end scenarios:

| Category | Scope | Count |
|----------|-------|-------|
| **Unit tests** | Tools, models, backends, safety, privacy, errors, refs | 65+ files |
| **Integration tests** | Live app interaction on Windows and Linux | 7 apps, 43 cases |
| **Golden snapshots** | Platform-specific element tree fixtures | Per-app fixtures |
| **Agent harness** | End-to-end replay with Anthropic SDK | Live model tests |

```bash
# Run full suite
pytest

# Run with coverage
pytest --cov=guidewire

# Run only unit tests (no desktop needed)
pytest -k "not integration"
```

---

## Roadmap

| Phase | Focus | Status |
|-------|-------|--------|
| **Phase 1** | Core server, Windows & Linux backends, 17 MCP tools, element refs, safety model | ✅ Complete |
| **Phase 2** | Clipboard read/write, structured element data, platform normalization | ✅ Complete |
| **Phase 3** | Error hints with recovery suggestions, `wait_for` async polling, `multi_action` batch execution | ✅ Complete |
| **Phase 4** | Web accessibility backend (Chrome DevTools Protocol), browser tab control, iframe support | ✅ Complete |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, architecture details, and the tool authoring guide.

For release and rollback procedures, see [RELEASING.md](RELEASING.md).

---

## License

[MIT](LICENSE) — Copyright 2025–2026 Mikenahh92

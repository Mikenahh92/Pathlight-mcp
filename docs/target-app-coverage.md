# Target App Coverage Report

Summary of snapshot capture, integration test coverage, and observed accessibility gaps across target applications for the Pathlight MCP MVP.

## Windows (UIA Backend)

### Notepad

- **Snapshot**: Captured (`tests/fixtures/windows/notepad_snapshot.json`)
- **Integration test**: `tests/integration/test_windows_agent_notepad.py` — 6 tests
- **Workflow**: list_windows → snapshot → find → type_text → get_text
- **Status**: Full coverage. Text editor element is directly accessible via UIA `text_input` role.

### Windows Settings

- **Snapshot**: Captured (`tests/fixtures/windows/settings_snapshot.json`)
- **Integration test**: `tests/integration/test_windows_agent_settings.py` — 5 tests
- **Workflow**: list_windows → snapshot → find → type_text → get_text
- **Status**: Full coverage. XAML Islands app with navigation pane and search bar. Search box accessible via `text_input` role.

### File Explorer

- **Snapshot**: Captured (`tests/fixtures/windows/file_explorer_snapshot.json`)
- **Integration test**: `tests/integration/test_windows_agent_file_explorer.py` — 5 tests
- **Workflow**: list_windows → snapshot → find → get_text
- **Status**: Full coverage. Win32 shell app with address bar, tree pane, and item grid. Address bar located by name.

### Calculator (cross-app only)

- **Snapshot**: Captured (`tests/fixtures/windows/calculator_snapshot.json`)
- **Integration test**: `tests/integration/test_windows_agent_cross_app.py` — 8 tests
- **Workflow**: list_windows → snapshot → find → get_text → focus_window → snapshot → find → type_text → get_text
- **Status**: Full coverage. Used as source app in cross-app workflow with Notepad.

## Linux (AT-SPI2 Backend)

### gedit

- **Snapshot**: Captured (`tests/fixtures/linux/gedit_snapshot.json`)
- **Integration test**: `tests/integration/test_linux_agent_gedit.py` — 6 tests
- **Workflow**: list_windows → snapshot → find → type_text → get_text
- **Status**: Full coverage. GTK3 text editor. Text area accessible via `text` role.

### GNOME Calculator

- **Snapshot**: Captured (`tests/fixtures/linux/gnome_calculator_snapshot.json`)
- **Integration test**: `tests/integration/test_linux_agent_cross_app.py` — 8 tests
- **Workflow**: list_windows → snapshot → find → get_text → focus_window → snapshot → find → type_text → get_text
- **Status**: Full coverage. Used as source app in cross-app workflow with gedit.

### Nautilus (Files)

- **Snapshot**: Captured (`tests/fixtures/linux/nautilus_snapshot.json`)
- **Integration test**: `tests/integration/test_linux_agent_nautilus.py` — 5 tests
- **Workflow**: list_windows → snapshot → find → get_text
- **Status**: Full coverage. GTK4 file manager with sidebar and content grid. Path bar located by name.

## Accessibility Gaps

| Gap | Platform | App | Detail |
|-----|----------|-----|--------|
| Complex nested menus | Windows | Settings | Settings uses deep XAML navigation; sub-page content behind scrollable regions may require additional snapshot calls |
| Dynamic list content | Windows | File Explorer | Item grid contents change on navigation; snapshots are point-in-time |
| Toolbar button labels | Windows | File Explorer | Small-icon toolbar buttons may have truncated or missing `Name` properties in UIA |
| Wayland window focus | Linux | All | `focus_window` may be restricted under Wayland compositors (works on X11/XWayland) |
| GTK4 tree view | Linux | Nautilus | Sidebar tree items use recursive ATK roles; `find` by role may return multiple matches |

## Test Coverage Summary

| Metric | Count |
|--------|-------|
| Target applications | 7 |
| Golden snapshot fixtures | 7 |
| Integration test files | 7 |
| Integration test cases | 43 |
| Platforms covered | 2 (Windows, Linux) |
| Backend APIs exercised | 2 (UIA, AT-SPI2) |
| MCP tools exercised | 8 / 8 |

All 8 MCP tools (`list_windows`, `focus_window`, `snapshot`, `find`, `click`, `type_text`, `press_key`, `get_text`) have schema validation tests on both platforms. The `click` and `press_key` tools are validated as *not called* in read-only and type-driven workflows, confirming they are available but not required for the covered scenarios.

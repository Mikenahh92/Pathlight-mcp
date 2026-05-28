# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- git-cliff:START -->
## [Unreleased]

## [0.1.0] — 2025-05-21

### Added

- Professional 12-section README with badges, architecture diagram, tools reference, and usage examples ([#GW-069](https://github.com/Mikenahh92/Guidewire/issues/GW-069))
- CHANGELOG.md ([#GW-069](https://github.com/Mikenahh92/Guidewire/issues/GW-069))
- MIT LICENSE, CODE_OF_CONDUCT.md, SECURITY.md, dependabot.yml ([#GW-072](https://github.com/Mikenahh92/Guidewire/issues/GW-072))

### Fixed

- `launch_app` now propagates DISPLAY on Linux, resolves snap binary paths, and validates post-launch liveness ([#GW-068](https://github.com/Mikenahh92/Guidewire/issues/GW-068))

### Added — Phase 3: Agent Workflow Quality

- `desktop.multi_action` tool — batch 2–20 actions with per-action safety classification and stop-on-first-error ([#GW-066](https://github.com/Mikenahh92/Guidewire/issues/GW-066))
- Error hint integration — context-aware recovery hints on all 14 MCP tool handlers ([#GW-062](https://github.com/Mikenahh92/Guidewire/issues/GW-062))
- Error hint infrastructure — extend `PathlightMCPError` with hints field and hint registry module ([#GW-061](https://github.com/Mikenahh92/Guidewire/issues/GW-061))
- `desktop.wait_for` tool — async polling with 4 condition types (element_appears, element_disappears, text_equals, state_change) ([#GW-064](https://github.com/Mikenahh92/Guidewire/issues/GW-064))
- Wait tool architecture spike — async handler validation and condition DSL feasibility ([#GW-063](https://github.com/Mikenahh92/Guidewire/issues/GW-063))
- Multi-action design and safety classification ([#GW-065](https://github.com/Mikenahh92/Guidewire/issues/GW-065))
- Live agent prompt test — first live-model integration test with Anthropic SDK AgentClient ([#GW-059](https://github.com/Mikenahh92/Guidewire/issues/GW-059))

### Added — Phase 2: Enhanced Agent Environment

- `desktop.launch_app` tool — launch desktop applications by name or path ([#GW-054](https://github.com/Mikenahh92/Guidewire/issues/GW-054))
- `desktop.scroll_to_item` tool — scroll virtualized lists to bring target items into view ([#GW-052](https://github.com/Mikenahh92/Guidewire/issues/GW-052))
- `desktop.manage_window` tool — minimize, maximize, restore, move, resize windows ([#GW-055](https://github.com/Mikenahh92/Guidewire/issues/GW-055))
- `desktop.get_table_info` tool — read table/grid dimensions, headers, rows, and cells ([#GW-049](https://github.com/Mikenahh92/Guidewire/issues/GW-049))
- `desktop.get_tree_info` tool — query tree view structure and expand/collapse state ([#GW-050](https://github.com/Mikenahh92/Guidewire/issues/GW-050))
- `desktop.clipboard_read` tool — read text from system clipboard with privacy redaction ([#GW-045](https://github.com/Mikenahh92/Guidewire/issues/GW-045))
- `desktop.clipboard_write` tool — write text to system clipboard ([#GW-046](https://github.com/Mikenahh92/Guidewire/issues/GW-046))
- Clipboard privacy redaction (`redact_clipboard_text`) ([#GW-044](https://github.com/Mikenahh92/Guidewire/issues/GW-044))
- Clipboard integration tests ([#GW-047](https://github.com/Mikenahh92/Guidewire/issues/GW-047))
- Selection pattern support — multi-select, deselect, add-to-selection across all backends ([#GW-051](https://github.com/Mikenahh92/Guidewire/issues/GW-051))
- NormalizedElement schema extension for table/tree/selection fields ([#GW-048](https://github.com/Mikenahh92/Guidewire/issues/GW-048))
- `classify_system_action()` for non-element risk classification ([#GW-053](https://github.com/Mikenahh92/Guidewire/issues/GW-053))
- Target app coverage tests for Windows Settings, File Explorer, Linux Nautilus ([#GW-042](https://github.com/Mikenahh92/Guidewire/issues/GW-042))

### Added — Phase 1: Core Platform Backends and MCP Tools

- Agent test harness — MCP server subprocess bootstrapping, AI agent client, tool usage assertions ([#GW-037](https://github.com/Mikenahh92/Guidewire/issues/GW-037))
- Windows agent integration test — single-app Notepad text entry and readback ([#GW-038](https://github.com/Mikenahh92/Guidewire/issues/GW-038))
- Windows cross-app agent test — Calculator to Notepad multi-turn workflow ([#GW-039](https://github.com/Mikenahh92/Guidewire/issues/GW-039))
- Linux agent integration test — single-app gedit text entry and readback ([#GW-040](https://github.com/Mikenahh92/Guidewire/issues/GW-040))
- Linux cross-app agent test — GNOME Calculator to gedit multi-turn workflow ([#GW-041](https://github.com/Mikenahh92/Guidewire/issues/GW-041))
- Windows Backend — full implementation using UI Automation via comtypes ([#GW-019](https://github.com/Mikenahh92/Guidewire/issues/GW-019), [#GW-020](https://github.com/Mikenahh92/Guidewire/issues/GW-020), [#GW-021](https://github.com/Mikenahh92/Guidewire/issues/GW-021), [#GW-022](https://github.com/Mikenahh92/Guidewire/issues/GW-022), [#GW-023](https://github.com/Mikenahh92/Guidewire/issues/GW-023), [#GW-024](https://github.com/Mikenahh92/Guidewire/issues/GW-024))
- Windows normalization — cross-platform property mapping ([#GW-025](https://github.com/Mikenahh92/Guidewire/issues/GW-025))
- Windows golden snapshot fixtures for Notepad, Calculator, Settings ([#GW-026](https://github.com/Mikenahh92/Guidewire/issues/GW-026))
- Windows unit tests ([#GW-027](https://github.com/Mikenahh92/Guidewire/issues/GW-027))
- Linux Backend — full implementation using AT-SPI2 via pyatspi ([#GW-028](https://github.com/Mikenahh92/Guidewire/issues/GW-028), [#GW-029](https://github.com/Mikenahh92/Guidewire/issues/GW-029), [#GW-030](https://github.com/Mikenahh92/Guidewire/issues/GW-030), [#GW-031](https://github.com/Mikenahh92/Guidewire/issues/GW-031), [#GW-032](https://github.com/Mikenahh92/Guidewire/issues/GW-032), [#GW-033](https://github.com/Mikenahh92/Guidewire/issues/GW-033))
- Linux normalization, golden snapshot fixtures, unit tests ([#GW-034](https://github.com/Mikenahh92/Guidewire/issues/GW-034), [#GW-035](https://github.com/Mikenahh92/Guidewire/issues/GW-035), [#GW-036](https://github.com/Mikenahh92/Guidewire/issues/GW-036))
- `desktop.list_windows` tool ([#GW-010](https://github.com/Mikenahh92/Guidewire/issues/GW-010))
- `desktop.focus_window` tool ([#GW-011](https://github.com/Mikenahh92/Guidewire/issues/GW-011))
- `desktop.snapshot` tool
- `desktop.find` tool ([#GW-013](https://github.com/Mikenahh92/Guidewire/issues/GW-013))
- `desktop.click` tool ([#GW-012](https://github.com/Mikenahh92/Guidewire/issues/GW-012))
- `desktop.type_text` tool ([#GW-015](https://github.com/Mikenahh92/Guidewire/issues/GW-015))
- `desktop.press_key` tool ([#GW-016](https://github.com/Mikenahh92/Guidewire/issues/GW-016))
- `desktop.get_text` tool ([#GW-017](https://github.com/Mikenahh92/Guidewire/issues/GW-017))
- Comprehensive MCP tool handler tests ([#GW-018](https://github.com/Mikenahh92/Guidewire/issues/GW-018))

### Added — Foundation

- Python project scaffold — pyproject.toml, src layout, ruff, pytest ([#GW-001](https://github.com/Mikenahh92/Guidewire/issues/GW-001))
- `DesktopBackend` ABC with abstract methods, `NativeHandle` type, `MockBackend` test double ([#GW-002](https://github.com/Mikenahh92/Guidewire/issues/GW-002))
- `ElementRefStore` — short string references to native handles ([#GW-003](https://github.com/Mikenahh92/Guidewire/issues/GW-003))
- `NormalizedElement` dataclass and role/state/action mapping tables ([#GW-004](https://github.com/Mikenahh92/Guidewire/issues/GW-004))
- Structured error codes — 8 exception classes with error codes ([#GW-005](https://github.com/Mikenahh92/Guidewire/issues/GW-005))
- Safety module — three-tier risk classification model ([#GW-006](https://github.com/Mikenahh92/Guidewire/issues/GW-006), [#GW-008](https://github.com/Mikenahh92/Guidewire/issues/GW-008))
- Privacy controls — password field detection, value redaction, app denylisting ([#GW-007](https://github.com/Mikenahh92/Guidewire/issues/GW-007))
- Unit tests for shared core modules ([#GW-009](https://github.com/Mikenahh92/Guidewire/issues/GW-009))

<!-- git-cliff:END -->

[Unreleased]: https://github.com/Mikenahh92/Guidewire/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Mikenahh92/Guidewire/releases/tag/v0.1.0

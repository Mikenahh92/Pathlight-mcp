"""Golden snapshot fixture tests for Windows backend regression (GW-026).

Validates that JSON golden fixtures representing raw pre-normalization
accessibility tree snapshots from Notepad, Calculator, and Settings:
- Have a valid ``_metadata`` envelope.
- Conform to the raw snapshot schema (control_type, is_enabled, patterns, ...).
- Contain realistic control types, bounds, and pattern values.
- Have consistent structural elements for each application.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.helpers import (
    FIXTURES_DIR,
    compare_snapshots,
    load_golden_snapshot,
)

# ---------------------------------------------------------------------------
# Raw snapshot schema constants
# ---------------------------------------------------------------------------

_VALID_CONTROL_TYPES = {
    50000,  # Button
    50001,  # Calendar
    50002,  # CheckBox
    50003,  # ComboBox
    50004,  # Edit
    50005,  # Hyperlink
    50006,  # Image
    50007,  # ListItem
    50008,  # List
    50009,  # Menu
    50010,  # MenuBar
    50011,  # MenuItem
    50012,  # Pane
    50013,  # ProgressBar
    50014,  # RadioButton
    50015,  # ScrollBar
    50016,  # SemanticZoom
    50017,  # Separator
    50018,  # Slider
    50019,  # Spinner
    50020,  # StatusBar (used as Text)
    50021,  # Tab
    50022,  # TabItem
    50023,  # Text
    50024,  # Thumb
    50025,  # TitleBar
    50026,  # ToolBar
    50027,  # ToolTip
    50028,  # Tree
    50029,  # TreeItem
    50030,  # Custom
    50031,  # Group
    50032,  # Window
    50033,  # Pane (TitleBar)
    50034,  # Header
    50035,  # HeaderItem
    50036,  # DataGrid
    50037,  # DataItem
    50038,  # Document
}

_VALID_PATTERN_NAMES = {
    "Invoke",
    "Selection",
    "SelectionItem",
    "Value",
    "Text",
    "Toggle",
    "ExpandCollapse",
    "Scroll",
    "RangeValue",
    "Grid",
    "GridItem",
    "Table",
    "TableItem",
    "Transform",
    "Dock",
    "LegacyIAccessible",
    "Annotation",
    "Drag",
    "DropTarget",
    "ItemContainer",
    "MultipleView",
    "ObjectModel",
    "Spreadsheet",
    "Styles",
    "SynchronizedInput",
    "VirtualizedItem",
}


# ---------------------------------------------------------------------------
# Schema validation helpers
# ---------------------------------------------------------------------------


def _collect_all_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a snapshot tree into a list of all node dicts."""
    result = [node]
    for child in node.get("children", []):
        result.extend(_collect_all_nodes(child))
    return result


def _validate_node_schema(
    node: dict[str, Any],
    path: str = "root",
) -> list[str]:
    """Validate a single node against the raw snapshot schema.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []

    # control_type must be an int in the known set
    ct = node.get("control_type")
    if ct is None:
        errors.append(f"{path}: missing 'control_type'")
    elif not isinstance(ct, int):
        errors.append(f"{path}: 'control_type' must be int, got {type(ct).__name__}")
    elif ct not in _VALID_CONTROL_TYPES:
        errors.append(f"{path}: unknown control_type {ct}")

    # name must be str or null
    name = node.get("name")
    if name is not None and not isinstance(name, str):
        errors.append(f"{path}: 'name' must be str or null, got {type(name).__name__}")

    # value must be str or null
    value = node.get("value")
    if value is not None and not isinstance(value, str):
        errors.append(f"{path}: 'value' must be str or null, got {type(value).__name__}")

    # is_enabled must be bool
    if "is_enabled" not in node:
        errors.append(f"{path}: missing 'is_enabled'")
    elif not isinstance(node["is_enabled"], bool):
        errors.append(f"{path}: 'is_enabled' must be bool, got {type(node['is_enabled']).__name__}")

    # is_offscreen must be bool
    if "is_offscreen" not in node:
        errors.append(f"{path}: missing 'is_offscreen'")
    elif not isinstance(node["is_offscreen"], bool):
        errors.append(
            f"{path}: 'is_offscreen' must be bool, got {type(node['is_offscreen']).__name__}"
        )

    # bounds must be a dict with x, y, width, height (all numeric)
    bounds = node.get("bounds")
    if bounds is not None:
        if not isinstance(bounds, dict):
            errors.append(f"{path}: 'bounds' must be dict, got {type(bounds).__name__}")
        else:
            for key in ("x", "y", "width", "height"):
                if key not in bounds:
                    errors.append(f"{path}: 'bounds' missing '{key}'")
                elif not isinstance(bounds[key], (int, float)):
                    errors.append(
                        f"{path}: bounds.{key} must be numeric, got {type(bounds[key]).__name__}"
                    )

    # patterns must be a list of valid pattern name strings
    patterns = node.get("patterns")
    if patterns is None:
        errors.append(f"{path}: missing 'patterns'")
    elif not isinstance(patterns, list):
        errors.append(f"{path}: 'patterns' must be list, got {type(patterns).__name__}")
    else:
        for i, pat in enumerate(patterns):
            if not isinstance(pat, str):
                errors.append(f"{path}: patterns[{i}] must be str")
            elif pat not in _VALID_PATTERN_NAMES:
                errors.append(f"{path}: patterns[{i}] unknown pattern '{pat}'")

    # children must be a list
    children = node.get("children")
    if children is None:
        errors.append(f"{path}: missing 'children'")
    elif not isinstance(children, list):
        errors.append(f"{path}: 'children' must be list, got {type(children).__name__}")

    return errors


def _validate_tree_schema(tree: dict[str, Any]) -> list[str]:
    """Validate the entire raw snapshot tree recursively."""
    errors: list[str] = []
    nodes = _collect_all_nodes(tree)
    for node in nodes:
        path = node.get("name", str(node.get("control_type", "unknown")))
        errors.extend(_validate_node_schema(node, path))
    return errors


def _validate_metadata(metadata: dict[str, Any]) -> list[str]:
    """Validate the _metadata envelope."""
    errors: list[str] = []
    required_keys = {
        "captured_at",
        "os_version",
        "app_name",
        "guidewire_version",
        "max_depth",
        "max_nodes",
    }
    for key in required_keys:
        if key not in metadata:
            errors.append(f"_metadata: missing '{key}'")
    if "max_depth" in metadata and not isinstance(metadata["max_depth"], int):
        errors.append("_metadata: 'max_depth' must be int")
    if "max_nodes" in metadata and not isinstance(metadata["max_nodes"], int):
        errors.append("_metadata: 'max_nodes' must be int")
    return errors


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

_FIXTURE_NAMES = [
    "notepad_snapshot.json",
    "calculator_snapshot.json",
    "settings_snapshot.json",
    "file_explorer_snapshot.json",
]


@pytest.fixture(params=_FIXTURE_NAMES)
def golden_snapshot(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Load a golden snapshot fixture by filename."""
    return load_golden_snapshot(request.param)


@pytest.fixture(params=_FIXTURE_NAMES)
def snapshot_tree(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Load just the snapshot tree (without _metadata envelope)."""
    data = load_golden_snapshot(request.param)
    return data["snapshot"]


# ---------------------------------------------------------------------------
# Metadata envelope tests
# ---------------------------------------------------------------------------


class TestMetadataEnvelope:
    """All golden fixtures have a valid _metadata envelope."""

    def test_has_metadata_key(self, golden_snapshot: dict[str, Any]) -> None:
        """Fixture must have a top-level '_metadata' key."""
        assert "_metadata" in golden_snapshot

    def test_has_snapshot_key(self, golden_snapshot: dict[str, Any]) -> None:
        """Fixture must have a top-level 'snapshot' key."""
        assert "snapshot" in golden_snapshot

    def test_metadata_has_required_fields(self, golden_snapshot: dict[str, Any]) -> None:
        """_metadata must contain all required fields."""
        errors = _validate_metadata(golden_snapshot["_metadata"])
        assert errors == [], f"Metadata errors: {errors}"

    def test_metadata_max_depth_is_positive(self, golden_snapshot: dict[str, Any]) -> None:
        """_metadata.max_depth must be a positive integer."""
        assert golden_snapshot["_metadata"]["max_depth"] > 0

    def test_metadata_max_nodes_is_positive(self, golden_snapshot: dict[str, Any]) -> None:
        """_metadata.max_nodes must be a positive integer."""
        assert golden_snapshot["_metadata"]["max_nodes"] > 0


# ---------------------------------------------------------------------------
# Raw snapshot schema conformance tests
# ---------------------------------------------------------------------------


class TestRawSnapshotSchema:
    """All golden fixtures conform to the raw pre-normalization snapshot schema."""

    def test_root_is_window(self, snapshot_tree: dict[str, Any]) -> None:
        """Root element must have control_type 50032 (Window)."""
        assert snapshot_tree["control_type"] == 50032

    def test_root_has_bounds(self, snapshot_tree: dict[str, Any]) -> None:
        """Root element must have non-empty bounds."""
        bounds = snapshot_tree.get("bounds")
        assert bounds is not None
        assert bounds["width"] > 0
        assert bounds["height"] > 0

    def test_root_is_enabled(self, snapshot_tree: dict[str, Any]) -> None:
        """Root element must be enabled."""
        assert snapshot_tree["is_enabled"] is True

    def test_root_is_not_offscreen(self, snapshot_tree: dict[str, Any]) -> None:
        """Root element must not be offscreen."""
        assert snapshot_tree["is_offscreen"] is False

    def test_no_schema_errors(self, snapshot_tree: dict[str, Any]) -> None:
        """Full tree must have zero schema validation errors."""
        errors = _validate_tree_schema(snapshot_tree)
        assert errors == [], f"Schema errors: {errors}"

    def test_all_control_types_are_valid(self, snapshot_tree: dict[str, Any]) -> None:
        """All control_type values in the tree must be recognized UIA types."""
        nodes = _collect_all_nodes(snapshot_tree)
        for node in nodes:
            assert node["control_type"] in _VALID_CONTROL_TYPES, (
                f"Unknown control_type {node['control_type']} for {node.get('name', '?')}"
            )

    def test_all_patterns_are_valid(self, snapshot_tree: dict[str, Any]) -> None:
        """All pattern names in the tree must be recognized UIA patterns."""
        nodes = _collect_all_nodes(snapshot_tree)
        for node in nodes:
            for pat in node.get("patterns", []):
                assert pat in _VALID_PATTERN_NAMES, (
                    f"Unknown pattern '{pat}' on {node.get('name', '?')}"
                )


# ---------------------------------------------------------------------------
# Notepad-specific structure tests
# ---------------------------------------------------------------------------


class TestNotepadFixture:
    """Notepad golden fixture has expected structural elements."""

    @pytest.fixture()
    def notepad(self) -> dict[str, Any]:
        return load_golden_snapshot("notepad_snapshot.json")["snapshot"]

    def test_has_title_bar(self, notepad: dict[str, Any]) -> None:
        """Notepad must have a title bar child (control_type 50033)."""
        children = notepad.get("children", [])
        cts = {c["control_type"] for c in children}
        assert 50033 in cts

    def test_has_menu_bar(self, notepad: dict[str, Any]) -> None:
        """Notepad must have a menu bar child (control_type 50010)."""
        children = notepad.get("children", [])
        cts = {c["control_type"] for c in children}
        assert 50010 in cts

    def test_menu_bar_has_file_and_edit(self, notepad: dict[str, Any]) -> None:
        """Notepad menu bar must contain File and Edit items."""
        menu_bar = next(
            (c for c in notepad.get("children", []) if c["control_type"] == 50010),
            None,
        )
        assert menu_bar is not None
        names = {c["name"] for c in menu_bar.get("children", [])}
        assert "File" in names
        assert "Edit" in names

    def test_has_document_area(self, notepad: dict[str, Any]) -> None:
        """Notepad must have a document child (control_type 50038)."""
        children = notepad.get("children", [])
        cts = {c["control_type"] for c in children}
        assert 50038 in cts

    def test_has_status_bar(self, notepad: dict[str, Any]) -> None:
        """Notepad must have a status bar child (control_type 50020)."""
        children = notepad.get("children", [])
        cts = {c["control_type"] for c in children}
        assert 50034 in cts

    def test_document_has_edit_child(self, notepad: dict[str, Any]) -> None:
        """Notepad document must contain an Edit child (control_type 50004)."""
        document = next(
            (c for c in notepad.get("children", []) if c["control_type"] == 50038),
            None,
        )
        assert document is not None
        child_cts = {c["control_type"] for c in document.get("children", [])}
        assert 50004 in child_cts

    def test_edit_is_enabled(self, notepad: dict[str, Any]) -> None:
        """Notepad edit control must be enabled."""
        document = next(
            (c for c in notepad.get("children", []) if c["control_type"] == 50038),
            None,
        )
        assert document is not None
        edit = next(
            (c for c in document.get("children", []) if c["control_type"] == 50004),
            None,
        )
        assert edit is not None
        assert edit["is_enabled"] is True

    def test_menu_items_have_invoke_pattern(self, notepad: dict[str, Any]) -> None:
        """Notepad menu items must support the Invoke pattern."""
        menu_bar = next(
            (c for c in notepad.get("children", []) if c["control_type"] == 50010),
            None,
        )
        assert menu_bar is not None
        for item in menu_bar.get("children", []):
            assert "Invoke" in item.get("patterns", []), (
                f"Menu item '{item.get('name')}' missing Invoke pattern"
            )

    def test_element_count(self, notepad: dict[str, Any]) -> None:
        """Notepad fixture should have at least 10 elements."""
        count = len(_collect_all_nodes(notepad))
        assert count >= 10


# ---------------------------------------------------------------------------
# Calculator-specific structure tests
# ---------------------------------------------------------------------------


class TestCalculatorFixture:
    """Calculator golden fixture has expected structural elements."""

    @pytest.fixture()
    def calculator(self) -> dict[str, Any]:
        return load_golden_snapshot("calculator_snapshot.json")["snapshot"]

    def test_has_title_bar(self, calculator: dict[str, Any]) -> None:
        """Calculator must have a title bar child."""
        children = calculator.get("children", [])
        cts = {c["control_type"] for c in children}
        assert 50033 in cts

    def test_has_display_group(self, calculator: dict[str, Any]) -> None:
        """Calculator must have a group named 'Display'."""
        groups = [
            c
            for c in calculator.get("children", [])
            if c["control_type"] == 50026 and c.get("name") == "Display"
        ]
        assert len(groups) == 1

    def test_display_has_value_field(self, calculator: dict[str, Any]) -> None:
        """Calculator display must contain an Edit with a value."""
        display = next(
            (
                c
                for c in calculator.get("children", [])
                if c["control_type"] == 50026 and c.get("name") == "Display"
            ),
            None,
        )
        assert display is not None
        edits = [c for c in display.get("children", []) if c["control_type"] == 50004]
        assert len(edits) >= 1
        assert edits[0].get("value") is not None

    def test_has_digit_buttons(self, calculator: dict[str, Any]) -> None:
        """Calculator must have buttons for digits 0-9."""
        nodes = _collect_all_nodes(calculator)
        button_names = {n["name"] for n in nodes if n["control_type"] == 50000}
        for digit_name in (
            "Zero",
            "One",
            "Two",
            "Three",
            "Four",
            "Five",
            "Six",
            "Seven",
            "Eight",
            "Nine",
        ):
            assert digit_name in button_names, f"Missing button '{digit_name}'"

    def test_has_operator_buttons(self, calculator: dict[str, Any]) -> None:
        """Calculator must have operator buttons."""
        nodes = _collect_all_nodes(calculator)
        button_names = {n["name"] for n in nodes if n["control_type"] == 50000}
        for op_name in ("Plus", "Minus", "Multiply by", "Divide by", "Equals"):
            assert op_name in button_names, f"Missing operator button '{op_name}'"

    def test_all_buttons_have_invoke_pattern(self, calculator: dict[str, Any]) -> None:
        """All calculator buttons must support the Invoke pattern."""
        nodes = _collect_all_nodes(calculator)
        buttons = [n for n in nodes if n["control_type"] == 50000]
        assert len(buttons) > 0
        for button in buttons:
            assert "Invoke" in button.get("patterns", []), (
                f"Button '{button.get('name')}' missing Invoke pattern"
            )

    def test_memory_button_is_disabled(self, calculator: dict[str, Any]) -> None:
        """Calculator memory button should be disabled."""
        nodes = _collect_all_nodes(calculator)
        memory_btn = next(
            (n for n in nodes if n["control_type"] == 50000 and n.get("name") == "Memory"),
            None,
        )
        assert memory_btn is not None
        assert memory_btn["is_enabled"] is False

    def test_element_count(self, calculator: dict[str, Any]) -> None:
        """Calculator fixture should have at least 20 elements."""
        count = len(_collect_all_nodes(calculator))
        assert count >= 20


# ---------------------------------------------------------------------------
# Windows Settings-specific structure tests
# ---------------------------------------------------------------------------


class TestWindowsSettingsFixture:
    """Windows Settings golden fixture has expected structural elements."""

    @pytest.fixture()
    def settings(self) -> dict[str, Any]:
        return load_golden_snapshot("settings_snapshot.json")["snapshot"]

    def test_has_title_bar(self, settings: dict[str, Any]) -> None:
        """Settings must have a title bar child."""
        children = settings.get("children", [])
        cts = {c["control_type"] for c in children}
        assert 50033 in cts

    def test_has_search_box(self, settings: dict[str, Any]) -> None:
        """Settings must have a search Edit control."""
        nodes = _collect_all_nodes(settings)
        search_edits = [
            n for n in nodes if n["control_type"] == 50004 and "Find" in (n.get("name") or "")
        ]
        assert len(search_edits) >= 1

    def test_has_navigation_list(self, settings: dict[str, Any]) -> None:
        """Settings must have a list for navigation (control_type 50008)."""
        children = settings.get("children", [])
        cts = {c["control_type"] for c in children}
        assert 50008 in cts

    def test_has_content_pane(self, settings: dict[str, Any]) -> None:
        """Settings must have a pane for content (control_type 50025)."""
        children = settings.get("children", [])
        cts = {c["control_type"] for c in children}
        assert 50025 in cts

    def test_navigation_has_common_items(self, settings: dict[str, Any]) -> None:
        """Settings nav must include System, Personalization, and Windows Update."""
        nav = next(
            (c for c in settings.get("children", []) if c["control_type"] == 50008),
            None,
        )
        assert nav is not None
        nav_names = {c["name"] for c in nav.get("children", [])}
        assert "System" in nav_names
        assert "Personalization" in nav_names
        assert "Windows Update" in nav_names

    def test_nav_items_have_invoke_and_selection_item(self, settings: dict[str, Any]) -> None:
        """Navigation list items must support Invoke and SelectionItem patterns."""
        nav = next(
            (c for c in settings.get("children", []) if c["control_type"] == 50008),
            None,
        )
        assert nav is not None
        for item in nav.get("children", []):
            patterns = item.get("patterns", [])
            assert "Invoke" in patterns, f"Nav item '{item.get('name')}' missing Invoke pattern"
            assert "SelectionItem" in patterns, (
                f"Nav item '{item.get('name')}' missing SelectionItem pattern"
            )

    def test_one_nav_item_is_selected(self, settings: dict[str, Any]) -> None:
        """At least one navigation item should have SelectionItem pattern."""
        nav = next(
            (c for c in settings.get("children", []) if c["control_type"] == 50008),
            None,
        )
        assert nav is not None
        selectable = [
            c for c in nav.get("children", []) if "SelectionItem" in c.get("patterns", [])
        ]
        assert len(selectable) >= 1

    def test_content_pane_has_page_title(self, settings: dict[str, Any]) -> None:
        """Content pane must contain a Text element for the page title."""
        content = next(
            (c for c in settings.get("children", []) if c["control_type"] == 50025),
            None,
        )
        assert content is not None
        texts = [c for c in content.get("children", []) if c["control_type"] == 50020]
        assert len(texts) >= 1

    def test_content_pane_has_setting_groups(self, settings: dict[str, Any]) -> None:
        """Content pane must contain group elements for setting categories."""
        content = next(
            (c for c in settings.get("children", []) if c["control_type"] == 50025),
            None,
        )
        assert content is not None
        groups = [c for c in content.get("children", []) if c["control_type"] == 50026]
        assert len(groups) >= 3

    def test_setting_groups_have_buttons(self, settings: dict[str, Any]) -> None:
        """Each setting group must contain at least one button."""
        content = next(
            (c for c in settings.get("children", []) if c["control_type"] == 50025),
            None,
        )
        assert content is not None
        groups = [c for c in content.get("children", []) if c["control_type"] == 50026]
        for group in groups:
            buttons = [c for c in group.get("children", []) if c["control_type"] == 50000]
            assert len(buttons) >= 1, f"Setting group '{group.get('name')}' has no buttons"

    def test_element_count(self, settings: dict[str, Any]) -> None:
        """Windows Settings fixture should have at least 20 elements."""
        count = len(_collect_all_nodes(settings))
        assert count >= 20


# ---------------------------------------------------------------------------
# Fixture file integrity tests
# ---------------------------------------------------------------------------


class TestFixtureFiles:
    """Golden fixture files are well-formed and loadable."""

    def test_notepad_file_exists(self) -> None:
        """notepad_snapshot.json must exist in the fixtures/windows/ directory."""
        assert (FIXTURES_DIR / "notepad_snapshot.json").is_file()

    def test_calculator_file_exists(self) -> None:
        """calculator_snapshot.json must exist in the fixtures/windows/ directory."""
        assert (FIXTURES_DIR / "calculator_snapshot.json").is_file()

    def test_settings_file_exists(self) -> None:
        """settings_snapshot.json must exist in the fixtures/windows/ directory."""
        assert (FIXTURES_DIR / "settings_snapshot.json").is_file()

    def test_notepad_is_valid_json(self) -> None:
        """notepad_snapshot.json must be valid JSON."""
        data = load_golden_snapshot("notepad_snapshot.json")
        assert isinstance(data, dict)

    def test_calculator_is_valid_json(self) -> None:
        """calculator_snapshot.json must be valid JSON."""
        data = load_golden_snapshot("calculator_snapshot.json")
        assert isinstance(data, dict)

    def test_settings_is_valid_json(self) -> None:
        """settings_snapshot.json must be valid JSON."""
        data = load_golden_snapshot("settings_snapshot.json")
        assert isinstance(data, dict)

    def test_no_extra_fixture_files(self) -> None:
        """Only the four expected snapshot files should exist."""
        json_files = sorted(FIXTURES_DIR.glob("*.json"))
        names = {f.name for f in json_files}
        assert names == {
            "calculator_snapshot.json",
            "file_explorer_snapshot.json",
            "notepad_snapshot.json",
            "settings_snapshot.json",
        }


# ---------------------------------------------------------------------------
# compare_snapshots helper tests
# ---------------------------------------------------------------------------


class TestCompareSnapshots:
    """compare_snapshots helper correctly detects differences."""

    def test_identical_trees(self) -> None:
        """Identical trees produce no differences."""
        tree = {
            "control_type": 50032,
            "name": "Test",
            "value": None,
            "is_enabled": True,
            "is_offscreen": False,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "patterns": [],
            "children": [],
        }
        assert compare_snapshots(tree, tree) == []

    def test_different_name(self) -> None:
        """Different names are detected."""
        base = {
            "control_type": 50032,
            "name": "A",
            "value": None,
            "is_enabled": True,
            "is_offscreen": False,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "patterns": [],
            "children": [],
        }
        other = {
            "control_type": 50032,
            "name": "B",
            "value": None,
            "is_enabled": True,
            "is_offscreen": False,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "patterns": [],
            "children": [],
        }
        diffs = compare_snapshots(base, other)
        assert any("name" in d for d in diffs)

    def test_missing_pattern(self) -> None:
        """Missing patterns are detected."""
        base = {
            "control_type": 50000,
            "name": "Btn",
            "value": None,
            "is_enabled": True,
            "is_offscreen": False,
            "bounds": {"x": 0, "y": 0, "width": 50, "height": 50},
            "patterns": ["Invoke"],
            "children": [],
        }
        other = {
            "control_type": 50000,
            "name": "Btn",
            "value": None,
            "is_enabled": True,
            "is_offscreen": False,
            "bounds": {"x": 0, "y": 0, "width": 50, "height": 50},
            "patterns": [],
            "children": [],
        }
        diffs = compare_snapshots(base, other)
        assert any("Invoke" in d for d in diffs)

    def test_different_children_count(self) -> None:
        """Different children counts are detected."""
        base = {
            "control_type": 50032,
            "name": "W",
            "value": None,
            "is_enabled": True,
            "is_offscreen": False,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "patterns": [],
            "children": [],
        }
        other = {
            "control_type": 50032,
            "name": "W",
            "value": None,
            "is_enabled": True,
            "is_offscreen": False,
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "patterns": [],
            "children": [
                {
                    "control_type": 50033,
                    "name": "TB",
                    "value": None,
                    "is_enabled": True,
                    "is_offscreen": False,
                    "bounds": {"x": 0, "y": 0, "width": 100, "height": 32},
                    "patterns": [],
                    "children": [],
                },
            ],
        }
        diffs = compare_snapshots(base, other)
        assert any("children" in d for d in diffs)

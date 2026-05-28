"""Golden snapshot fixture tests for Linux backend regression (GW-035).

Validates that JSON golden fixtures representing normalized accessibility
tree snapshots from gedit and GNOME Calculator:
- Have a valid ``_metadata`` envelope.
- Conform to the NormalizedElement.to_dict() schema (role, states, actions, ...).
- Contain realistic normalized roles, states, bounds, and action values.
- Have consistent structural elements for each application.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.helpers import (
    LINUX_FIXTURES_DIR,
    compare_linux_snapshots,
    load_linux_golden_snapshot,
)

# ---------------------------------------------------------------------------
# Normalized snapshot schema constants
# ---------------------------------------------------------------------------

# Normalized roles produced by the Linux backend via _LINUX_ROLES mapping
_VALID_NORMALIZED_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "custom",
    "dialog",
    "document",
    "group",
    "header_item",
    "image",
    "label",
    "link",
    "list",
    "list_item",
    "menu_bar",
    "menu_item",
    "pane",
    "progress_bar",
    "radio_button",
    "scroll_bar",
    "separator",
    "slider",
    "spinner",
    "status_bar",
    "table",
    "table_cell",
    "tab",
    "tab_item",
    "text",
    "text_input",
    "toggle_button",
    "toolbar",
    "tooltip",
    "tree",
    "tree_item",
    "window",
}

# Normalized actions produced by the Linux backend via _LINUX_ACTIONS mapping
_VALID_NORMALIZED_ACTIONS = {
    "click",
    "invoke",
    "toggle",
    "select",
    "select_item",
    "deselect_item",
    "add_to_selection",
    "scroll",
    "expand",
    "collapse",
    "increment",
    "decrement",
    "type",
}

# Normalized state field names from ElementStates
_VALID_STATE_FIELDS = {
    "enabled",
    "focused",
    "selected",
    "checked",
    "expanded",
    "visible",
    "offscreen",
    "read_only",
    "required",
    "is_password",
    # Non-ElementStates fields that may appear from raw state mapping
    "focusable",
    "selectable",
    "multi_selectable",
    "modal",
    "horizontal",
    "vertical",
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
    """Validate a single node against the normalized snapshot schema.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []

    # role must be a non-empty string
    role = node.get("role")
    if role is None:
        errors.append(f"{path}: missing 'role'")
    elif not isinstance(role, str):
        errors.append(f"{path}: 'role' must be str, got {type(role).__name__}")
    elif role not in _VALID_NORMALIZED_ROLES:
        errors.append(f"{path}: unknown normalized role '{role}'")

    # ref must be a string
    ref = node.get("ref")
    if ref is None:
        errors.append(f"{path}: missing 'ref'")
    elif not isinstance(ref, str):
        errors.append(f"{path}: 'ref' must be str, got {type(ref).__name__}")

    # backend_id must be a string
    backend_id = node.get("backend_id")
    if backend_id is None:
        errors.append(f"{path}: missing 'backend_id'")
    elif not isinstance(backend_id, str):
        errors.append(f"{path}: 'backend_id' must be str, got {type(backend_id).__name__}")

    # name must be str or null
    name = node.get("name")
    if name is not None and not isinstance(name, str):
        errors.append(f"{path}: 'name' must be str or null, got {type(name).__name__}")

    # native_role must be str or null
    native_role = node.get("native_role")
    if native_role is not None and not isinstance(native_role, str):
        errors.append(
            f"{path}: 'native_role' must be str or null, got {type(native_role).__name__}"
        )

    # value must be str or null
    value = node.get("value")
    if value is not None and not isinstance(value, str):
        errors.append(f"{path}: 'value' must be str or null, got {type(value).__name__}")

    # text must be str or null
    text = node.get("text")
    if text is not None and not isinstance(text, str):
        errors.append(f"{path}: 'text' must be str or null, got {type(text).__name__}")

    # states must be a dict
    states = node.get("states")
    if states is None:
        errors.append(f"{path}: missing 'states'")
    elif not isinstance(states, dict):
        errors.append(f"{path}: 'states' must be dict, got {type(states).__name__}")
    else:
        for key in states:
            if key not in _VALID_STATE_FIELDS:
                errors.append(f"{path}: unknown state field '{key}'")

    # bounds must be a dict with x, y, width, height (all numeric) or null
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

    # actions must be a list of valid action strings
    actions = node.get("actions")
    if actions is None:
        errors.append(f"{path}: missing 'actions'")
    elif not isinstance(actions, list):
        errors.append(f"{path}: 'actions' must be list, got {type(actions).__name__}")
    else:
        for i, action in enumerate(actions):
            if not isinstance(action, str):
                errors.append(f"{path}: actions[{i}] must be str")
            elif action not in _VALID_NORMALIZED_ACTIONS:
                errors.append(f"{path}: actions[{i}] unknown action '{action}'")

    # children must be a list
    children = node.get("children")
    if children is None:
        errors.append(f"{path}: missing 'children'")
    elif not isinstance(children, list):
        errors.append(f"{path}: 'children' must be list, got {type(children).__name__}")

    return errors


def _validate_tree_schema(tree: dict[str, Any]) -> list[str]:
    """Validate the entire normalized snapshot tree recursively."""
    errors: list[str] = []
    nodes = _collect_all_nodes(tree)
    for node in nodes:
        path = node.get("name", str(node.get("role", "unknown")))
        errors.extend(_validate_node_schema(node, path))
    return errors


def _validate_metadata(metadata: dict[str, Any]) -> list[str]:
    """Validate the _metadata envelope."""
    errors: list[str] = []
    required_keys = {
        "captured_at",
        "os_version",
        "app_name",
        "pathlight_mcp_version",
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
# Shared fixtures
# ---------------------------------------------------------------------------

_LINUX_FIXTURE_NAMES = [
    "gedit_snapshot.json",
    "gnome_calculator_snapshot.json",
    "nautilus_snapshot.json",
]


@pytest.fixture(params=_LINUX_FIXTURE_NAMES)
def linux_golden_snapshot(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Load a Linux golden snapshot fixture by filename."""
    return load_linux_golden_snapshot(request.param)


@pytest.fixture(params=_LINUX_FIXTURE_NAMES)
def linux_snapshot_tree(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Load just the snapshot tree (without _metadata envelope)."""
    data = load_linux_golden_snapshot(request.param)
    return data["snapshot"]


# ---------------------------------------------------------------------------
# Metadata envelope tests
# ---------------------------------------------------------------------------


class TestLinuxMetadataEnvelope:
    """All Linux golden fixtures have a valid _metadata envelope."""

    def test_has_metadata_key(self, linux_golden_snapshot: dict[str, Any]) -> None:
        """Fixture must have a top-level '_metadata' key."""
        assert "_metadata" in linux_golden_snapshot

    def test_has_snapshot_key(self, linux_golden_snapshot: dict[str, Any]) -> None:
        """Fixture must have a top-level 'snapshot' key."""
        assert "snapshot" in linux_golden_snapshot

    def test_metadata_has_required_fields(self, linux_golden_snapshot: dict[str, Any]) -> None:
        """_metadata must contain all required fields."""
        errors = _validate_metadata(linux_golden_snapshot["_metadata"])
        assert errors == [], f"Metadata errors: {errors}"

    def test_metadata_max_depth_is_positive(self, linux_golden_snapshot: dict[str, Any]) -> None:
        """_metadata.max_depth must be a positive integer."""
        assert linux_golden_snapshot["_metadata"]["max_depth"] > 0

    def test_metadata_max_nodes_is_positive(self, linux_golden_snapshot: dict[str, Any]) -> None:
        """_metadata.max_nodes must be a positive integer."""
        assert linux_golden_snapshot["_metadata"]["max_nodes"] > 0


# ---------------------------------------------------------------------------
# Normalized snapshot schema conformance tests
# ---------------------------------------------------------------------------


class TestLinuxNormalizedSchema:
    """All Linux golden fixtures conform to the NormalizedElement.to_dict() schema."""

    def test_root_is_window(self, linux_snapshot_tree: dict[str, Any]) -> None:
        """Root element must have role 'window'."""
        assert linux_snapshot_tree["role"] == "window"

    def test_root_has_bounds(self, linux_snapshot_tree: dict[str, Any]) -> None:
        """Root element must have non-empty bounds."""
        bounds = linux_snapshot_tree.get("bounds")
        assert bounds is not None
        assert bounds["width"] > 0
        assert bounds["height"] > 0

    def test_root_has_ref(self, linux_snapshot_tree: dict[str, Any]) -> None:
        """Root element must have a ref string."""
        assert isinstance(linux_snapshot_tree.get("ref"), str)

    def test_root_has_backend_id(self, linux_snapshot_tree: dict[str, Any]) -> None:
        """Root element must have a backend_id string."""
        assert isinstance(linux_snapshot_tree.get("backend_id"), str)

    def test_no_schema_errors(self, linux_snapshot_tree: dict[str, Any]) -> None:
        """Full tree must have zero schema validation errors."""
        errors = _validate_tree_schema(linux_snapshot_tree)
        assert errors == [], f"Schema errors: {errors}"

    def test_all_roles_are_valid(self, linux_snapshot_tree: dict[str, Any]) -> None:
        """All role values in the tree must be recognized normalized roles."""
        nodes = _collect_all_nodes(linux_snapshot_tree)
        for node in nodes:
            assert node["role"] in _VALID_NORMALIZED_ROLES, (
                f"Unknown role '{node['role']}' for {node.get('name', '?')}"
            )

    def test_all_actions_are_valid(self, linux_snapshot_tree: dict[str, Any]) -> None:
        """All action values in the tree must be recognized normalized actions."""
        nodes = _collect_all_nodes(linux_snapshot_tree)
        for node in nodes:
            for action in node.get("actions", []):
                assert action in _VALID_NORMALIZED_ACTIONS, (
                    f"Unknown action '{action}' on {node.get('name', '?')}"
                )

    def test_all_states_are_valid(self, linux_snapshot_tree: dict[str, Any]) -> None:
        """All state field names in the tree must be recognized."""
        nodes = _collect_all_nodes(linux_snapshot_tree)
        for node in nodes:
            for state_key in node.get("states", {}):
                assert state_key in _VALID_STATE_FIELDS, (
                    f"Unknown state '{state_key}' on {node.get('name', '?')}"
                )


# ---------------------------------------------------------------------------
# gedit-specific structure tests
# ---------------------------------------------------------------------------


class TestGeditFixture:
    """gedit golden fixture has expected structural elements."""

    @pytest.fixture()
    def gedit(self) -> dict[str, Any]:
        return load_linux_golden_snapshot("gedit_snapshot.json")["snapshot"]

    def test_has_menu_bar(self, gedit: dict[str, Any]) -> None:
        """gedit must have a menu bar child."""
        children = gedit.get("children", [])
        roles = {c["role"] for c in children}
        assert "menu_bar" in roles

    def test_menu_bar_has_file_and_edit(self, gedit: dict[str, Any]) -> None:
        """gedit menu bar must contain File and Edit items."""
        menu_bar = next(
            (c for c in gedit.get("children", []) if c["role"] == "menu_bar"),
            None,
        )
        assert menu_bar is not None
        names = {c["name"] for c in menu_bar.get("children", [])}
        assert "File" in names
        assert "Edit" in names

    def test_menu_bar_has_common_items(self, gedit: dict[str, Any]) -> None:
        """gedit menu bar must contain File, Edit, View, Search, Tools, Help."""
        menu_bar = next(
            (c for c in gedit.get("children", []) if c["role"] == "menu_bar"),
            None,
        )
        assert menu_bar is not None
        names = {c["name"] for c in menu_bar.get("children", [])}
        for expected in ("File", "Edit", "View", "Search", "Tools", "Help"):
            assert expected in names, f"Missing menu item '{expected}'"

    def test_has_toolbar(self, gedit: dict[str, Any]) -> None:
        """gedit must have a toolbar child."""
        children = gedit.get("children", [])
        roles = {c["role"] for c in children}
        assert "toolbar" in roles

    def test_toolbar_has_common_buttons(self, gedit: dict[str, Any]) -> None:
        """gedit toolbar must contain New, Open, Save, Undo, Redo."""
        toolbar = next(
            (c for c in gedit.get("children", []) if c["role"] == "toolbar"),
            None,
        )
        assert toolbar is not None
        names = {c["name"] for c in toolbar.get("children", [])}
        for expected in ("New", "Open", "Save", "Undo", "Redo"):
            assert expected in names, f"Missing toolbar button '{expected}'"

    def test_toolbar_buttons_have_click_action(self, gedit: dict[str, Any]) -> None:
        """gedit toolbar buttons must support the click action."""
        toolbar = next(
            (c for c in gedit.get("children", []) if c["role"] == "toolbar"),
            None,
        )
        assert toolbar is not None
        for item in toolbar.get("children", []):
            assert "click" in item.get("actions", []), (
                f"Toolbar button '{item.get('name')}' missing click action"
            )

    def test_redo_button_is_disabled(self, gedit: dict[str, Any]) -> None:
        """gedit Redo button should be disabled (empty document)."""
        toolbar = next(
            (c for c in gedit.get("children", []) if c["role"] == "toolbar"),
            None,
        )
        assert toolbar is not None
        redo = next(
            (c for c in toolbar.get("children", []) if c.get("name") == "Redo"),
            None,
        )
        assert redo is not None
        assert redo["states"].get("enabled") is False

    def test_has_document_area(self, gedit: dict[str, Any]) -> None:
        """gedit must have a document child."""
        nodes = _collect_all_nodes(gedit)
        roles = {n["role"] for n in nodes}
        assert "document" in roles

    def test_document_has_text_input(self, gedit: dict[str, Any]) -> None:
        """gedit document must contain a text_input child (the editor)."""
        nodes = _collect_all_nodes(gedit)
        documents = [n for n in nodes if n["role"] == "document"]
        assert len(documents) >= 1
        doc = documents[0]
        child_roles = {c["role"] for c in doc.get("children", [])}
        assert "text_input" in child_roles

    def test_text_input_is_editable(self, gedit: dict[str, Any]) -> None:
        """gedit text editor must be editable."""
        nodes = _collect_all_nodes(gedit)
        text_inputs = [n for n in nodes if n["role"] == "text_input"]
        assert len(text_inputs) >= 1
        assert text_inputs[0]["states"].get("read_only") is False

    def test_text_input_has_type_action(self, gedit: dict[str, Any]) -> None:
        """gedit text editor must support the type action."""
        nodes = _collect_all_nodes(gedit)
        text_inputs = [n for n in nodes if n["role"] == "text_input"]
        assert len(text_inputs) >= 1
        assert "type" in text_inputs[0].get("actions", [])

    def test_has_status_bar(self, gedit: dict[str, Any]) -> None:
        """gedit must have a status bar child."""
        children = gedit.get("children", [])
        roles = {c["role"] for c in children}
        assert "status_bar" in roles

    def test_status_bar_has_cursor_position(self, gedit: dict[str, Any]) -> None:
        """gedit status bar must show cursor position (Ln/Col)."""
        status_bar = next(
            (c for c in gedit.get("children", []) if c["role"] == "status_bar"),
            None,
        )
        assert status_bar is not None
        texts = [c for c in status_bar.get("children", []) if c["role"] == "text"]
        assert len(texts) >= 1
        assert "Ln" in texts[0].get("text", "")

    def test_menu_items_are_menu_item_role(self, gedit: dict[str, Any]) -> None:
        """gedit menu bar children must have role 'menu_item'."""
        menu_bar = next(
            (c for c in gedit.get("children", []) if c["role"] == "menu_bar"),
            None,
        )
        assert menu_bar is not None
        for item in menu_bar.get("children", []):
            assert item["role"] == "menu_item", (
                f"Expected menu_item, got '{item['role']}' for '{item.get('name')}'"
            )

    def test_element_count(self, gedit: dict[str, Any]) -> None:
        """gedit fixture should have at least 10 elements."""
        count = len(_collect_all_nodes(gedit))
        assert count >= 10


# ---------------------------------------------------------------------------
# GNOME Calculator-specific structure tests
# ---------------------------------------------------------------------------


class TestGnomeCalculatorFixture:
    """GNOME Calculator golden fixture has expected structural elements."""

    @pytest.fixture()
    def calculator(self) -> dict[str, Any]:
        return load_linux_golden_snapshot("gnome_calculator_snapshot.json")["snapshot"]

    def test_root_is_window(self, calculator: dict[str, Any]) -> None:
        """Calculator root must have role 'window'."""
        assert calculator["role"] == "window"

    def test_has_display(self, calculator: dict[str, Any]) -> None:
        """Calculator must have a Display pane."""
        nodes = _collect_all_nodes(calculator)
        displays = [n for n in nodes if n["role"] == "pane" and n.get("name") == "Display"]
        assert len(displays) == 1

    def test_display_shows_zero(self, calculator: dict[str, Any]) -> None:
        """Calculator display must show '0'."""
        nodes = _collect_all_nodes(calculator)
        displays = [n for n in nodes if n["role"] == "pane" and n.get("name") == "Display"]
        assert len(displays) == 1
        texts = [c for c in displays[0].get("children", []) if c["role"] == "text"]
        assert len(texts) >= 1
        assert texts[0].get("text") == "0"

    def test_has_digit_buttons(self, calculator: dict[str, Any]) -> None:
        """Calculator must have buttons for digits 0-9."""
        nodes = _collect_all_nodes(calculator)
        button_names = {n["name"] for n in nodes if n["role"] == "button"}
        for digit in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
            assert digit in button_names, f"Missing digit button '{digit}'"

    def test_has_operator_buttons(self, calculator: dict[str, Any]) -> None:
        """Calculator must have operator buttons."""
        nodes = _collect_all_nodes(calculator)
        button_names = {n["name"] for n in nodes if n["role"] == "button"}
        for op_name in ("+", "−", "×", "÷"):  # noqa: RUF001
            assert op_name in button_names, f"Missing operator button '{op_name}'"

    def test_has_clear_button(self, calculator: dict[str, Any]) -> None:
        """Calculator must have a Clear button."""
        nodes = _collect_all_nodes(calculator)
        button_names = {n["name"] for n in nodes if n["role"] == "button"}
        assert "Clear" in button_names

    def test_has_equals_button(self, calculator: dict[str, Any]) -> None:
        """Calculator must have an equals button."""
        nodes = _collect_all_nodes(calculator)
        button_names = {n["name"] for n in nodes if n["role"] == "button"}
        assert "=" in button_names

    def test_all_buttons_have_click_action(self, calculator: dict[str, Any]) -> None:
        """All calculator buttons must support the click action."""
        nodes = _collect_all_nodes(calculator)
        buttons = [n for n in nodes if n["role"] == "button"]
        assert len(buttons) > 0
        for button in buttons:
            assert "click" in button.get("actions", []), (
                f"Button '{button.get('name')}' missing click action"
            )

    def test_all_buttons_are_push_button_native_role(self, calculator: dict[str, Any]) -> None:
        """All calculator buttons must have native_role 'push button'."""
        nodes = _collect_all_nodes(calculator)
        buttons = [n for n in nodes if n["role"] == "button"]
        assert len(buttons) > 0
        for button in buttons:
            assert button.get("native_role") == "push button", (
                f"Button '{button.get('name')}' has native_role '{button.get('native_role')}'"
            )

    def test_has_button_groups(self, calculator: dict[str, Any]) -> None:
        """Calculator must have group containers for button rows."""
        nodes = _collect_all_nodes(calculator)
        groups = [n for n in nodes if n["role"] == "group"]
        assert len(groups) >= 4, "Expected at least 4 button row groups"

    def test_element_count(self, calculator: dict[str, Any]) -> None:
        """GNOME Calculator fixture should have at least 20 elements."""
        count = len(_collect_all_nodes(calculator))
        assert count >= 20


# ---------------------------------------------------------------------------
# Fixture file integrity tests
# ---------------------------------------------------------------------------


class TestLinuxFixtureFiles:
    """Linux golden fixture files are well-formed and loadable."""

    def test_gedit_file_exists(self) -> None:
        """gedit_snapshot.json must exist in the fixtures/linux/ directory."""
        assert (LINUX_FIXTURES_DIR / "gedit_snapshot.json").is_file()

    def test_gnome_calculator_file_exists(self) -> None:
        """gnome_calculator_snapshot.json must exist in the fixtures/linux/ directory."""
        assert (LINUX_FIXTURES_DIR / "gnome_calculator_snapshot.json").is_file()

    def test_gedit_is_valid_json(self) -> None:
        """gedit_snapshot.json must be valid JSON."""
        data = load_linux_golden_snapshot("gedit_snapshot.json")
        assert isinstance(data, dict)

    def test_gnome_calculator_is_valid_json(self) -> None:
        """gnome_calculator_snapshot.json must be valid JSON."""
        data = load_linux_golden_snapshot("gnome_calculator_snapshot.json")
        assert isinstance(data, dict)

    def test_no_extra_fixture_files(self) -> None:
        """Only the three expected snapshot files should exist."""
        json_files = sorted(LINUX_FIXTURES_DIR.glob("*.json"))
        names = {f.name for f in json_files}
        assert names == {
            "gedit_snapshot.json",
            "gnome_calculator_snapshot.json",
            "nautilus_snapshot.json",
        }


# ---------------------------------------------------------------------------
# compare_linux_snapshots helper tests
# ---------------------------------------------------------------------------


class TestCompareLinuxSnapshots:
    """compare_linux_snapshots helper correctly detects differences."""

    def test_identical_trees(self) -> None:
        """Identical trees produce no differences."""
        tree = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "window",
            "native_role": "window",
            "name": "Test",
            "states": {"enabled": True, "visible": True},
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "actions": [],
            "children": [],
        }
        assert compare_linux_snapshots(tree, tree) == []

    def test_different_role(self) -> None:
        """Different roles are detected."""
        base = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "button",
            "native_role": "push button",
            "name": "Btn",
            "states": {"enabled": True},
            "bounds": {"x": 0, "y": 0, "width": 50, "height": 50},
            "actions": ["click"],
            "children": [],
        }
        other = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "pane",
            "native_role": "panel",
            "name": "Btn",
            "states": {"enabled": True},
            "bounds": {"x": 0, "y": 0, "width": 50, "height": 50},
            "actions": ["click"],
            "children": [],
        }
        diffs = compare_linux_snapshots(base, other)
        assert any("role" in d for d in diffs)

    def test_missing_action(self) -> None:
        """Missing actions are detected."""
        base = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "button",
            "native_role": "push button",
            "name": "Btn",
            "states": {"enabled": True},
            "bounds": {"x": 0, "y": 0, "width": 50, "height": 50},
            "actions": ["click"],
            "children": [],
        }
        other = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "button",
            "native_role": "push button",
            "name": "Btn",
            "states": {"enabled": True},
            "bounds": {"x": 0, "y": 0, "width": 50, "height": 50},
            "actions": [],
            "children": [],
        }
        diffs = compare_linux_snapshots(base, other)
        assert any("click" in d for d in diffs)

    def test_different_children_count(self) -> None:
        """Different children counts are detected."""
        base = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "window",
            "native_role": "window",
            "name": "W",
            "states": {"enabled": True, "visible": True},
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "actions": [],
            "children": [],
        }
        other = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "window",
            "native_role": "window",
            "name": "W",
            "states": {"enabled": True, "visible": True},
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "actions": [],
            "children": [
                {
                    "ref": "e1",
                    "backend_id": "0x1",
                    "role": "pane",
                    "native_role": "panel",
                    "name": "P",
                    "states": {"enabled": True},
                    "bounds": {"x": 0, "y": 0, "width": 100, "height": 32},
                    "actions": [],
                    "children": [],
                },
            ],
        }
        diffs = compare_linux_snapshots(base, other)
        assert any("children" in d for d in diffs)

    def test_different_state(self) -> None:
        """Different state values are detected."""
        base = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "button",
            "native_role": "push button",
            "name": "Btn",
            "states": {"enabled": True},
            "bounds": {"x": 0, "y": 0, "width": 50, "height": 50},
            "actions": ["click"],
            "children": [],
        }
        other = {
            "ref": "e0",
            "backend_id": "0x0",
            "role": "button",
            "native_role": "push button",
            "name": "Btn",
            "states": {"enabled": False},
            "bounds": {"x": 0, "y": 0, "width": 50, "height": 50},
            "actions": ["click"],
            "children": [],
        }
        diffs = compare_linux_snapshots(base, other)
        assert any("states" in d for d in diffs)

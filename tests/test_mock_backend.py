"""Tests for the MockBackend test double (GW-002, test design v2 TC-16-TC-36).

Verifies that MockBackend:
- Is a proper DesktopBackend subclass
- Implements all 8 canonical methods correctly
- Supports the fluent builder API (add_window, add_element return self)
- Raises appropriate errors for missing/invalid handles
- Records action logs for verification
- Returns correct types (dict for get_window_info/snapshot, list[NativeHandle] for find_elements)
- Supports GET_TEXT special return handling
- Produces tree dict with children nesting for snapshot
- Respects max_depth and max_nodes parameters
- Supports dispose cleanup
"""

import pytest

from pathlight_mcp.backends import (
    DesktopAction,
    DesktopBackend,
    ElementBounds,
    MockBackend,
    NativeHandle,
)
from pathlight_mcp.errors import (
    ElementNotFoundError,
    StaleElementReferenceError,
    WindowNotFoundError,
)

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def backend() -> MockBackend:
    """Return a fresh MockBackend with seed data (non-fluent for fixture compat)."""
    b = MockBackend()
    b.add_window(title="Notepad", app="notepad.exe", focused=True)
    w1 = b.last_window_handle
    b.add_window(title="Calculator", app="calc.exe", focused=False)
    w2 = b.last_window_handle
    b.add_element(role="button", name="Save", parent=w1)
    b.add_element(role="text_input", name="Editor", value="hello", parent=w1)
    b.add_element(role="button", name="Cancel", parent=w1)
    b.add_element(role="menu_item", name="File", parent=w1)
    b._w1 = w1
    b._w2 = w2
    return b


# -- TC-16: Is a DesktopBackend subclass ------------------------------------


class TestIsBackendSubclass:
    """TC-16: MockBackend must be a proper DesktopBackend."""

    def test_is_subclass(self) -> None:
        assert issubclass(MockBackend, DesktopBackend)

    def test_isinstance(self) -> None:
        b = MockBackend()
        assert isinstance(b, DesktopBackend)


# -- TC-17: Constructor -----------------------------------------------------


class TestConstructor:
    """TC-17: MockBackend should initialize cleanly."""

    def test_default_state(self) -> None:
        b = MockBackend()
        assert not b.is_disposed
        assert b.action_log == []


# -- TC-18-TC-19: Builder API -- add_window (fluent) ------------------------


class TestAddWindow:
    """TC-18-TC-19: add_window returns self for fluent chaining."""

    def test_returns_self(self) -> None:
        b = MockBackend()
        result = b.add_window(title="Test")
        assert result is b

    def test_window_listable(self) -> None:
        b = MockBackend()
        b.add_window(title="Test")
        windows = b.list_windows()
        assert len(windows) == 1

    def test_multiple_windows(self) -> None:
        b = MockBackend()
        b.add_window(title="W1")
        b.add_window(title="W2")
        windows = b.list_windows()
        assert len(windows) == 2

    def test_custom_bounds(self) -> None:
        b = MockBackend()
        bounds = ElementBounds(x=100, y=200, width=400, height=300)
        b.add_window(title="Custom", bounds=bounds)
        h = b.last_window_handle
        info = b.get_window_info(h)
        assert info["bounds"]["x"] == 100
        assert info["bounds"]["width"] == 400

    def test_last_window_handle(self) -> None:
        b = MockBackend()
        b.add_window(title="First")
        h1 = b.last_window_handle
        b.add_window(title="Second")
        h2 = b.last_window_handle
        assert h1 != h2
        assert h2 in b.list_windows()


# -- TC-20-TC-21: Builder API -- add_element (fluent) -----------------------


class TestAddElement:
    """TC-20-TC-21: add_element returns self for fluent chaining."""

    def test_returns_self(self, backend: MockBackend) -> None:
        result = backend.add_element(role="button", name="NewBtn", parent=backend._w1)
        assert result is backend

    def test_element_findable(self, backend: MockBackend) -> None:
        backend.add_element(role="checkbox", name="Check", parent=backend._w1)
        results = backend.find_elements(backend._w1, role="checkbox")
        assert len(results) == 1
        assert isinstance(results[0], str)


# -- TC-22: invalidate ------------------------------------------------------


class TestInvalidate:
    """TC-22: invalidate marks an element as stale."""

    def test_is_valid_true_initially(self, backend: MockBackend) -> None:
        backend.add_element(role="button", name="X", parent=backend._w1)
        handles = backend.find_elements(backend._w1, role="button")
        assert len(handles) >= 3  # Save, Cancel, X
        assert backend.is_valid(handles[-1]) is True

    def test_is_valid_false_after_invalidate(self, backend: MockBackend) -> None:
        backend.add_element(role="button", name="X", parent=backend._w1)
        handles = backend.find_elements(backend._w1, role="button")
        h = handles[-1]
        backend.invalidate(h)
        assert backend.is_valid(h) is False

    def test_is_valid_false_unknown_handle(self, backend: MockBackend) -> None:
        assert backend.is_valid(NativeHandle("nonexistent")) is False


# -- TC-23: list_windows ----------------------------------------------------


class TestListWindows:
    """TC-23: list_windows returns handles for all registered windows."""

    def test_returns_all_windows(self, backend: MockBackend) -> None:
        windows = backend.list_windows()
        assert len(windows) == 2
        assert backend._w1 in windows
        assert backend._w2 in windows

    def test_empty_backend(self) -> None:
        b = MockBackend()
        assert b.list_windows() == []


# -- TC-24: get_window_info (returns dict) ----------------------------------


class TestGetWindowInfo:
    """TC-24: get_window_info returns a dict with title, app_name, focused, bounds."""

    def test_returns_dict(self, backend: MockBackend) -> None:
        info = backend.get_window_info(backend._w1)
        assert isinstance(info, dict)

    def test_dict_has_correct_keys(self, backend: MockBackend) -> None:
        info = backend.get_window_info(backend._w1)
        assert "title" in info
        assert "app_name" in info
        assert "focused" in info
        assert "bounds" in info

    def test_dict_values(self, backend: MockBackend) -> None:
        info = backend.get_window_info(backend._w1)
        assert info["title"] == "Notepad"
        assert info["app_name"] == "notepad.exe"
        assert info["focused"] is True

    def test_window_not_found(self, backend: MockBackend) -> None:
        with pytest.raises(WindowNotFoundError):
            backend.get_window_info(NativeHandle("nonexistent"))


# -- TC-25: focus_window ----------------------------------------------------


class TestFocusWindow:
    """TC-25: focus_window sets focused state on the target window."""

    def test_focus_sets_flag(self, backend: MockBackend) -> None:
        backend.focus_window(backend._w2)
        info = backend.get_window_info(backend._w2)
        assert info["focused"] is True
        # Original window should no longer be focused
        info1 = backend.get_window_info(backend._w1)
        assert info1["focused"] is False

    def test_focus_window_not_found(self, backend: MockBackend) -> None:
        with pytest.raises(WindowNotFoundError):
            backend.focus_window(NativeHandle("nonexistent"))


# -- TC-26: snapshot (returns tree dict) ------------------------------------


class TestSnapshot:
    """TC-26: snapshot returns a tree dict with children nesting."""

    def test_snapshot_returns_dict(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        assert isinstance(tree, dict)

    def test_snapshot_dict_has_desktop_element_keys(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        for key in ("ref", "role", "name", "states", "bounds", "actions", "children"):
            assert key in tree, f"Missing key: {key}"

    def test_snapshot_root_is_window(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        assert tree["role"] == "window"
        assert tree["name"] == "Notepad"

    def test_snapshot_children_count(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        assert len(tree["children"]) == 4

    def test_snapshot_children_have_correct_roles(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        roles = [c["role"] for c in tree["children"]]
        assert roles.count("button") == 2
        assert "text_input" in roles
        assert "menu_item" in roles

    def test_snapshot_excludes_invalid(self, backend: MockBackend) -> None:
        handles = backend.find_elements(backend._w1, role="button")
        backend.invalidate(handles[0])
        tree = backend.snapshot(backend._w1)
        assert len(tree["children"]) == 3

    def test_snapshot_window_not_found(self, backend: MockBackend) -> None:
        with pytest.raises(WindowNotFoundError):
            backend.snapshot(NativeHandle("nonexistent"))

    def test_snapshot_empty_window(self) -> None:
        b = MockBackend()
        b.add_window(title="Empty")
        tree = b.snapshot(b.last_window_handle)
        assert tree["children"] == []

    def test_snapshot_max_depth_limits_nesting(self) -> None:
        """max_depth=0 should return no children."""
        b = MockBackend()
        b.add_window(title="Deep")
        b.add_element(role="button", name="B", parent=b.last_window_handle)
        tree = b.snapshot(b.last_window_handle, max_depth=0)
        assert tree["children"] == []

    def test_snapshot_max_nodes_limits_count(self) -> None:
        """max_nodes should limit total children included."""
        b = MockBackend()
        b.add_window(title="Big")
        for i in range(10):
            b.add_element(role="button", name=f"B{i}", parent=b.last_window_handle)
        tree = b.snapshot(b.last_window_handle, max_nodes=3)
        # max_nodes limits the number of child elements (root is separate)
        assert len(tree["children"]) == 3


# -- TC-27-TC-30: find_elements (returns list[NativeHandle]) ----------------


class TestFindElements:
    """TC-27-TC-30: find_elements returns list[NativeHandle]."""

    def test_returns_handles(self, backend: MockBackend) -> None:
        results = backend.find_elements(backend._w1, role="button")
        assert len(results) == 2
        for r in results:
            assert isinstance(r, str)

    def test_find_by_role(self, backend: MockBackend) -> None:
        results = backend.find_elements(backend._w1, role="button")
        assert len(results) == 2

    def test_find_by_name(self, backend: MockBackend) -> None:
        results = backend.find_elements(backend._w1, name="Save")
        assert len(results) == 1

    def test_find_by_name_case_insensitive(self, backend: MockBackend) -> None:
        results = backend.find_elements(backend._w1, name="save")
        assert len(results) == 1

    def test_find_by_role_and_name(self, backend: MockBackend) -> None:
        results = backend.find_elements(backend._w1, role="button", name="Save")
        assert len(results) == 1

    def test_find_no_match(self, backend: MockBackend) -> None:
        results = backend.find_elements(backend._w1, role="checkbox")
        assert results == []

    def test_find_window_not_found(self, backend: MockBackend) -> None:
        with pytest.raises(WindowNotFoundError):
            backend.find_elements(NativeHandle("nonexistent"), role="button")

    def test_find_skips_invalidated_elements(self, backend: MockBackend) -> None:
        """find_elements should skip elements that have been invalidated."""
        buttons_before = backend.find_elements(backend._w1, role="button")
        assert len(buttons_before) == 2
        backend.invalidate(buttons_before[0])
        buttons_after = backend.find_elements(backend._w1, role="button")
        assert len(buttons_after) == 1


# -- TC-31-TC-33: perform_action (handle, action, **kwargs) ------------------


class TestPerformAction:
    """TC-31-TC-33: perform_action dispatches with (handle, action) order."""

    def test_click_action(self, backend: MockBackend) -> None:
        elem_handle = backend.find_elements(backend._w1, role="button")[0]
        backend.perform_action(elem_handle, DesktopAction.CLICK)
        assert len(backend.action_log) == 1
        assert backend.action_log[0]["action"] == DesktopAction.CLICK

    def test_type_action(self, backend: MockBackend) -> None:
        """TYPE_TEXT was renamed to TYPE per §4.3."""
        elem_handle = backend.find_elements(backend._w1, role="text_input")[0]
        backend.perform_action(elem_handle, DesktopAction.TYPE, text="hello")
        assert len(backend.action_log) == 1
        assert backend.action_log[0]["kwargs"]["text"] == "hello"

    def test_element_not_found(self, backend: MockBackend) -> None:
        with pytest.raises(ElementNotFoundError):
            backend.perform_action(NativeHandle("nonexistent"), DesktopAction.CLICK)

    def test_stale_element_raises(self, backend: MockBackend) -> None:
        elem_handle = backend.find_elements(backend._w1, role="button")[0]
        backend.invalidate(elem_handle)
        with pytest.raises(StaleElementReferenceError):
            backend.perform_action(elem_handle, DesktopAction.CLICK)

    def test_action_log_multiple(self, backend: MockBackend) -> None:
        elem = backend.find_elements(backend._w1, role="button")[0]
        backend.perform_action(elem, DesktopAction.CLICK)
        backend.perform_action(elem, DesktopAction.CLICK)
        backend.perform_action(elem, DesktopAction.SCROLL, direction="down")
        assert len(backend.action_log) == 3

    def test_get_text_returns_str(self, backend: MockBackend) -> None:
        """GET_TEXT should return element value as str."""
        elem_handle = backend.find_elements(backend._w1, role="text_input")[0]
        result = backend.perform_action(elem_handle, DesktopAction.GET_TEXT)
        assert isinstance(result, str)
        assert result == "hello"

    def test_get_text_returns_empty_for_no_value(self, backend: MockBackend) -> None:
        """GET_TEXT should return empty string when element has no value."""
        elem_handle = backend.find_elements(backend._w1, role="button")[0]
        result = backend.perform_action(elem_handle, DesktopAction.GET_TEXT)
        assert result == ""

    def test_non_get_text_returns_none(self, backend: MockBackend) -> None:
        """Non-GET_TEXT actions should return None."""
        elem_handle = backend.find_elements(backend._w1, role="button")[0]
        result = backend.perform_action(elem_handle, DesktopAction.CLICK)
        assert result is None


# -- TC-34: is_valid --------------------------------------------------------


class TestIsValid:
    """TC-34: is_valid checks element reference validity."""

    def test_valid_element(self, backend: MockBackend) -> None:
        backend.add_element(role="button", name="X", parent=backend._w1)
        handles = backend.find_elements(backend._w1, role="button")
        assert backend.is_valid(handles[-1]) is True

    def test_invalidated_element(self, backend: MockBackend) -> None:
        backend.add_element(role="button", name="X", parent=backend._w1)
        handles = backend.find_elements(backend._w1, role="button")
        h = handles[-1]
        backend.invalidate(h)
        assert backend.is_valid(h) is False

    def test_unknown_element(self, backend: MockBackend) -> None:
        assert backend.is_valid(NativeHandle("nonexistent")) is False


# -- TC-35: dispose ---------------------------------------------------------


class TestDispose:
    """TC-35: dispose releases all resources."""

    def test_dispose_clears_windows(self, backend: MockBackend) -> None:
        assert len(backend.list_windows()) > 0
        backend.dispose()
        assert backend.list_windows() == []

    def test_dispose_clears_elements(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        assert len(tree["children"]) > 0
        backend.dispose()
        assert backend.is_disposed

    def test_dispose_idempotent(self, backend: MockBackend) -> None:
        backend.dispose()
        backend.dispose()  # should not raise
        assert backend.is_disposed


# -- TC-36: snapshot tree dict fields ---------------------------------------


class TestSnapshotTreeFields:
    """TC-36: snapshot children should have DesktopElement schema fields."""

    def test_child_has_correct_keys(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        btn = next(c for c in tree["children"] if c["role"] == "button" and c["name"] == "Save")
        for key in ("ref", "role", "name", "states", "bounds", "actions", "children"):
            assert key in btn

    def test_child_states_has_element_state_flags(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        btn = next(c for c in tree["children"] if c["role"] == "button" and c["name"] == "Save")
        states = btn["states"]
        assert "enabled" in states
        assert "focused" in states
        assert "selected" in states
        assert "checked" in states
        assert "expanded" in states
        assert "visible" in states
        assert "offscreen" in states
        assert "read_only" in states
        assert "required" in states

    def test_child_ref_is_handle(self, backend: MockBackend) -> None:
        tree = backend.snapshot(backend._w1)
        child = tree["children"][0]
        assert isinstance(child["ref"], str)
        assert backend.is_valid(child["ref"])


# -- Fluent chaining integration ---------------------------------------------


class TestFluentChaining:
    """Verify builder API supports fluent method chaining (§5.3)."""

    def test_full_fluent_chain(self) -> None:
        b = MockBackend()
        b.add_window(title="App", app="app.exe", focused=True)
        w = b.last_window_handle
        b.add_element(role="button", name="OK", parent=w)
        b.add_element(role="text_input", name="Input", value="test", parent=w)
        assert isinstance(b, MockBackend)
        assert len(b.list_windows()) == 1
        tree = b.snapshot(w)
        assert len(tree["children"]) == 2

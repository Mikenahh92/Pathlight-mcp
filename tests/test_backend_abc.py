"""Tests for the DesktopBackend ABC contract (GW-002, test design v2 TC-01-TC-10).

Verifies that:
- DesktopBackend cannot be instantiated directly
- DesktopBackend is an ABC
- All 16 canonical abstract methods are present
- Method signatures match the architecture v2 contract
- A minimal concrete subclass compiles and runs
- The ABC is properly discoverable from the package
"""

import inspect

import pytest

from guidewire.backends import DesktopBackend

# -- TC-01: Cannot instantiate directly -------------------------------------


class TestABCInstantiation:
    """TC-01: DesktopBackend must not be instantiable without implementing all methods."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            DesktopBackend()  # type: ignore[abstract]


# -- TC-02: Is an ABC -------------------------------------------------------


class TestABCBase:
    """TC-02: DesktopBackend should be an abstract base class."""

    def test_is_abstract_base_class(self) -> None:
        from abc import ABC

        assert issubclass(DesktopBackend, ABC)


# -- TC-03: 16 abstract methods present --------------------------------------


class TestAbstractMethodsPresent:
    """TC-03: Exactly 16 abstract methods must be declared."""

    EXPECTED_METHODS = frozenset(
        {
            "list_windows",
            "get_window_info",
            "focus_window",
            "snapshot",
            "find_elements",
            "perform_action",
            "get_element_info",
            "is_valid",
            "clipboard_read",
            "clipboard_write",
            "scroll_to_item",
            "minimize_window",
            "maximize_window",
            "restore_window",
            "move_window",
            "resize_window",
            "dispose",
        }
    )

    def test_all_abstract_methods_present(self) -> None:
        actual = {
            name
            for name, method in inspect.getmembers(DesktopBackend, predicate=inspect.isfunction)
            if getattr(method, "__isabstractmethod__", False)
        }
        assert actual == self.EXPECTED_METHODS

    def test_abstract_method_count(self) -> None:
        abstracts = [
            name
            for name, method in inspect.getmembers(DesktopBackend, predicate=inspect.isfunction)
            if getattr(method, "__isabstractmethod__", False)
        ]
        assert len(abstracts) == 17


# -- TC-04: list_windows signature ------------------------------------------


class TestListWindowsSignature:
    """TC-04: list_windows takes only self, returns list[NativeHandle]."""

    def test_signature(self) -> None:
        sig = inspect.signature(DesktopBackend.list_windows)
        params = list(sig.parameters.keys())
        assert params == ["self"]


# -- TC-05: get_window_info signature ---------------------------------------


class TestGetWindowInfoSignature:
    """TC-05: get_window_info takes self + window, returns dict."""

    def test_signature(self) -> None:
        sig = inspect.signature(DesktopBackend.get_window_info)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "window" in params


# -- TC-06: focus_window signature ------------------------------------------


class TestFocusWindowSignature:
    """TC-06: focus_window takes self + window, returns None."""

    def test_signature(self) -> None:
        sig = inspect.signature(DesktopBackend.focus_window)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "window" in params


# -- TC-07: snapshot signature ----------------------------------------------


class TestSnapshotSignature:
    """TC-07: snapshot takes self + window + max_depth (default 4) + max_nodes (default 500)."""

    def test_signature(self) -> None:
        sig = inspect.signature(DesktopBackend.snapshot)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "window" in params
        assert "max_depth" in params
        assert "max_nodes" in params
        assert sig.parameters["max_depth"].default == 4
        assert sig.parameters["max_nodes"].default == 500


# -- TC-08: find_elements signature -----------------------------------------


class TestFindElementsSignature:
    """TC-08: find_elements takes self + window + role (optional) + name (optional)."""

    def test_signature(self) -> None:
        sig = inspect.signature(DesktopBackend.find_elements)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "window" in params
        assert "role" in params
        assert "name" in params


# -- TC-09: perform_action signature (handle, action, **kwargs) -------------


class TestPerformActionSignature:
    """TC-09: perform_action takes self + handle + action + **kwargs (§4.1 order)."""

    def test_signature(self) -> None:
        sig = inspect.signature(DesktopBackend.perform_action)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "handle" in params
        assert "action" in params
        assert "kwargs" in params

    def test_handle_before_action(self) -> None:
        """handle must come before action in parameter order."""
        sig = inspect.signature(DesktopBackend.perform_action)
        params = list(sig.parameters.keys())
        assert params.index("handle") < params.index("action")


# -- TC-10: Minimal concrete subclass ---------------------------------------


class TestMinimalSubclass:
    """TC-10: A subclass implementing all 16 methods should be instantiable."""

    def test_subclass_instantiation(self) -> None:
        class MinimalBackend(DesktopBackend):
            def list_windows(self):
                return []

            def get_window_info(self, window):
                return {"title": "", "app_name": "", "focused": False, "bounds": None}

            def focus_window(self, window) -> None:
                pass

            def snapshot(self, window, max_depth=4, max_nodes=500):
                return {
                    "ref": window,
                    "role": "window",
                    "name": "",
                    "states": {},
                    "bounds": None,
                    "actions": [],
                    "children": [],
                }

            def find_elements(self, window, role=None, name=None):
                return []

            def perform_action(self, handle, action, **kwargs):
                return None

            def get_element_info(self, handle):
                return {"role": "element", "name": None, "states": {}}

            def is_valid(self, element) -> bool:
                return False

            def scroll_to_item(self, container, *, item_name=None, item_index=None, max_retries=10):
                return None

            def clipboard_read(self) -> str:
                return ""

            def clipboard_write(self, text: str) -> None:
                pass

            def minimize_window(self, window) -> None:
                pass

            def maximize_window(self, window) -> None:
                pass

            def restore_window(self, window) -> None:
                pass

            def move_window(self, window, x, y) -> None:
                pass

            def resize_window(self, window, width, height) -> None:
                pass

            def dispose(self) -> None:
                pass

        backend = MinimalBackend()
        assert isinstance(backend, DesktopBackend)


# -- Package exports --------------------------------------------------------


class TestPackageExports:
    """Verify public API of the backends package."""

    def test_desktop_backend_exported(self) -> None:
        from guidewire.backends import DesktopBackend

        assert DesktopBackend is not None

    def test_mock_backend_exported(self) -> None:
        from guidewire.backends import MockBackend

        assert MockBackend is not None

    def test_native_handle_exported(self) -> None:
        from guidewire.backends import NativeHandle

        assert NativeHandle is not None

    def test_element_state_exported(self) -> None:
        from guidewire.backends import ElementState

        assert ElementState is not None

    def test_element_bounds_exported(self) -> None:
        from guidewire.backends import ElementBounds

        assert ElementBounds is not None

    def test_desktop_action_exported(self) -> None:
        from guidewire.backends import DesktopAction

        assert DesktopAction is not None

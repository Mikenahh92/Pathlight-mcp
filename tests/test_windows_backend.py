"""Tests for the WindowsBackend skeleton (GW-019) and list_windows (GW-020).

Validates:
- Module can be imported on any platform (guarded comtypes import).
- WindowsBackend is a concrete subclass of DesktopBackend.
- Platform guard raises BackendUnavailableError on non-Windows systems.
- comtypes-missing guard raises BackendUnavailableError.
- list_windows() enumerates visible top-level windows via UIA COM.
- Off-screen windows are filtered out.
- COM errors are translated to BackendUnavailableError.
- is_valid() detects stale COM element handles via property access (GW-024).
- dispose() performs full COM cleanup and is idempotent.
- Constructor signature matches DesktopBackend contract.

Focus-window tests live in ``test_windows_focus_window.py`` (architecture §3.2).
"""

import inspect
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from pathlight_mcp.backends.base import DesktopBackend
from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.backends.windows import WindowsBackend
from pathlight_mcp.errors import (
    BackendUnavailableError,
    StaleElementReferenceError,
    WindowNotFoundError,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")),
    reason="requires Windows desktop (not available in headless CI)",
)

# ---------------------------------------------------------------------------
# Structural tests (run on any platform)
# ---------------------------------------------------------------------------


class TestWindowsBackendStructure:
    """Verify the WindowsBackend class shape."""

    def test_is_subclass_of_desktop_backend(self) -> None:
        """WindowsBackend must inherit from DesktopBackend."""
        assert issubclass(WindowsBackend, DesktopBackend)

    def test_concrete_class(self) -> None:
        """WindowsBackend must be instantiable (no unimplemented abstracts)."""
        # We can't instantiate it on non-Windows, so just verify it's not abstract
        assert not getattr(WindowsBackend, "__abstractmethods__", None)

    def test_exports_in_backends_package(self) -> None:
        """WindowsBackend must be re-exported from the backends package on win32."""
        with patch("sys.platform", "win32"):
            # Reload the __init__ to pick up the conditional import
            import importlib

            import pathlight_mcp.backends

            importlib.reload(pathlight_mcp.backends)
            if pathlight_mcp.backends.WindowsBackend is not None:
                assert pathlight_mcp.backends.WindowsBackend is WindowsBackend

    def test_all_nine_abstract_methods_exist(self) -> None:
        """WindowsBackend must define all 9 abstract DesktopBackend methods."""
        expected = [
            "list_windows",
            "get_window_info",
            "focus_window",
            "snapshot",
            "find_elements",
            "perform_action",
            "get_element_info",
            "is_valid",
            "dispose",
        ]
        for method_name in expected:
            assert hasattr(WindowsBackend, method_name), (
                f"WindowsBackend missing method: {method_name}"
            )
            assert callable(getattr(WindowsBackend, method_name))

    # TC-WIN-006: Constructor signature must match DesktopBackend contract

    def test_constructor_signature_no_extra_params(self) -> None:
        """WindowsBackend.__init__ must accept only self (no extra params)."""
        sig = inspect.signature(WindowsBackend.__init__)
        params = list(sig.parameters.keys())
        assert params == ["self"], f"Expected only 'self', got {params}"


# ---------------------------------------------------------------------------
# Platform guard tests
# ---------------------------------------------------------------------------


class TestPlatformGuard:
    """Verify platform detection and comtypes availability guards."""

    @patch("sys.platform", "linux")
    def test_raises_on_linux(self) -> None:
        """Must raise BackendUnavailableError on Linux."""
        with pytest.raises(BackendUnavailableError, match="Windows platform"):
            WindowsBackend()

    @patch("sys.platform", "darwin")
    def test_raises_on_macos(self) -> None:
        """Must raise BackendUnavailableError on macOS."""
        with pytest.raises(BackendUnavailableError, match="Windows platform"):
            WindowsBackend()

    # TC-WIN-014: Error message must include the current platform

    @patch("sys.platform", "linux")
    def test_error_message_includes_platform(self) -> None:
        """Platform guard error message must include the current platform name."""
        with pytest.raises(BackendUnavailableError) as exc_info:
            WindowsBackend()
        assert "linux" in str(exc_info.value).lower()

    @patch("sys.platform", "win32")
    def test_raises_when_comtypes_missing(self) -> None:
        """Must raise BackendUnavailableError when comtypes is not installed."""
        with (
            patch.dict("sys.modules", {"comtypes": None}),
            pytest.raises(BackendUnavailableError, match="comtypes"),
        ):
            WindowsBackend()

    @patch("sys.platform", "win32")
    def test_error_code_is_backend_unavailable(self) -> None:
        """Guard errors must use the backend_unavailable error code."""
        with (
            patch.dict("sys.modules", {"comtypes": None}),
            pytest.raises(BackendUnavailableError) as exc_info,
        ):
            WindowsBackend()
        assert exc_info.value.error_code == "backend_unavailable"

    @patch("sys.platform", "win32")
    def test_error_message_mentions_windows_extra(self) -> None:
        """comtypes-missing error must mention the [windows] extra."""
        with (
            patch.dict("sys.modules", {"comtypes": None}),
            pytest.raises(BackendUnavailableError) as exc_info,
        ):
            WindowsBackend()
        assert "pathlight_mcp[windows]" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Stub method tests
# ---------------------------------------------------------------------------


class TestStubMethods:
    """Verify stub methods raise NotImplementedError (focus_window tested separately)."""

    @pytest.fixture()
    def backend(self) -> WindowsBackend:
        """Create a WindowsBackend bypassing the platform guard."""
        with (
            patch("sys.platform", "win32"),
            patch.dict("sys.modules", {"comtypes": type("mod", (), {})}),
        ):
            b = WindowsBackend.__new__(WindowsBackend)
            b._com_initialized = True
            uia_mock = MagicMock()
            uia_mock.ControlViewWalker.GetFirstChildElement.return_value = None
            uia_mock.ControlViewWalker.GetNextSiblingElement.return_value = None
            b._uia = uia_mock
            b._disposed = False
            b._element_cache = {}
            return b

    def test_list_windows_returns_handles(self, backend: WindowsBackend) -> None:
        """list_windows() should return a list of NativeHandle objects."""
        mock_element1 = MagicMock()
        mock_element1.GetCurrentPropertyValue.return_value = False
        mock_element2 = MagicMock()
        mock_element2.GetCurrentPropertyValue.return_value = False
        mock_array = MagicMock()
        mock_array.Length = 2
        mock_array.GetElement.side_effect = [mock_element1, mock_element2]
        backend._uia.GetRootElement.return_value = MagicMock()
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        result = backend.list_windows()
        assert isinstance(result, list)
        assert len(result) == 2
        for item in result:
            # NativeHandle is a NewType over Any, wrapping the COM element
            assert item is not None

    def test_list_windows_filters_offscreen(self, backend: WindowsBackend) -> None:
        """list_windows() should exclude off-screen windows."""
        visible = MagicMock()
        visible.GetCurrentPropertyValue.return_value = False  # not offscreen
        hidden = MagicMock()
        hidden.GetCurrentPropertyValue.return_value = True  # offscreen
        mock_array = MagicMock()
        mock_array.Length = 2
        mock_array.GetElement.side_effect = [visible, hidden]
        backend._uia.GetRootElement.return_value = MagicMock()
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        result = backend.list_windows()
        assert len(result) == 1

    def test_list_windows_empty(self, backend: WindowsBackend) -> None:
        """list_windows() should return empty list when no windows found."""
        mock_array = MagicMock()
        mock_array.Length = 0
        backend._uia.GetRootElement.return_value = MagicMock()
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        result = backend.list_windows()
        assert result == []

    def test_list_windows_com_error_raises_backend_unavailable(
        self, backend: WindowsBackend
    ) -> None:
        """COM errors in list_windows should be translated to BackendUnavailableError."""
        backend._uia.GetRootElement.side_effect = RuntimeError("COM error")

        with pytest.raises(BackendUnavailableError, match="enumerate windows"):
            backend.list_windows()

    def test_get_window_info_returns_metadata(self, backend: WindowsBackend) -> None:
        """get_window_info returns title, app_name, focused, bounds from COM element."""
        from pathlight_mcp.backends.types import NativeHandle

        mock_element = MagicMock()
        # Set up property values: Name, ClassName, HasKeyboardFocus, BoundingRectangle, ProcessId
        prop_values = {
            30005: "Calculator",  # Name → title
            30012: "CalcFrame",  # ClassName → app_name
            30008: True,  # HasKeyboardFocus → focused
            30001: MagicMock(left=10, top=20, right=810, bottom=620),  # BoundingRectangle
            30076: 1234,  # ProcessId → liveness probe
        }
        mock_element.GetCurrentPropertyValue.side_effect = lambda pid: prop_values[pid]

        result = backend.get_window_info(NativeHandle(mock_element))

        assert result["title"] == "Calculator"
        assert result["app_name"] == "CalcFrame"
        assert result["focused"] is True
        assert result["bounds"] == {"x": 10, "y": 20, "width": 800, "height": 600}

    def test_get_window_info_bounds_as_tuple(self, backend: WindowsBackend) -> None:
        """get_window_info handles BoundingRectangle returned as a tuple."""
        from pathlight_mcp.backends.types import NativeHandle

        mock_element = MagicMock()
        prop_values = {
            30005: "Notepad",
            30012: "Notepad",
            30008: False,
            30001: (100, 200, 500, 600),
            30076: 5678,
        }
        mock_element.GetCurrentPropertyValue.side_effect = lambda pid: prop_values[pid]

        result = backend.get_window_info(NativeHandle(mock_element))

        assert result["bounds"] == {"x": 100, "y": 200, "width": 400, "height": 400}

    def test_get_window_info_null_bounds(self, backend: WindowsBackend) -> None:
        """get_window_info returns None for bounds when rect is None."""
        from pathlight_mcp.backends.types import NativeHandle

        mock_element = MagicMock()
        prop_values = {
            30005: "Untitled",
            30012: "Chrome_WidgetWin_1",
            30008: False,
            30001: None,
            30076: 9999,
        }
        mock_element.GetCurrentPropertyValue.side_effect = lambda pid: prop_values[pid]

        result = backend.get_window_info(NativeHandle(mock_element))

        assert result["bounds"] is None

    def test_get_window_info_disposed_raises(self, backend: WindowsBackend) -> None:
        """get_window_info on a disposed backend raises WindowNotFoundError."""
        backend._disposed = True
        with pytest.raises(WindowNotFoundError, match="disposed"):
            backend.get_window_info(NativeHandle("fake"))

    def test_get_window_info_com_error_raises_window_not_found(
        self, backend: WindowsBackend
    ) -> None:
        """COM errors in get_window_info are translated to WindowNotFoundError."""

        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = RuntimeError("COM error")

        with pytest.raises(WindowNotFoundError, match="read window info"):
            backend.get_window_info(NativeHandle(mock_element))

    def test_focus_window_invalid_handle_raises_window_not_found(
        self, backend: WindowsBackend
    ) -> None:
        """focus_window is implemented (GW-021); invalid handle raises WindowNotFoundError."""

        with pytest.raises(WindowNotFoundError):
            backend.focus_window(NativeHandle(0))

    def test_snapshot_no_longer_raises_not_implemented(self, backend: WindowsBackend) -> None:
        from pathlight_mcp.backends.types import NativeHandle

        # snapshot() is now implemented (GW-022); verify it no longer raises
        # NotImplementedError.  With the mock _uia, it will attempt to walk
        # a tree and return a dict (even if empty/mocked).
        try:
            result = backend.snapshot(NativeHandle("fake"))
            assert isinstance(result, dict)
        except NotImplementedError:
            pytest.fail("snapshot() should no longer raise NotImplementedError")

    def test_find_elements_no_longer_raises_not_implemented(self, backend: WindowsBackend) -> None:
        from pathlight_mcp.backends.types import NativeHandle

        # find_elements() is now implemented (GW-022); verify it no longer raises
        # NotImplementedError.  With the mock _uia, it returns a list.
        try:
            result = backend.find_elements(NativeHandle("fake"), role="button")
            assert isinstance(result, list)
        except NotImplementedError:
            pytest.fail("find_elements() should no longer raise NotImplementedError")

    def test_perform_action_disposed_raises(self, backend: WindowsBackend) -> None:
        """perform_action on a disposed backend raises StaleElementReferenceError."""
        from pathlight_mcp.backends.types import DesktopAction, NativeHandle

        backend.dispose()
        with pytest.raises(StaleElementReferenceError, match="disposed"):
            backend.perform_action(NativeHandle("fake"), DesktopAction.CLICK)

    def test_get_element_info_disposed_raises(self, backend: WindowsBackend) -> None:
        """get_element_info on a disposed backend raises StaleElementReferenceError."""
        from pathlight_mcp.backends.types import NativeHandle

        backend.dispose()
        with pytest.raises(StaleElementReferenceError, match="disposed"):
            backend.get_element_info(NativeHandle("fake"))

    def test_is_valid_no_longer_raises_not_implemented(self, backend: WindowsBackend) -> None:
        """is_valid is implemented (GW-024) — must not raise NotImplementedError."""
        from pathlight_mcp.backends.types import NativeHandle

        # COM element: property access succeeds → True
        mock_element = MagicMock()
        backend.is_valid(NativeHandle(mock_element))  # type: ignore[arg-type]
        # No assertion needed — just verifying it doesn't raise NotImplementedError

    def test_is_valid_returns_false_for_none(self, backend: WindowsBackend) -> None:
        """is_valid must return False for None handle."""
        assert backend.is_valid(None) is False

    def test_dispose_sets_disposed_flag(self, backend: WindowsBackend) -> None:
        """dispose() must set _disposed to True without raising."""
        assert not backend._disposed
        backend.dispose()
        assert backend._disposed

    # TC-WIN-028: dispose() must set _com_initialized to False

    def test_dispose_clears_com_initialized(self, backend: WindowsBackend) -> None:
        """TC-WIN-028: dispose() must set _com_initialized to False."""
        assert backend._com_initialized is True
        backend.dispose()
        assert backend._com_initialized is False

    # TC-WIN-029: dispose() must set _uia to None

    def test_dispose_clears_uia_reference(self, backend: WindowsBackend) -> None:
        """TC-WIN-029: dispose() must set _uia to None."""
        assert backend._uia is not None
        backend.dispose()
        assert backend._uia is None

    def test_dispose_is_idempotent(self, backend: WindowsBackend) -> None:
        """dispose() must be safe to call multiple times."""
        backend.dispose()
        backend.dispose()  # second call should not raise
        assert backend._disposed is True
        assert backend._com_initialized is False
        assert backend._uia is None


# ---------------------------------------------------------------------------
# list_windows P0 targeted tests (QA F2 fix)
# ---------------------------------------------------------------------------


class TestListWindowsP0:
    """P0 test-design cases for list_windows (TC-LW-004, 005, 006, 021)."""

    @pytest.fixture()
    def backend(self) -> WindowsBackend:
        """Create a WindowsBackend bypassing the platform guard."""
        with (
            patch("sys.platform", "win32"),
            patch.dict("sys.modules", {"comtypes": type("mod", (), {})}),
        ):
            b = WindowsBackend.__new__(WindowsBackend)
            b._com_initialized = True
            b._uia = MagicMock()
            b._disposed = False
            b._element_cache = {}
            return b

    def test_tc_lw_004_get_root_element_called_once(self, backend: WindowsBackend) -> None:
        """TC-LW-004: GetRootElement must be called exactly once."""
        mock_array = MagicMock()
        mock_array.Length = 0
        backend._uia.GetRootElement.return_value = MagicMock()
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        backend.list_windows()

        backend._uia.GetRootElement.assert_called_once()

    def test_tc_lw_005_control_type_constant_is_50032(self, backend: WindowsBackend) -> None:
        """TC-LW-005: CreatePropertyCondition must use control type 50032 (0xC370)."""
        from pathlight_mcp.backends.windows import _UIA_WINDOW_CONTROL_TYPE_ID

        assert _UIA_WINDOW_CONTROL_TYPE_ID == 50032, (
            "UIA Window control type must be 50032 (0xC370), not 50036 (TitleBar)"
        )

        mock_array = MagicMock()
        mock_array.Length = 0
        backend._uia.GetRootElement.return_value = MagicMock()
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        backend.list_windows()

        backend._uia.CreatePropertyCondition.assert_called_once()
        args, _kwargs = backend._uia.CreatePropertyCondition.call_args
        # Second positional arg is the control type value
        assert args[1] == 50032, f"Expected 50032, got {args[1]}"

    def test_tc_lw_006_tree_scope_children_used(self, backend: WindowsBackend) -> None:
        """TC-LW-006: FindAll must be called with TreeScope_Children (= 2)."""
        from pathlight_mcp.backends.windows import _UIA_TREE_SCOPE_CHILDREN

        assert _UIA_TREE_SCOPE_CHILDREN == 2

        mock_array = MagicMock()
        mock_array.Length = 0
        backend._uia.GetRootElement.return_value = MagicMock()
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        backend.list_windows()

        args, _kwargs = backend._uia.FindAll.call_args
        assert args[0] == 2, f"Expected TreeScope_Children=2, got {args[0]}"

    def test_tc_lw_021_disposed_backend_raises_backend_unavailable(
        self, backend: WindowsBackend
    ) -> None:
        """TC-LW-021: list_windows on disposed backend raises BackendUnavailableError."""
        backend.dispose()

        with pytest.raises(BackendUnavailableError, match="disposed"):
            backend.list_windows()


# ---------------------------------------------------------------------------
# list_windows P1 targeted tests (QA F3 fix)
# ---------------------------------------------------------------------------


class TestListWindowsP1:
    """P1 test-design cases for list_windows."""

    @pytest.fixture()
    def backend(self) -> WindowsBackend:
        """Create a WindowsBackend bypassing the platform guard."""
        with (
            patch("sys.platform", "win32"),
            patch.dict("sys.modules", {"comtypes": type("mod", (), {})}),
        ):
            b = WindowsBackend.__new__(WindowsBackend)
            b._com_initialized = True
            b._uia = MagicMock()
            b._disposed = False
            b._element_cache = {}
            return b

    def test_create_property_condition_uses_control_type_property_id(
        self, backend: WindowsBackend
    ) -> None:
        """CreatePropertyCondition first arg must be UIA_ControlTypePropertyId (30003)."""
        from pathlight_mcp.backends.windows import _UIA_CONTROL_TYPE_PROPERTY_ID

        assert _UIA_CONTROL_TYPE_PROPERTY_ID == 30003

        mock_array = MagicMock()
        mock_array.Length = 0
        backend._uia.GetRootElement.return_value = MagicMock()
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        backend.list_windows()

        args, _kwargs = backend._uia.CreatePropertyCondition.call_args
        assert args[0] == 30003, f"Expected ControlTypePropertyId=30003, got {args[0]}"

    def test_is_offscreen_property_uses_correct_constant(self, backend: WindowsBackend) -> None:
        """GetCurrentPropertyValue must be called with UIA_IsOffscreenPropertyId (30022)."""
        from pathlight_mcp.backends.windows import _UIA_IS_OFFSCREEN_PROPERTY_ID

        assert _UIA_IS_OFFSCREEN_PROPERTY_ID == 30022

        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.return_value = False
        mock_array = MagicMock()
        mock_array.Length = 1
        mock_array.GetElement.return_value = mock_element
        backend._uia.GetRootElement.return_value = MagicMock()
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        backend.list_windows()

        mock_element.GetCurrentPropertyValue.assert_called_once_with(30022)

    def test_disposed_error_message_is_descriptive(self, backend: WindowsBackend) -> None:
        """Disposed backend error must mention 'disposed' and 'WindowsBackend'."""
        backend.dispose()

        with pytest.raises(BackendUnavailableError) as exc_info:
            backend.list_windows()
        msg = str(exc_info.value).lower()
        assert "disposed" in msg
        assert "windowsbackend" in msg

    def test_findall_receives_root_element_as_second_arg(self, backend: WindowsBackend) -> None:
        """FindAll must receive the root element as its second positional argument."""
        mock_root = MagicMock()
        mock_array = MagicMock()
        mock_array.Length = 0
        backend._uia.GetRootElement.return_value = mock_root
        backend._uia.CreatePropertyCondition.return_value = MagicMock()
        backend._uia.FindAll.return_value = mock_array

        backend.list_windows()

        args, _kwargs = backend._uia.FindAll.call_args
        assert args[1] is mock_root, "FindAll second arg must be the root element"

    def test_backend_unavailable_error_not_wrapped(self, backend: WindowsBackend) -> None:
        """If a BackendUnavailableError occurs inside COM call, it must not be double-wrapped."""
        backend._uia.GetRootElement.side_effect = BackendUnavailableError("already failed")

        with pytest.raises(BackendUnavailableError, match="already failed"):
            backend.list_windows()

    def test_module_constants_are_immutable_integers(self) -> None:
        """Module-level UIA constants must be plain integers (not expressions)."""
        from pathlight_mcp.backends.windows import (
            _UIA_CONTROL_TYPE_PROPERTY_ID,
            _UIA_IS_OFFSCREEN_PROPERTY_ID,
            _UIA_TREE_SCOPE_CHILDREN,
            _UIA_WINDOW_CONTROL_TYPE_ID,
        )

        for name, val in [
            ("_UIA_TREE_SCOPE_CHILDREN", _UIA_TREE_SCOPE_CHILDREN),
            ("_UIA_CONTROL_TYPE_PROPERTY_ID", _UIA_CONTROL_TYPE_PROPERTY_ID),
            ("_UIA_WINDOW_CONTROL_TYPE_ID", _UIA_WINDOW_CONTROL_TYPE_ID),
            ("_UIA_IS_OFFSCREEN_PROPERTY_ID", _UIA_IS_OFFSCREEN_PROPERTY_ID),
        ]:
            assert isinstance(val, int), f"{name} must be int, got {type(val).__name__}"


# ---------------------------------------------------------------------------
# is_valid tests (GW-024)
# ---------------------------------------------------------------------------


class TestIsValid:
    """Tests for WindowsBackend.is_valid (GW-024, GW-087).

    Validates:
    - Disposed backend returns False (never raises).
    - COM IUIAutomationElement handles: property probe success → True.
    - COM IUIAutomationElement handles: property probe failure → False.
    - HWND integer handles: IsWindow returns nonzero → True.
    - HWND integer handles: IsWindow returns zero → False.
    - COM errors are caught and return False (never propagate).
    - ctypes errors are caught and return False.
    - String backend_id handles: cache hit + live COM → True.
    - String backend_id handles: cache hit + stale COM → False.
    - String backend_id handles: cache miss → False.
    """

    @pytest.fixture()
    def backend(self) -> WindowsBackend:
        """Create a WindowsBackend bypassing the platform guard."""
        with (
            patch("sys.platform", "win32"),
            patch.dict("sys.modules", {"comtypes": type("mod", (), {})}),
        ):
            b = WindowsBackend.__new__(WindowsBackend)
            b._com_initialized = True
            b._uia = MagicMock()
            b._disposed = False
            b._element_cache = {}
            return b

    # -- Disposed backend ---------------------------------------------------

    def test_disposed_returns_false(self, backend: WindowsBackend) -> None:
        """A disposed backend must return False, never raise."""
        backend.dispose()
        assert backend.is_valid(NativeHandle(MagicMock())) is False

    # -- COM IUIAutomationElement handles ------------------------------------

    def test_com_element_valid_returns_true(self, backend: WindowsBackend) -> None:
        """A live COM element (property probe succeeds) → True."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.return_value = 1234

        assert backend.is_valid(NativeHandle(mock_element)) is True  # type: ignore[arg-type]
        mock_element.GetCurrentPropertyValue.assert_called_once()

    def test_com_element_stale_returns_false(self, backend: WindowsBackend) -> None:
        """A stale COM element (property probe raises) → False."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = OSError(
            "COM object has been separated from its underlying RCW"
        )

        assert backend.is_valid(NativeHandle(mock_element)) is False  # type: ignore[arg-type]

    def test_com_element_process_id_constant_used(self, backend: WindowsBackend) -> None:
        """is_valid must probe with UIA_ProcessIdPropertyId (30076)."""
        from pathlight_mcp.backends.windows import _UIA_PROCESS_ID_PROPERTY_ID

        assert _UIA_PROCESS_ID_PROPERTY_ID == 30076

        mock_element = MagicMock()
        backend.is_valid(NativeHandle(mock_element))  # type: ignore[arg-type]

        mock_element.GetCurrentPropertyValue.assert_called_once_with(30076)

    def test_com_element_generic_exception_returns_false(self, backend: WindowsBackend) -> None:
        """Any exception from COM property access → False."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = RuntimeError("unexpected")

        assert backend.is_valid(NativeHandle(mock_element)) is False  # type: ignore[arg-type]

    # -- HWND integer handles ------------------------------------------------

    def test_hwnd_valid_returns_true(self, backend: WindowsBackend) -> None:
        """HWND with IsWindow returning nonzero → True."""
        import ctypes

        with patch.object(ctypes.windll.user32, "IsWindow", return_value=1):
            assert backend.is_valid(NativeHandle(12345)) is True  # type: ignore[arg-type]

    def test_hwnd_invalid_returns_false(self, backend: WindowsBackend) -> None:
        """HWND with IsWindow returning zero → False."""
        import ctypes

        with patch.object(ctypes.windll.user32, "IsWindow", return_value=0):
            assert backend.is_valid(NativeHandle(99999)) is False  # type: ignore[arg-type]

    def test_hwnd_zero_returns_false(self, backend: WindowsBackend) -> None:
        """HWND 0x0 → IsWindow returns 0 → False."""
        import ctypes

        with patch.object(ctypes.windll.user32, "IsWindow", return_value=0):
            assert backend.is_valid(NativeHandle(0)) is False  # type: ignore[arg-type]

    def test_hwnd_ctypes_error_returns_false(self, backend: WindowsBackend) -> None:
        """ctypes failure during IsWindow → False."""
        import ctypes

        with patch.object(ctypes.windll.user32, "IsWindow", side_effect=OSError("ctypes error")):
            assert backend.is_valid(NativeHandle(12345)) is False  # type: ignore[arg-type]

    # -- String backend_id handles (GW-087) -----------------------------------

    def test_string_backend_id_in_cache_valid(self, backend: WindowsBackend) -> None:
        """A string backend_id found in cache → probe cached COM element → True."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.return_value = 1234

        backend._element_cache["cached_123"] = mock_element
        assert backend.is_valid(NativeHandle("cached_123")) is True  # type: ignore[arg-type]
        mock_element.GetCurrentPropertyValue.assert_called_once()

    def test_string_backend_id_in_cache_stale(self, backend: WindowsBackend) -> None:
        """A string backend_id found in cache but COM element is stale → False."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = OSError("stale")

        backend._element_cache["cached_456"] = mock_element
        assert backend.is_valid(NativeHandle("cached_456")) is False  # type: ignore[arg-type]

    def test_string_backend_id_not_in_cache_returns_false(self, backend: WindowsBackend) -> None:
        """A string backend_id not in cache → False (no stale_element_reference error)."""
        assert backend.is_valid(NativeHandle("missing_789")) is False  # type: ignore[arg-type]

    # -- Edge cases ----------------------------------------------------------

    def test_none_handle_returns_false(self, backend: WindowsBackend) -> None:
        """None handle → COM probe fails → False."""
        assert backend.is_valid(NativeHandle(None)) is False  # type: ignore[arg-type]

    def test_never_raises_for_any_input(self, backend: WindowsBackend) -> None:
        """is_valid must never raise, regardless of input."""
        for value in [None, "bad", 0, -1, object(), MagicMock()]:
            try:
                result = backend.is_valid(NativeHandle(value))  # type: ignore[arg-type]
                assert isinstance(result, bool)
            except Exception as exc:
                pytest.fail(f"is_valid raised {type(exc).__name__} for {value!r}")

    def test_process_id_constant_is_immutable_integer(self) -> None:
        """_UIA_PROCESS_ID_PROPERTY_ID must be a plain integer."""
        from pathlight_mcp.backends.windows import _UIA_PROCESS_ID_PROPERTY_ID

        assert isinstance(_UIA_PROCESS_ID_PROPERTY_ID, int)
        assert _UIA_PROCESS_ID_PROPERTY_ID == 30076

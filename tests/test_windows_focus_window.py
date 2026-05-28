"""Tests for WindowsBackend.focus_window (GW-021, architecture §3.2).

Dedicated test file for the focus_window implementation and its private
helpers (``_extract_hwnd``, ``_element_from_handle``).

Test cases:
- TC-021-001: Successful foreground activation
- TC-021-002: Invalid handle (IsWindow returns False)
- TC-021-003: SetForegroundWindow returns 0 (both attempts)
- TC-021-004: ArgumentError (non-int NativeHandle)
- TC-021-005: OSError from ctypes windll
- TC-021-006: WindowNotFoundError error_code
- TC-021-007: No return value on success
- TC-021-008: Disposed backend guard
- TC-021-009: Idempotent foreground (success on first call)
- TC-021-010: focus_window calls IsWindow then SetForegroundWindow
- TC-021-011: SetForegroundWindow called with correct HWND
- TC-021-012: HWND=0 raises WindowNotFoundError
- TC-021-013: focus_window docstring describes behavior
- TC-021-014: focus_window is defined on WindowsBackend
- TC-021-015: _extract_hwnd raises WindowNotFoundError for zero handle
- TC-021-016: Foreground lock workaround (keybd_event + retry)
- TC-021-017: _extract_hwnd extracts correct integer
- TC-021-018: SetFocus called after successful foreground
"""

import ctypes
from unittest.mock import MagicMock, patch

import pytest

from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.backends.windows import WindowsBackend
from pathlight_mcp.errors import WindowNotFoundError

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def backend() -> WindowsBackend:
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


def _make_user32(
    is_window: bool = True,
    set_fg_result: int = 1,
    set_fg_results: list[int] | None = None,
) -> MagicMock:
    """Build a mock user32 with the given return values."""
    mock = MagicMock()
    mock.IsWindow.return_value = is_window
    if set_fg_results is not None:
        mock.SetForegroundWindow.side_effect = set_fg_results
    else:
        mock.SetForegroundWindow.return_value = set_fg_result
    return mock


# ---------------------------------------------------------------------------
# _extract_hwnd helper tests
# ---------------------------------------------------------------------------


class TestExtractHwnd:
    """Tests for the _extract_hwnd static helper."""

    # TC-021-017: _extract_hwnd extracts correct integer
    def test_extracts_int_from_native_handle(self) -> None:
        """_extract_hwnd must return the underlying integer from NativeHandle."""
        hwnd = 12345
        result = WindowsBackend._extract_hwnd(NativeHandle(hwnd))
        assert result == hwnd

    def test_extracts_large_handle(self) -> None:
        """_extract_hwnd must handle large HWND values."""
        hwnd = 0x7FFA1234
        result = WindowsBackend._extract_hwnd(NativeHandle(hwnd))
        assert result == hwnd

    # TC-021-012 / TC-021-015: HWND=0 raises WindowNotFoundError
    def test_zero_handle_raises_window_not_found(self) -> None:
        """_extract_hwnd must raise WindowNotFoundError for handle 0."""
        with pytest.raises(WindowNotFoundError, match="0x0"):
            WindowsBackend._extract_hwnd(NativeHandle(0))

    def test_non_int_handle_raises_type_error(self) -> None:
        """_extract_hwnd must raise TypeError for non-int, non-COM values."""
        with pytest.raises(TypeError, match="Cannot extract HWND"):
            WindowsBackend._extract_hwnd(NativeHandle("not-an-int"))  # type: ignore[arg-type]

    def test_negative_handle_passes_through(self) -> None:
        """Negative HWND values pass through without zero-check rejection."""
        # In practice HWND is unsigned; negative just means large positive.
        # Only exactly 0 is rejected.
        result = WindowsBackend._extract_hwnd(NativeHandle(-1))
        assert result == -1


# ---------------------------------------------------------------------------
# focus_window core tests
# ---------------------------------------------------------------------------


class TestFocusWindow:
    """Tests for the focus_window public method."""

    # TC-021-001 / TC-021-009: Successful foreground on first attempt
    def test_success_first_attempt(self, backend: WindowsBackend) -> None:
        """focus_window must succeed when SetForegroundWindow returns nonzero."""
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            result = backend.focus_window(NativeHandle(12345))

        assert result is None
        mock_user32.IsWindow.assert_called_once_with(12345)
        mock_user32.SetForegroundWindow.assert_called_once_with(12345)
        mock_element.SetFocus.assert_called_once()

    # TC-021-002: Invalid handle
    def test_invalid_handle_raises(self, backend: WindowsBackend) -> None:
        """focus_window must raise WindowNotFoundError for invalid HWND."""
        mock_user32 = _make_user32(is_window=False)

        with (
            patch.object(ctypes, "windll", MagicMock(user32=mock_user32)),
            pytest.raises(WindowNotFoundError, match="not a valid window"),
        ):
            backend.focus_window(NativeHandle(99999))

        mock_user32.IsWindow.assert_called_once_with(99999)
        mock_user32.SetForegroundWindow.assert_not_called()

    # TC-021-003: SetForegroundWindow fails both attempts
    def test_set_foreground_fails_both_attempts(self, backend: WindowsBackend) -> None:
        """focus_window must raise when SetForegroundWindow fails after workaround."""
        mock_user32 = _make_user32(
            is_window=True,
            set_fg_results=[0, 0],  # first attempt + retry both fail
        )

        with (
            patch.object(ctypes, "windll", MagicMock(user32=mock_user32)),
            pytest.raises(WindowNotFoundError, match="SetForegroundWindow failed"),
        ):
            backend.focus_window(NativeHandle(12345))

        assert mock_user32.SetForegroundWindow.call_count == 2
        mock_user32.keybd_event.assert_called()  # workaround was triggered

    # TC-021-004: TypeError (non-int NativeHandle that is not a COM element)
    def test_type_error_for_non_int_handle(self, backend: WindowsBackend) -> None:
        """focus_window must raise TypeError for non-numeric, non-COM NativeHandle strings."""
        with pytest.raises(TypeError):
            backend.focus_window(NativeHandle("not-an-int"))

    # TC-021-005: OSError from ctypes windll
    def test_oserror_from_ctypes(self, backend: WindowsBackend) -> None:
        """focus_window must propagate OSError from ctypes.windll access."""
        with (
            patch.object(ctypes, "windll", None, create=True),
            pytest.raises((AttributeError, OSError)),
        ):
            backend.focus_window(NativeHandle(12345))

    # TC-021-006: WindowNotFoundError error_code
    def test_error_code_is_window_not_found(self, backend: WindowsBackend) -> None:
        """WindowNotFoundError from focus_window must use window_not_found code."""
        mock_user32 = _make_user32(is_window=False)

        with (
            patch.object(ctypes, "windll", MagicMock(user32=mock_user32)),
            pytest.raises(WindowNotFoundError) as exc_info,
        ):
            backend.focus_window(NativeHandle(0xDEAD))

        assert exc_info.value.error_code == "window_not_found"

    # TC-021-007: No return value on success
    def test_returns_none_on_success(self, backend: WindowsBackend) -> None:
        """focus_window must return None on success."""
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            result = backend.focus_window(NativeHandle(42))

        assert result is None

    # TC-021-008: Disposed backend guard
    def test_disposed_backend_raises_runtime_error(self, backend: WindowsBackend) -> None:
        """focus_window must raise RuntimeError when backend is disposed."""
        backend._disposed = True

        with pytest.raises(RuntimeError, match="disposed backend"):
            backend.focus_window(NativeHandle(42))

    # TC-021-010: Calls IsWindow then SetForegroundWindow
    def test_calls_is_window_before_set_fg(self, backend: WindowsBackend) -> None:
        """focus_window must call IsWindow before SetForegroundWindow."""
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        call_order: list[str] = []
        mock_user32.IsWindow.side_effect = lambda h: (call_order.append("IsWindow"), True)[-1]
        mock_user32.SetForegroundWindow.side_effect = lambda h: (
            call_order.append("SetForegroundWindow"),
            1,
        )[-1]

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.focus_window(NativeHandle(42))

        assert call_order == ["IsWindow", "SetForegroundWindow"]

    # TC-021-011: SetForegroundWindow called with correct HWND
    def test_set_foreground_called_with_correct_hwnd(self, backend: WindowsBackend) -> None:
        """SetForegroundWindow must receive the exact HWND from NativeHandle."""
        hwnd = 0xABCD
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.focus_window(NativeHandle(hwnd))

        mock_user32.SetForegroundWindow.assert_called_with(hwnd)

    # TC-021-012: HWND=0 raises WindowNotFoundError
    def test_zero_hwnd_raises(self, backend: WindowsBackend) -> None:
        """focus_window must raise WindowNotFoundError for HWND 0."""
        with pytest.raises(WindowNotFoundError, match="0x0"):
            backend.focus_window(NativeHandle(0))


# ---------------------------------------------------------------------------
# Foreground lock workaround tests (architecture §2.4)
# ---------------------------------------------------------------------------


class TestForegroundLockWorkaround:
    """Tests for the keybd_event foreground-lock workaround."""

    # TC-021-016: Workaround succeeds on retry
    def test_workaround_succeeds_on_retry(self, backend: WindowsBackend) -> None:
        """After first SetForegroundWindow fails, keybd_event workaround must succeed."""
        mock_user32 = _make_user32(
            is_window=True,
            set_fg_results=[0, 1],  # first fails, retry succeeds
        )
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            result = backend.focus_window(NativeHandle(12345))

        assert result is None
        assert mock_user32.SetForegroundWindow.call_count == 2
        # keybd_event must be called twice (keydown + keyup)
        assert mock_user32.keybd_event.call_count == 2

    def test_workaround_uses_alt_key(self, backend: WindowsBackend) -> None:
        """keybd_event workaround must use VK_MENU (0x12, Alt key)."""
        mock_user32 = _make_user32(
            is_window=True,
            set_fg_results=[0, 1],
        )
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.focus_window(NativeHandle(12345))

        # Verify keybd_event was called with VK_MENU=0x12
        keybd_calls = mock_user32.keybd_event.call_args_list
        assert len(keybd_calls) == 2
        # First call: key down (flags=0)
        assert keybd_calls[0][0][0] == 0x12  # VK_MENU
        assert keybd_calls[0][0][2] == 0  # no flags (keydown)
        # Second call: key up (flags=KEYEVENTF_KEYUP=0x0002)
        assert keybd_calls[1][0][0] == 0x12  # VK_MENU
        assert keybd_calls[1][0][2] == 0x0002  # KEYEVENTF_KEYUP

    def test_no_workaround_when_first_succeeds(self, backend: WindowsBackend) -> None:
        """keybd_event must NOT be called when first SetForegroundWindow succeeds."""
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.focus_window(NativeHandle(12345))

        mock_user32.keybd_event.assert_not_called()
        mock_user32.SetForegroundWindow.assert_called_once()


# ---------------------------------------------------------------------------
# SetFocus tests (architecture §2.5)
# ---------------------------------------------------------------------------


class TestSetFocus:
    """Tests for the UIA SetFocus call after foreground activation."""

    # TC-021-018: SetFocus called after successful foreground
    def test_set_focus_called_on_success(self, backend: WindowsBackend) -> None:
        """focus_window must call SetFocus on the UIA element after foreground."""
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.focus_window(NativeHandle(12345))

        backend._uia.ElementFromHandle.assert_called_once_with(12345)
        mock_element.SetFocus.assert_called_once()

    def test_set_focus_called_with_correct_hwnd(self, backend: WindowsBackend) -> None:
        """ElementFromHandle must receive the correct HWND."""
        hwnd = 0xBEEF
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.focus_window(NativeHandle(hwnd))

        backend._uia.ElementFromHandle.assert_called_once_with(hwnd)

    def test_set_focus_failure_does_not_raise(self, backend: WindowsBackend) -> None:
        """SetFocus failure must be silently ignored (best-effort)."""
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_element = MagicMock()
        mock_element.SetFocus.side_effect = Exception("COM error")
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            # Should NOT raise despite SetFocus failing
            result = backend.focus_window(NativeHandle(12345))

        assert result is None

    def test_set_focus_called_after_workaround_retry(self, backend: WindowsBackend) -> None:
        """SetFocus must be called even after the workaround path succeeds."""
        mock_user32 = _make_user32(
            is_window=True,
            set_fg_results=[0, 1],
        )
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.focus_window(NativeHandle(12345))

        mock_element.SetFocus.assert_called_once()


# ---------------------------------------------------------------------------
# _element_from_handle helper tests
# ---------------------------------------------------------------------------


class TestElementFromHandle:
    """Tests for the _element_from_handle private helper."""

    def test_delegates_to_uia_element_from_handle(self, backend: WindowsBackend) -> None:
        """_element_from_handle must delegate to self._uia.ElementFromHandle."""
        hwnd = 42
        mock_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_element

        result = backend._element_from_handle(hwnd)

        assert result is mock_element
        backend._uia.ElementFromHandle.assert_called_once_with(hwnd)


# ---------------------------------------------------------------------------
# COM element HWND extraction tests (GW-083 bug fix)
# ---------------------------------------------------------------------------


class TestExtractHwndFromComElement:
    """Tests for _extract_hwnd handling COM IUIAutomationElement handles.

    GW-083: list_windows returns COM IUIAutomationElement pointers, not HWND
    integers.  _extract_hwnd must read the NativeWindowHandle property (30020)
    to extract the HWND from COM elements.
    """

    @staticmethod
    def _make_com_element(hwnd: int = 0x12345) -> MagicMock:
        """Create a mock COM element that raises ValueError on int()."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.return_value = hwnd
        # Make int() raise ValueError to simulate a real COM pointer
        mock_element.__int__ = MagicMock(side_effect=ValueError("COM pointer"))
        return mock_element

    def test_com_element_extracts_hwnd_via_property(self) -> None:
        """_extract_hwnd must read NativeWindowHandle from COM elements."""
        mock_element = self._make_com_element(hwnd=0x12345)

        result = WindowsBackend._extract_hwnd(NativeHandle(mock_element))

        assert result == 0x12345
        mock_element.GetCurrentPropertyValue.assert_called_once_with(30020)

    def test_com_element_with_large_hwnd(self) -> None:
        """_extract_hwnd must handle large HWND values from COM elements."""
        mock_element = self._make_com_element(hwnd=0xFFFFFFFF)

        result = WindowsBackend._extract_hwnd(NativeHandle(mock_element))

        assert result == 0xFFFFFFFF

    def test_com_element_zero_hwnd_raises_window_not_found(self) -> None:
        """_extract_hwnd must raise WindowNotFoundError for COM element with HWND 0."""
        mock_element = self._make_com_element(hwnd=0)

        with pytest.raises(WindowNotFoundError, match="0x0"):
            WindowsBackend._extract_hwnd(NativeHandle(mock_element))

    def test_com_element_property_error_raises_type_error(self) -> None:
        """_extract_hwnd must raise TypeError when COM property read fails."""
        mock_element = self._make_com_element()
        mock_element.GetCurrentPropertyValue.side_effect = OSError("COM error")

        with pytest.raises(TypeError, match="Cannot extract HWND"):
            WindowsBackend._extract_hwnd(NativeHandle(mock_element))

    def test_focus_window_with_com_element_succeeds(self, backend: WindowsBackend) -> None:
        """focus_window must work with COM elements from list_windows."""
        mock_element = self._make_com_element(hwnd=12345)
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_uia_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_uia_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            result = backend.focus_window(NativeHandle(mock_element))

        assert result is None
        mock_user32.IsWindow.assert_called_once_with(12345)
        mock_user32.SetForegroundWindow.assert_called_once_with(12345)

    def test_focus_window_com_element_uses_correct_property_id(
        self, backend: WindowsBackend
    ) -> None:
        """focus_window must read UIA NativeWindowHandle property (30020)."""
        mock_element = self._make_com_element(hwnd=0xBEEF)
        mock_user32 = _make_user32(is_window=True, set_fg_result=1)
        mock_uia_element = MagicMock()
        backend._uia.ElementFromHandle.return_value = mock_uia_element

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.focus_window(NativeHandle(mock_element))

        mock_element.GetCurrentPropertyValue.assert_called_with(30020)

    def test_require_hwnd_with_com_element(self, backend: WindowsBackend) -> None:
        """_require_hwnd must extract HWND from COM elements."""
        mock_element = self._make_com_element(hwnd=12345)
        mock_user32 = MagicMock()
        mock_user32.IsWindow.return_value = True

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            hwnd = backend._require_hwnd(NativeHandle(mock_element))

        assert hwnd == 12345

    def test_minimize_window_with_com_element(self, backend: WindowsBackend) -> None:
        """minimize_window must work with COM elements from list_windows."""
        mock_element = self._make_com_element(hwnd=12345)
        mock_user32 = MagicMock()
        mock_user32.IsWindow.return_value = True

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.minimize_window(NativeHandle(mock_element))

        mock_user32.ShowWindow.assert_called_once_with(12345, backend._SW_MINIMIZE)

    def test_maximize_window_with_com_element(self, backend: WindowsBackend) -> None:
        """maximize_window must work with COM elements from list_windows."""
        mock_element = self._make_com_element(hwnd=12345)
        mock_user32 = MagicMock()
        mock_user32.IsWindow.return_value = True

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.maximize_window(NativeHandle(mock_element))

        mock_user32.ShowWindow.assert_called_once_with(12345, backend._SW_MAXIMIZE)

    def test_restore_window_with_com_element(self, backend: WindowsBackend) -> None:
        """restore_window must work with COM elements from list_windows."""
        mock_element = self._make_com_element(hwnd=12345)
        mock_user32 = MagicMock()
        mock_user32.IsWindow.return_value = True

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)):
            backend.restore_window(NativeHandle(mock_element))

        mock_user32.ShowWindow.assert_called_once_with(12345, backend._SW_RESTORE)

    def test_move_window_with_com_element(self, backend: WindowsBackend) -> None:
        """move_window must work with COM elements from list_windows."""
        mock_element = self._make_com_element(hwnd=12345)
        mock_user32 = MagicMock()
        mock_user32.IsWindow.return_value = True
        # GetWindowRect returns a RECT-like with left/top/right/bottom
        mock_rect = MagicMock()
        mock_rect.left = 100
        mock_rect.top = 200
        mock_rect.right = 800
        mock_rect.bottom = 600
        mock_user32.GetWindowRect.return_value = None

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)), \
             patch("ctypes.wintypes.RECT", return_value=mock_rect), \
             patch("ctypes.byref", return_value=mock_rect):
            backend.move_window(NativeHandle(mock_element), x=50, y=75)

        mock_user32.MoveWindow.assert_called_once_with(12345, 50, 75, 700, 400, True)

    def test_resize_window_with_com_element(self, backend: WindowsBackend) -> None:
        """resize_window must work with COM elements from list_windows."""
        mock_element = self._make_com_element(hwnd=12345)
        mock_user32 = MagicMock()
        mock_user32.IsWindow.return_value = True
        mock_rect = MagicMock()
        mock_rect.left = 100
        mock_rect.top = 200
        mock_rect.right = 800
        mock_rect.bottom = 600
        mock_user32.GetWindowRect.return_value = None

        with patch.object(ctypes, "windll", MagicMock(user32=mock_user32)), \
             patch("ctypes.wintypes.RECT", return_value=mock_rect), \
             patch("ctypes.byref", return_value=mock_rect):
            backend.resize_window(NativeHandle(mock_element), width=1024, height=768)

        mock_user32.MoveWindow.assert_called_once_with(12345, 100, 200, 1024, 768, True)


# ---------------------------------------------------------------------------
# is_valid COM-element tests (AC6)
# ---------------------------------------------------------------------------


class TestIsValidComElement:
    """Tests for is_valid with COM IUIAutomationElement handles (AC6).

    AC6 requires that is_valid correctly handles COM elements returned by
    list_windows / find_elements, probing via GetCurrentPropertyValue
    rather than the Win32 IsWindow API.
    """

    @staticmethod
    def _make_com_element() -> MagicMock:
        """Create a mock COM element that raises ValueError on int()."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.return_value = 1234
        mock_element.__int__ = MagicMock(side_effect=ValueError("COM pointer"))
        return mock_element

    def test_is_valid_returns_true_for_live_com_element(
        self, backend: WindowsBackend
    ) -> None:
        """is_valid must return True for a valid COM element."""
        mock_element = self._make_com_element()

        result = backend.is_valid(NativeHandle(mock_element))

        assert result is True
        mock_element.GetCurrentPropertyValue.assert_called()

    def test_is_valid_returns_false_for_dead_com_element(
        self, backend: WindowsBackend
    ) -> None:
        """is_valid must return False when COM property read raises."""
        mock_element = self._make_com_element()
        mock_element.GetCurrentPropertyValue.side_effect = OSError("COM error")

        result = backend.is_valid(NativeHandle(mock_element))

        assert result is False

    def test_is_valid_returns_false_for_disposed_backend(
        self, backend: WindowsBackend
    ) -> None:
        """is_valid must return False when backend is disposed."""
        backend._disposed = True
        mock_element = self._make_com_element()

        result = backend.is_valid(NativeHandle(mock_element))

        assert result is False

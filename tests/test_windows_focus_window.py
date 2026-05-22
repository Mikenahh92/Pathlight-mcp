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

from guidewire.backends.types import NativeHandle
from guidewire.backends.windows import WindowsBackend
from guidewire.errors import WindowNotFoundError

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

    def test_non_int_handle_raises_value_error(self) -> None:
        """_extract_hwnd must raise ValueError for non-int string values."""
        with pytest.raises(ValueError):
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

    # TC-021-004: ArgumentError (non-int NativeHandle)
    def test_value_error_for_non_int_handle(self, backend: WindowsBackend) -> None:
        """focus_window must raise ValueError for non-numeric NativeHandle strings."""
        with pytest.raises(ValueError):
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

"""Tests for the desktop.launch_app tool handler (GW-054, GW-068).

Validates that the wired launch_app tool:
- Launches an application via subprocess and returns pid.
- Polls backend.list_windows for a matching window and returns a w-ref.
- Returns structured JSON success response with SENSITIVE safety metadata.
- Returns structured JSON error for validation failures and launch errors.
- Falls back to static stub response when no backend is provided.
- Handles readiness timeout gracefully (success with warning, no crash).
- Supports optional args parameter.

GW-068 additions:
- Propagates DISPLAY environment variable on Linux.
- Resolves snap-wrapped binaries to actual paths.
- Detects immediate process crashes (liveness check) and reports error.
"""

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with one pre-existing window."""
    return MockBackend().add_window(title="Explorer", app="explorer.exe", focused=True)


@pytest.fixture()
def ref_store() -> ElementRefStore:
    """Return a fresh ElementRefStore."""
    return ElementRefStore()


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-launch-app")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-launch-app-stub")
    register_all(mcp)
    return mcp


def _make_live_proc(pid: int = 1234) -> MagicMock:
    """Create a mock Popen process that appears alive (poll returns None)."""
    mock_proc = MagicMock()
    mock_proc.pid = pid
    mock_proc.poll.return_value = None
    return mock_proc


def _make_dead_proc(pid: int = 1234, exit_code: int = 1) -> MagicMock:
    """Create a mock Popen process that appears dead (poll returns exit code)."""
    mock_proc = MagicMock()
    mock_proc.pid = pid
    mock_proc.poll.return_value = exit_code
    return mock_proc


# -- Stub mode tests ----------------------------------------------------------


class TestLaunchAppStub:
    """launch_app returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, launch_app should return a static string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.launch_app", arguments={"app": "notepad"}
        )
        assert "notepad" in result[0].text
        assert "Launched" in result[0].text

    async def test_stub_includes_args(self, stub_mcp: FastMCP) -> None:
        """Stub should include args in the message if provided."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "args": ["test.txt"]},
        )
        assert "test.txt" in result[0].text


# -- Wired mode: successful launch with readiness detection -------------------


class TestLaunchAppSuccess:
    """launch_app wired to backend — successful launch and readiness detection."""

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_launch_success_with_window_match(
        self, mock_popen: MagicMock, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Should return JSON with success, ref, title, pid, and SENSITIVE risk."""
        mock_popen.return_value = _make_live_proc(pid=1234)

        # Add a new window that will appear after launch
        # We need to simulate the window appearing after list_windows is called
        original_list_windows = backend.list_windows

        call_count = [0]

        def list_windows_with_new():
            call_count[0] += 1
            # On first call (pre-launch snapshot), return original windows
            # On subsequent calls (polling), add the new window
            if call_count[0] > 1:
                backend.add_window(title="Notepad", app="notepad.exe")
            return original_list_windows()

        backend.list_windows = list_windows_with_new

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "timeout": 3},
        )
        data = json.loads(result[0].text)

        assert data["success"] is True
        assert data["pid"] == 1234
        assert data["risk"] == "sensitive"
        assert data["confirmation_required"] is True
        assert "ref" in data
        assert data["ref"] is not None
        assert data["ref"].startswith("w")

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_launch_calls_popen_correctly(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Should call Popen with the app and args."""
        mock_popen.return_value = _make_live_proc(pid=5678)

        await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "args": ["file.txt"], "timeout": 0},
        )

        args, kwargs = mock_popen.call_args
        assert args[0] == ["notepad", "file.txt"]
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_launch_without_args(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Should call Popen with just the app name when no args."""
        mock_popen.return_value = _make_live_proc(pid=9999)

        await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "calc", "timeout": 0},
        )

        args, _kwargs = mock_popen.call_args
        assert args[0] == ["calc"]


# -- Wired mode: readiness timeout -------------------------------------------


class TestLaunchAppTimeout:
    """launch_app readiness detection — timeout behavior."""

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_timeout_returns_success_with_warning(
        self, mock_popen: MagicMock, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """When no matching window appears, should return success with warning."""
        mock_popen.return_value = _make_live_proc(pid=4321)

        # Backend only has the pre-existing window, no new window will appear
        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "nonexistent_app", "timeout": 0.5},
        )
        data = json.loads(result[0].text)

        assert data["success"] is True
        assert data["pid"] == 4321
        assert data["ref"] is None
        assert "warning" in data
        assert "no matching window" in data["warning"].lower()

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_zero_timeout_skips_readiness(
        self, mock_popen: MagicMock, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """timeout=0 should skip readiness detection entirely."""
        mock_popen.return_value = _make_live_proc(pid=7777)

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "timeout": 0},
        )
        data = json.loads(result[0].text)

        assert data["success"] is True
        assert data["pid"] == 7777
        # No warning when timeout=0 (readiness was not requested)
        assert "warning" not in data


# -- Error handling -----------------------------------------------------------


class TestLaunchAppErrors:
    """launch_app wired to backend — error paths."""

    async def test_empty_app_returns_validation_error(
        self, mcp: FastMCP
    ) -> None:
        """Empty app should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": ""},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"].lower()

    async def test_whitespace_app_returns_validation_error(
        self, mcp: FastMCP
    ) -> None:
        """Whitespace-only app should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "   "},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

    async def test_negative_timeout_returns_validation_error(
        self, mcp: FastMCP
    ) -> None:
        """Negative timeout should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "timeout": -1},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-negative" in data["message"].lower()

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_file_not_found_returns_error(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Should return app_not_found error for FileNotFoundError."""
        mock_popen.side_effect = FileNotFoundError("not found")

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "nonexistent_app_xyz"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "app_not_found"
        assert "nonexistent_app_xyz" in data["message"]

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_os_error_returns_launch_error(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Should return launch_error for generic OSError."""
        mock_popen.side_effect = OSError("permission denied")

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "someapp"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "launch_error"
        assert "someapp" in data["message"]


# -- Safety metadata ----------------------------------------------------------


class TestLaunchAppSafety:
    """launch_app safety classification."""

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_risk_level_is_sensitive(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """app_launch should be classified as SENSITIVE."""
        mock_popen.return_value = _make_live_proc(pid=1234)

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "timeout": 0},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "sensitive"
        assert data["confirmation_required"] is True


# -- Schema validation --------------------------------------------------------


class TestLaunchAppSchema:
    """launch_app tool schema is correct."""

    async def test_tool_name_registered(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.launch_app."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.launch_app" in names

    async def test_required_params(self, mcp: FastMCP) -> None:
        """app should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.launch_app")
        schema = tool.inputSchema
        assert "app" in schema["required"]

    async def test_optional_params(self, mcp: FastMCP) -> None:
        """args and timeout should be optional parameters."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.launch_app")
        schema = tool.inputSchema
        required = schema.get("required", [])
        assert "args" not in required
        assert "timeout" not in required

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.launch_app")
        assert tool.description is not None
        assert len(tool.description) > 0


# -- Window matching ----------------------------------------------------------


class TestLaunchAppWindowMatching:
    """launch_app readiness detection — window matching logic."""

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_matches_by_app_name(
        self, mock_popen: MagicMock, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Should match window by app_name containing the app stem."""
        mock_popen.return_value = _make_live_proc(pid=1111)

        original_list_windows = backend.list_windows
        call_count = [0]

        def list_windows_with_new():
            call_count[0] += 1
            if call_count[0] > 1:
                backend.add_window(title="My Document", app="myapp.exe")
            return original_list_windows()

        backend.list_windows = list_windows_with_new

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "myapp", "timeout": 3},
        )
        data = json.loads(result[0].text)

        assert data["success"] is True
        assert data["ref"] is not None
        assert data["title"] == "My Document"

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_matches_by_title(
        self, mock_popen: MagicMock, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Should match window by title containing the app stem."""
        mock_popen.return_value = _make_live_proc(pid=2222)

        original_list_windows = backend.list_windows
        call_count = [0]

        def list_windows_with_new():
            call_count[0] += 1
            if call_count[0] > 1:
                backend.add_window(title="Calculator", app="calc.exe")
            return original_list_windows()

        backend.list_windows = list_windows_with_new

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "calc", "timeout": 3},
        )
        data = json.loads(result[0].text)

        assert data["success"] is True
        assert data["ref"] is not None

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_matches_by_full_path(
        self, mock_popen: MagicMock, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Should match window when app is a full path and app_name matches."""
        mock_popen.return_value = _make_live_proc(pid=3333)

        original_list_windows = backend.list_windows
        call_count = [0]

        def list_windows_with_new():
            call_count[0] += 1
            if call_count[0] > 1:
                backend.add_window(title="Untitled", app="notepad.exe")
            return original_list_windows()

        backend.list_windows = list_windows_with_new

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "C:\\Windows\\notepad.exe", "timeout": 3},
        )
        data = json.loads(result[0].text)

        assert data["success"] is True
        assert data["ref"] is not None

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_registers_w_prefixed_ref(
        self, mock_popen: MagicMock, mcp: FastMCP, backend: MockBackend, ref_store: ElementRefStore
    ) -> None:
        """Should register the matched window handle with a w-prefixed ref."""
        mock_popen.return_value = _make_live_proc(pid=4444)

        original_list_windows = backend.list_windows
        call_count = [0]

        def list_windows_with_new():
            call_count[0] += 1
            if call_count[0] > 1:
                backend.add_window(title="Editor", app="editor.exe")
            return original_list_windows()

        backend.list_windows = list_windows_with_new

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "editor", "timeout": 3},
        )
        data = json.loads(result[0].text)

        assert data["success"] is True
        ref = data["ref"]
        assert ref is not None
        assert ref.startswith("w")
        # The ref should be resolvable in the store
        handle = ref_store.resolve(ref)
        assert handle is not None


# -- GW-068: Post-launch liveness check --------------------------------------


class TestLaunchAppLivenessCheck:
    """launch_app detects immediate crashes (GW-068)."""

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_immediate_crash_returns_error(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """When process exits immediately, should return launch_error."""
        mock_popen.return_value = _make_dead_proc(pid=5555, exit_code=127)

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "crashing_app", "timeout": 3},
        )
        data = json.loads(result[0].text)

        assert data.get("success") is None or data.get("success") is not True
        assert data["error"] == "launch_error"
        assert "exited immediately" in data["message"]
        assert "127" in data["message"]
        assert "hints" in data

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_immediate_crash_with_code_1(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Crash with exit code 1 should be reported."""
        mock_popen.return_value = _make_dead_proc(pid=6666, exit_code=1)

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "bad_app", "timeout": 0},
        )
        data = json.loads(result[0].text)

        assert data["error"] == "launch_error"
        assert "code 1" in data["message"]

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_live_process_succeeds(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """A process that stays alive should not trigger liveness error."""
        mock_popen.return_value = _make_live_proc(pid=7777)

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "timeout": 0},
        )
        data = json.loads(result[0].text)

        assert data["success"] is True
        assert data["pid"] == 7777


# -- GW-068: DISPLAY propagation ---------------------------------------------


class TestLaunchAppDisplayPropagation:
    """launch_app propagates DISPLAY on Linux (GW-068)."""

    def test_build_env_returns_none_on_non_linux(self) -> None:
        """On non-Linux platforms, _build_env should return None."""
        from guidewire.tools.launch_app import _build_env

        with patch("guidewire.tools.launch_app.sys") as mock_sys:
            mock_sys.platform = "win32"
            assert _build_env() is None

    def test_build_env_copies_display(self) -> None:
        """_build_env should propagate DISPLAY from environment."""
        from guidewire.tools.launch_app import _build_env

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch.dict(os.environ, {"DISPLAY": ":0", "PATH": "/usr/bin"}):
            mock_sys.platform = "linux"
            env = _build_env()
            assert env is not None
            assert env["DISPLAY"] == ":0"

    def test_build_env_defaults_display_if_missing(self) -> None:
        """_build_env should default DISPLAY to :0 if not set."""
        from guidewire.tools.launch_app import _build_env

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            mock_sys.platform = "linux"
            env = _build_env()
            assert env is not None
            assert env["DISPLAY"] == ":0"

    def test_build_env_defaults_display_if_empty(self) -> None:
        """_build_env should default DISPLAY to :0 if empty string."""
        from guidewire.tools.launch_app import _build_env

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch.dict(os.environ, {"DISPLAY": "", "PATH": "/usr/bin"}):
            mock_sys.platform = "linux"
            env = _build_env()
            assert env is not None
            assert env["DISPLAY"] == ":0"

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_env_passed_to_popen(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Popen should receive an env dict on the current platform."""
        mock_popen.return_value = _make_live_proc(pid=8888)

        await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "timeout": 0},
        )

        _, kwargs = mock_popen.call_args
        # On Linux, env should be a dict with DISPLAY; on other platforms, None
        if sys.platform == "linux":
            assert kwargs["env"] is not None
            assert "DISPLAY" in kwargs["env"]
        else:
            # On non-Linux, _build_env returns None which means Popen inherits
            assert kwargs["env"] is None


# -- GW-068: Snap binary resolution ------------------------------------------


class TestLaunchAppSnapResolution:
    """launch_app resolves snap-wrapped binaries on Linux (GW-068)."""

    def test_resolve_snap_binary_non_linux(self) -> None:
        """On non-Linux, _resolve_snap_binary returns app unchanged."""
        from guidewire.tools.launch_app import _resolve_snap_binary

        with patch("guidewire.tools.launch_app.sys") as mock_sys:
            mock_sys.platform = "win32"
            assert _resolve_snap_binary("/snap/bin/firefox") == "/snap/bin/firefox"

    def test_resolve_snap_binary_non_snap_path(self) -> None:
        """Non-snap paths should be returned unchanged."""
        from guidewire.tools.launch_app import _resolve_snap_binary

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch("os.path.isfile", return_value=False):
            mock_sys.platform = "linux"
            assert _resolve_snap_binary("/usr/bin/firefox") == "/usr/bin/firefox"

    def test_resolve_snap_binary_snap_prefix(self) -> None:
        """Snap prefix path should trigger resolution."""
        from guidewire.tools.launch_app import _resolve_snap_binary

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch(
                 "guidewire.tools.launch_app._try_resolve_snap",
                 return_value="/usr/bin/firefox",
             ) as mock_resolve:
            mock_sys.platform = "linux"
            result = _resolve_snap_binary("/snap/bin/firefox")
            assert result == "/usr/bin/firefox"
            mock_resolve.assert_called_once_with("firefox", "/snap/bin/firefox")

    def test_resolve_snap_binary_bare_name_with_snap(self) -> None:
        """Bare name that has a snap wrapper should trigger resolution."""
        from guidewire.tools.launch_app import _resolve_snap_binary

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True), \
             patch(
                 "guidewire.tools.launch_app._try_resolve_snap",
                 return_value="/snap/bin/firefox",
             ) as mock_resolve:
            mock_sys.platform = "linux"
            _resolve_snap_binary("firefox")
            mock_resolve.assert_called_once_with("firefox", "/snap/bin/firefox")

    def test_resolve_snap_binary_bare_name_no_snap(self) -> None:
        """Bare name without snap wrapper should be returned unchanged."""
        from guidewire.tools.launch_app import _resolve_snap_binary

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch("os.path.isfile", return_value=False):
            mock_sys.platform = "linux"
            result = _resolve_snap_binary("notepad")
            assert result == "notepad"

    def test_try_resolve_snap_which_succeeds(self) -> None:
        """_try_resolve_snap should use which to find the binary."""
        from guidewire.tools.launch_app import _try_resolve_snap

        mock_result = MagicMock()
        mock_result.stdout = "/usr/bin/firefox\n"
        with patch("subprocess.run", return_value=mock_result):
            result = _try_resolve_snap("firefox", "/snap/bin/firefox")
            assert result == "/usr/bin/firefox"

    def test_try_resolve_snap_which_returns_snap_path(self) -> None:
        """If which returns a snap path, should fall through to wrapper reading."""
        from guidewire.tools.launch_app import _try_resolve_snap

        mock_result = MagicMock()
        mock_result.stdout = "/snap/bin/firefox\n"
        with patch("subprocess.run", return_value=mock_result), \
             patch("builtins.open", side_effect=OSError("no file")):
            result = _try_resolve_snap("firefox", "/snap/bin/firefox")
            # Should return fallback since both strategies fail
            assert result == "/snap/bin/firefox"

    def test_try_resolve_snap_wrapper_exec(self) -> None:
        """_try_resolve_snap should parse exec line from wrapper script."""
        from guidewire.tools.launch_app import _try_resolve_snap

        wrapper_content = (
            '#!/bin/sh\n'
            'exec /snap/firefox/current/usr/lib/firefox/firefox "$@"\n'
        )
        mock_result = MagicMock()
        mock_result.stdout = "/snap/bin/firefox\n"

        mock_file = MagicMock()
        mock_file.__enter__ = lambda s: s
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.read.return_value = wrapper_content

        with patch("subprocess.run", return_value=mock_result), \
             patch("guidewire.tools.launch_app.open", return_value=mock_file), \
             patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True):
            result = _try_resolve_snap("firefox", "/snap/bin/firefox")
            assert result == "/snap/firefox/current/usr/lib/firefox/firefox"

    def test_try_resolve_snap_fallback(self) -> None:
        """When all resolution strategies fail, should return fallback."""
        from guidewire.tools.launch_app import _try_resolve_snap

        with patch("subprocess.run", side_effect=OSError("no which")), \
             patch("builtins.open", side_effect=OSError("no file")):
            result = _try_resolve_snap("myapp", "/snap/bin/myapp")
            assert result == "/snap/bin/myapp"


# -- GW-068: Error hints ------------------------------------------------------


class TestLaunchAppErrorHints:
    """launch_app includes hints from registry in error responses (GW-068)."""

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_app_not_found_includes_hints(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """app_not_found error should include hints from registry."""
        mock_popen.side_effect = FileNotFoundError("not found")

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "missing_app"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "app_not_found"
        assert "hints" in data
        assert len(data["hints"]) > 0

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_os_error_includes_hints(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """launch_error from OSError should include hints from registry."""
        mock_popen.side_effect = OSError("permission denied")

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "someapp"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "launch_error"
        assert "hints" in data
        assert len(data["hints"]) > 0

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_crash_liveness_includes_hints(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Crash detected by liveness check should include hints from registry."""
        mock_popen.return_value = _make_dead_proc(pid=1234, exit_code=1)

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "crashing_app"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "launch_error"
        assert "hints" in data
        assert len(data["hints"]) > 0


# -- GW-068: Electron detection -----------------------------------------------


class TestLaunchAppElectronDetection:
    """Electron/Chromium detection and --no-sandbox injection (GW-068)."""

    def test_is_electron_binary_code(self) -> None:
        """VS Code binary should be detected as Electron."""
        from guidewire.tools.launch_app import _is_electron_binary

        assert _is_electron_binary("/snap/code/current/usr/share/code/code") is True

    def test_is_electron_binary_chrome(self) -> None:
        """Google Chrome should be detected as Electron/Chromium."""
        from guidewire.tools.launch_app import _is_electron_binary

        assert _is_electron_binary("/usr/bin/google-chrome-stable") is True

    def test_is_electron_binary_electron_named(self) -> None:
        """Binary named 'electron' should be detected."""
        from guidewire.tools.launch_app import _is_electron_binary

        assert _is_electron_binary("/usr/bin/electron") is True

    def test_is_electron_binary_electron_versioned(self) -> None:
        """Versioned electron binary like 'electron25' should be detected."""
        from guidewire.tools.launch_app import _is_electron_binary

        assert _is_electron_binary("/usr/bin/electron25") is True

    def test_is_electron_binary_regular_app(self) -> None:
        """Regular app like gedit should NOT be detected as Electron."""
        from guidewire.tools.launch_app import _is_electron_binary

        assert _is_electron_binary("/usr/bin/gedit") is False

    def test_is_electron_binary_firefox(self) -> None:
        """Firefox should NOT be detected as Electron."""
        from guidewire.tools.launch_app import _is_electron_binary

        assert _is_electron_binary("/usr/bin/firefox") is False

    def test_is_electron_binary_chrome_sandbox_sibling(self) -> None:
        """Binary with chrome-sandbox sibling should be detected as Electron/Chromium."""
        from guidewire.tools.launch_app import _is_electron_binary

        with patch("os.path.isfile", return_value=True):
            assert _is_electron_binary("/opt/myapp/myapp-bin") is True

    def test_is_electron_binary_no_sandbox_sibling(self) -> None:
        """Binary without chrome-sandbox sibling should not be detected via sibling check."""
        from guidewire.tools.launch_app import _is_electron_binary

        with patch("os.path.isfile", return_value=False):
            assert _is_electron_binary("/usr/bin/gedit") is False

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_no_sandbox_injected_on_linux_electron(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """On Linux, Electron app should get --no-sandbox auto-injected."""
        mock_popen.return_value = _make_live_proc(pid=9901)

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch(
                 "guidewire.tools.launch_app._is_electron_binary",
                 return_value=True,
             ):
            mock_sys.platform = "linux"
            # Need to also make _build_env work
            with patch.dict(os.environ, {"DISPLAY": ":0", "PATH": "/usr/bin"}):
                await mcp.call_tool(
                    "desktop.launch_app",
                    arguments={"app": "code", "timeout": 0},
                )

        # Check that --no-sandbox was appended
        args, _ = mock_popen.call_args
        assert "--no-sandbox" in args[0]

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_no_sandbox_not_injected_on_non_linux(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """On non-Linux, Electron app should NOT get --no-sandbox auto-injected."""
        mock_popen.return_value = _make_live_proc(pid=9902)

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch(
                 "guidewire.tools.launch_app._is_electron_binary",
                 return_value=True,
             ):
            mock_sys.platform = "win32"
            await mcp.call_tool(
                "desktop.launch_app",
                arguments={"app": "code", "timeout": 0},
            )

        args, _ = mock_popen.call_args
        assert "--no-sandbox" not in args[0]

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_no_sandbox_not_doubled_if_already_in_args(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """If --no-sandbox is already in args, it should not be added again."""
        mock_popen.return_value = _make_live_proc(pid=9903)

        with patch("guidewire.tools.launch_app.sys") as mock_sys, \
             patch(
                 "guidewire.tools.launch_app._is_electron_binary",
                 return_value=True,
             ):
            mock_sys.platform = "linux"
            with patch.dict(os.environ, {"DISPLAY": ":0", "PATH": "/usr/bin"}):
                await mcp.call_tool(
                    "desktop.launch_app",
                    arguments={"app": "code", "args": ["--no-sandbox"], "timeout": 0},
                )

        args, _ = mock_popen.call_args
        # Should appear exactly once (from the user's args, not duplicated)
        assert args[0].count("--no-sandbox") == 1

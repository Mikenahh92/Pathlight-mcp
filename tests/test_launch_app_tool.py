"""Tests for the desktop.launch_app tool handler (GW-054).

Validates that the wired launch_app tool:
- Launches an application via subprocess and returns pid.
- Polls backend.list_windows for a matching window and returns a w-ref.
- Returns structured JSON success response with SENSITIVE safety metadata.
- Returns structured JSON error for validation failures and launch errors.
- Falls back to static stub response when no backend is provided.
- Handles readiness timeout gracefully (success with warning, no crash).
- Supports optional args parameter.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.types import NativeHandle
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
        # Setup: Popen returns a process with pid 1234
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

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
        mock_proc = MagicMock()
        mock_proc.pid = 5678
        mock_popen.return_value = mock_proc

        await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "notepad", "args": ["file.txt"], "timeout": 0},
        )

        mock_popen.assert_called_once_with(
            ["notepad", "file.txt"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_launch_without_args(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Should call Popen with just the app name when no args."""
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_popen.return_value = mock_proc

        await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "calc", "timeout": 0},
        )

        mock_popen.assert_called_once_with(
            ["calc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


# -- Wired mode: readiness timeout -------------------------------------------


class TestLaunchAppTimeout:
    """launch_app readiness detection — timeout behavior."""

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_timeout_returns_success_with_warning(
        self, mock_popen: MagicMock, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """When no matching window appears, should return success with warning."""
        mock_proc = MagicMock()
        mock_proc.pid = 4321
        mock_popen.return_value = mock_proc

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
        mock_proc = MagicMock()
        mock_proc.pid = 7777
        mock_popen.return_value = mock_proc

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
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

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
        mock_proc = MagicMock()
        mock_proc.pid = 1111
        mock_popen.return_value = mock_proc

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
        mock_proc = MagicMock()
        mock_proc.pid = 2222
        mock_popen.return_value = mock_proc

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
        mock_proc = MagicMock()
        mock_proc.pid = 3333
        mock_popen.return_value = mock_proc

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
        mock_proc = MagicMock()
        mock_proc.pid = 4444
        mock_popen.return_value = mock_proc

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

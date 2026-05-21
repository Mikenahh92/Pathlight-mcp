"""Linux integration test for desktop.launch_app (GW-068, AC-5/T9).

Validates the Linux-specific launch_app behaviour end-to-end:

1. DISPLAY propagation — child process receives DISPLAY env var.
2. Snap binary resolution — ``/snap/bin/<name>`` resolves to real binary.
3. Electron ``--no-sandbox`` injection — Electron apps auto-receive
   ``--no-sandbox`` when run via snap or directly.
4. Post-launch liveness check — immediately-crashing processes are
   reported as errors rather than false successes.

Tests are gated by:
- ``@pytest.mark.integration`` (requires ``GUIDEWARE_RUN_INTEGRATION=1``)
- ``@pytest.mark.skipif`` on non-Linux platforms
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all

skip_not_linux = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux launch_app integration test requires Linux platform",
)


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
    mcp = FastMCP(name="test-launch-app-linux-integration")
    register_all(mcp, backend=backend, ref_store=ref_store)
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


# -- Integration tests --------------------------------------------------------


@skip_not_linux
@pytest.mark.integration
class TestLaunchAppLinuxIntegration:
    """Linux-specific launch_app integration tests (GW-068, AC-5/T9).

    These tests validate the Linux-specific code paths in launch_app:
    DISPLAY propagation, snap resolution with Electron detection,
    and the post-launch liveness check.
    """

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_display_propagated_to_child(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """On Linux, Popen should receive an env dict containing DISPLAY."""
        mock_popen.return_value = _make_live_proc(pid=9001)

        await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "gedit", "timeout": 0},
        )

        _, kwargs = mock_popen.call_args
        assert kwargs["env"] is not None
        assert "DISPLAY" in kwargs["env"]

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_snap_binary_resolved_before_launch(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Snap-wrapped binary should be resolved before being passed to Popen."""
        mock_popen.return_value = _make_live_proc(pid=9002)

        with patch(
            "guidewire.tools.launch_app._resolve_snap_binary",
            return_value="/usr/bin/firefox",
        ) as mock_resolve:
            await mcp.call_tool(
                "desktop.launch_app",
                arguments={"app": "firefox", "timeout": 0},
            )

            mock_resolve.assert_called_once_with("firefox")
            args, _ = mock_popen.call_args
            assert args[0][0] == "/usr/bin/firefox"

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_electron_app_gets_no_sandbox(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Electron apps should automatically receive --no-sandbox flag.

        When the resolved binary is detected as an Electron/Chromium app
        (e.g. via chrome-sandbox sibling or electron binary name),
        ``--no-sandbox`` should be injected automatically.
        """
        mock_popen.return_value = _make_live_proc(pid=9003)

        with patch(
            "guidewire.tools.launch_app._resolve_snap_binary",
            return_value="/snap/code/current/usr/share/code/code",
        ), patch(
            "guidewire.tools.launch_app._is_electron_binary",
            return_value=True,
        ):
            result, _meta = await mcp.call_tool(
                "desktop.launch_app",
                arguments={"app": "code", "timeout": 0},
            )

            args, _ = mock_popen.call_args
            cmd = args[0]
            assert "--no-sandbox" in cmd
            data = json.loads(result[0].text)
            assert data["success"] is True

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_non_electron_app_no_sandbox(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Non-Electron apps should NOT receive --no-sandbox."""
        mock_popen.return_value = _make_live_proc(pid=9004)

        with patch(
            "guidewire.tools.launch_app._resolve_snap_binary",
            return_value="/usr/bin/gedit",
        ), patch(
            "guidewire.tools.launch_app._is_electron_binary",
            return_value=False,
        ):
            await mcp.call_tool(
                "desktop.launch_app",
                arguments={"app": "gedit", "timeout": 0},
            )

            args, _ = mock_popen.call_args
            cmd = args[0]
            assert "--no-sandbox" not in cmd

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_liveness_check_detects_crash(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """Post-launch liveness check should detect immediate crash."""
        mock_popen.return_value = _make_dead_proc(pid=9005, exit_code=127)

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "crashing_app", "timeout": 3},
        )

        data = json.loads(result[0].text)
        assert data.get("success") is not True
        assert data["error"] == "launch_error"
        assert "exited immediately" in data["message"]
        assert "127" in data["message"]
        assert "hints" in data

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_live_process_succeeds(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """A process that stays alive after liveness check should succeed."""
        mock_popen.return_value = _make_live_proc(pid=9006)

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "gedit", "timeout": 0},
        )

        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["pid"] == 9006

    @patch("guidewire.tools.launch_app.subprocess.Popen")
    async def test_launch_app_not_found_includes_hints(
        self, mock_popen: MagicMock, mcp: FastMCP
    ) -> None:
        """app_not_found error should include hints from registry."""
        mock_popen.side_effect = FileNotFoundError("not found")

        result, _meta = await mcp.call_tool(
            "desktop.launch_app",
            arguments={"app": "nonexistent_linux_app"},
        )

        data = json.loads(result[0].text)
        assert data["error"] == "app_not_found"
        assert "hints" in data
        assert len(data["hints"]) > 0

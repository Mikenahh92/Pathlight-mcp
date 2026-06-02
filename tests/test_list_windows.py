"""Tests for the desktop.list_windows MCP tool (GW-010).

Validates that:
- The tool calls backend.list_windows() and backend.get_window_info().
- Each window receives a w-prefixed ref via ElementRefStore.
- The returned dict has the correct wrapped format (windows, count, risk, target_summary).
- BackendUnavailableError is raised (not swallowed).
- Empty backend window lists produce an empty windows list.
- Multiple windows receive sequential refs (w1, w2, ...).
- The tool works through the full PathlightMCPServer pipeline with MockBackend.
- Session-scoped ref_store accumulates refs across calls (TC-10).
- Risk metadata is present on every response (TC-13).
- Stale windows are skipped gracefully (TC-08/TC-09).
- Bounds key is omitted when None (TC-12).
"""

import json

import pytest

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import ElementBounds
from pathlight_mcp.errors import BackendUnavailableError
from pathlight_mcp.server import PathlightMCPServer

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend():
    """Return a fresh MockBackend with sample windows."""
    return (
        MockBackend()
        .add_window(title="Notepad", app="notepad.exe", focused=True)
        .add_window(
            title="Calculator",
            app="calc.exe",
            focused=False,
            bounds=ElementBounds(x=100, y=200, width=400, height=300),
        )
    )


@pytest.fixture()
def server(backend):
    """Return a PathlightMCPServer wired with the mock backend."""
    srv = PathlightMCPServer(backend=backend)
    srv.register_tools()
    return srv


@pytest.fixture()
def empty_backend():
    """Return a MockBackend with no windows."""
    return MockBackend()


@pytest.fixture()
def empty_server(empty_backend):
    """Return a PathlightMCPServer wired with an empty mock backend."""
    srv = PathlightMCPServer(backend=empty_backend)
    srv.register_tools()
    return srv


@pytest.fixture()
def stub_server():
    """Return a PathlightMCPServer with no backend (stub mode)."""
    srv = PathlightMCPServer()
    srv.register_tools()
    return srv


# -- Helper -------------------------------------------------------------------


def _parse_result(result) -> dict:
    """Parse the MCP tool result text as JSON."""
    return json.loads(result[0].text)


# -- Direct tool invocation tests ---------------------------------------------


class TestListWindowsDirect:
    """Tests for list_windows tool invoked via the MCP call_tool API."""

    async def test_returns_wrapped_dict(self, server):
        """Tool should return a wrapped dict (not bare array)."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert isinstance(data, dict)
        assert "windows" in data
        assert "count" in data
        assert "risk" in data
        assert "target_summary" in data

    async def test_correct_window_count(self, server):
        """Should return one entry per backend window."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert data["count"] == 2
        assert len(data["windows"]) == 2

    async def test_window_has_ref_field(self, server):
        """Each window should have a 'ref' field with w-prefix."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        for window in data["windows"]:
            assert "ref" in window
            assert window["ref"].startswith("w")

    async def test_sequential_refs(self, server):
        """Windows should receive sequential refs: w1, w2, ..."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        refs = [w["ref"] for w in data["windows"]]
        assert refs == ["w1", "w2"]

    async def test_title_field(self, server):
        """Each window should have the correct title from the backend."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        titles = [w["title"] for w in data["windows"]]
        assert titles == ["Notepad", "Calculator"]

    async def test_app_name_field(self, server):
        """Each window should have the correct app_name from the backend."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        apps = [w["app_name"] for w in data["windows"]]
        assert apps == ["notepad.exe", "calc.exe"]

    async def test_focused_field(self, server):
        """Each window should have the correct focused boolean."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        focused = [w["focused"] for w in data["windows"]]
        assert focused == [True, False]

    async def test_bounds_field(self, server):
        """Each window should have the correct bounds from the backend."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        # First window uses default bounds (0, 0, 800, 600)
        assert data["windows"][0]["bounds"] == {"x": 0, "y": 0, "width": 800, "height": 600}
        # Second window has custom bounds
        assert data["windows"][1]["bounds"] == {"x": 100, "y": 200, "width": 400, "height": 300}

    async def test_empty_backend_returns_empty_windows(self, empty_server):
        """Backend with no windows should return empty windows list."""
        result, _meta = await empty_server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert data["windows"] == []
        assert data["count"] == 0

    async def test_all_required_fields_present(self, server):
        """Each window should have: ref, title, app_name, focused (bounds optional)."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        for window in data["windows"]:
            assert "ref" in window
            assert "title" in window
            assert "app_name" in window
            assert "focused" in window


# -- Stub mode tests (no backend) ---------------------------------------------


class TestListWindowsStubMode:
    """Tests for list_windows when no backend is provided (stub mode)."""

    async def test_stub_returns_wrapped_empty_dict(self, stub_server):
        """Without a backend, list_windows should return wrapped dict with empty windows."""
        result, _meta = await stub_server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert data["windows"] == []
        assert data["count"] == 0
        assert data["risk"] == "read"
        assert data["target_summary"] == "desktop windows"


# -- Server integration tests -------------------------------------------------


class TestListWindowsServerIntegration:
    """Tests for list_windows through the full PathlightMCPServer pipeline."""

    async def test_tool_discoverable_with_backend(self, server):
        """desktop.list_windows should be discoverable via list_tools."""
        tools = await server.mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.list_windows" in names

    async def test_tool_description(self, server):
        """Tool should have a description mentioning visible windows."""
        tools = await server.mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.list_windows")
        assert tool.description is not None
        assert "window" in tool.description.lower()

    async def test_server_without_backend_still_registers_all_tools(self, stub_server):
        """All tools should be registered even without a backend."""
        tools = await stub_server.mcp.list_tools()
        assert len(tools) == 32

    async def test_server_with_backend_registers_all_tools(self, server):
        """All tools should be registered with a backend."""
        tools = await server.mcp.list_tools()
        assert len(tools) == 32


# -- Error handling tests -----------------------------------------------------


class TestListWindowsErrorHandling:
    """Tests for error handling in list_windows."""

    async def test_backend_unavailable_raises(self):
        """BackendUnavailableError should propagate (not be swallowed)."""
        from unittest.mock import MagicMock

        from mcp.server.fastmcp.exceptions import ToolError

        failing_backend = MagicMock()
        failing_backend.list_windows.side_effect = BackendUnavailableError("Not available")
        srv = PathlightMCPServer(backend=failing_backend)
        srv.register_tools()

        with pytest.raises(ToolError, match="Not available"):
            await srv.mcp.call_tool("desktop.list_windows", arguments={})

    async def test_stale_window_skipped_gracefully(self):
        """A stale window (get_window_info raises) should be skipped, others returned."""

        backend = MockBackend()
        backend.add_window(title="Good", app="good.exe")
        backend.add_window(title="Bad", app="bad.exe")
        backend.add_window(title="Also Good", app="also.exe")

        srv = PathlightMCPServer(backend=backend)
        srv.register_tools()

        # Patch get_window_info to fail for the second handle only
        original_gwi = backend.get_window_info
        call_count = 0

        def patched_gwi(handle):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("stale")
            return original_gwi(handle)

        backend.get_window_info = patched_gwi

        result, _meta = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        # Should have 2 windows (stale one skipped)
        assert data["count"] == 2
        titles = [w["title"] for w in data["windows"]]
        assert titles == ["Good", "Also Good"]

    async def test_all_stale_windows_returns_empty(self):
        """If all windows are stale, should return empty windows list."""
        from unittest.mock import MagicMock

        backend = MockBackend()
        backend.add_window(title="Stale1", app="s1.exe")
        backend.add_window(title="Stale2", app="s2.exe")

        srv = PathlightMCPServer(backend=backend)
        srv.register_tools()

        # Patch get_window_info to always fail
        backend.get_window_info = MagicMock(side_effect=RuntimeError("stale"))

        result, _meta = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert data["windows"] == []
        assert data["count"] == 0


# -- ElementRefStore integration tests ----------------------------------------


class TestListWindowsRefStore:
    """Tests for w-prefixed reference assignment."""

    async def test_single_window_gets_w1(self):
        """A single window should get ref 'w1'."""
        backend = MockBackend().add_window(title="Solo", app="solo.exe")
        srv = PathlightMCPServer(backend=backend)
        srv.register_tools()

        result, _meta = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert len(data["windows"]) == 1
        assert data["windows"][0]["ref"] == "w1"

    async def test_three_windows_get_w1_w2_w3(self):
        """Three windows should get refs w1, w2, w3."""
        backend = (
            MockBackend()
            .add_window(title="A", app="a.exe")
            .add_window(title="B", app="b.exe")
            .add_window(title="C", app="c.exe")
        )
        srv = PathlightMCPServer(backend=backend)
        srv.register_tools()

        result, _meta = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        refs = [w["ref"] for w in data["windows"]]
        assert refs == ["w1", "w2", "w3"]

    async def test_session_scoped_ref_accumulation(self):
        """Session-scoped ref_store should accumulate refs across calls (TC-10)."""
        backend = MockBackend().add_window(title="Persistent", app="p.exe")
        srv = PathlightMCPServer(backend=backend)
        srv.register_tools()

        # First call — should get w1
        result1, _ = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data1 = _parse_result(result1)
        assert data1["windows"][0]["ref"] == "w1"

        # Second call — session-scoped store means w2 (not w1 again)
        result2, _ = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data2 = _parse_result(result2)
        assert data2["windows"][0]["ref"] == "w2"

        # Third call — w3
        result3, _ = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data3 = _parse_result(result3)
        assert data3["windows"][0]["ref"] == "w3"

    async def test_ref_store_accessible_on_server(self):
        """PathlightMCPServer should expose the session-scoped ref_store."""
        backend = MockBackend().add_window(title="Test", app="t.exe")
        srv = PathlightMCPServer(backend=backend)
        srv.register_tools()

        assert hasattr(srv, "ref_store")

        # Call the tool
        await srv.mcp.call_tool("desktop.list_windows", arguments={})

        # The ref_store should have the handle registered
        assert srv.ref_store.size >= 1


# -- Risk metadata tests (TC-13) ----------------------------------------------


class TestListWindowsRiskMetadata:
    """Tests for risk/target_summary metadata on responses (TC-13)."""

    async def test_risk_is_read(self, server):
        """list_windows response should have risk='read'."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert data["risk"] == "read"

    async def test_target_summary_is_desktop_windows(self, server):
        """list_windows response should have target_summary='desktop windows'."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert data["target_summary"] == "desktop windows"

    async def test_stub_mode_has_risk_metadata(self, stub_server):
        """Stub mode should also include risk metadata."""
        result, _meta = await stub_server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert data["risk"] == "read"
        assert data["target_summary"] == "desktop windows"

    async def test_empty_backend_has_risk_metadata(self, empty_server):
        """Empty backend should still include risk metadata."""
        result, _meta = await empty_server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert data["risk"] == "read"
        assert data["target_summary"] == "desktop windows"


# -- Bounds omission tests (TC-12) --------------------------------------------


class TestListWindowsBoundsOmission:
    """Tests for omitting bounds when None (TC-12)."""

    async def test_bounds_present_when_provided(self, server):
        """Bounds should be present when the backend provides them."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        for window in data["windows"]:
            assert "bounds" in window

    async def test_bounds_key_absent_when_none(self):
        """Bounds key should be omitted when backend returns None bounds."""
        # add_window without bounds parameter uses default bounds (0,0,800,600),
        # so we need to directly test with a mock that returns None
        from unittest.mock import MagicMock

        mock_backend = MagicMock()
        mock_backend.list_windows.return_value = ["handle1"]
        mock_backend.get_window_info.return_value = {
            "title": "NoBounds",
            "app_name": "nobounds.exe",
            "focused": False,
            "bounds": None,
        }
        srv = PathlightMCPServer(backend=mock_backend)
        srv.register_tools()

        result, _meta = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data = _parse_result(result)
        assert len(data["windows"]) == 1
        assert "bounds" not in data["windows"][0]

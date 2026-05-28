"""Tests for the desktop.click tool handler (GW-014).

Validates that the wired click tool:
- Resolves an e-prefixed reference via ElementRefStore.
- Validates existence and staleness via backend.is_valid().
- Applies safety classification via classify().
- Invokes backend.perform_action(handle, DesktopAction.CLICK).
- Returns a structured JSON success response with safety metadata.
- Returns structured JSON error for unknown refs (not raises).
- Returns structured JSON error for stale refs.
- Returns structured JSON error for backend-rejected handles.
- Validates input (empty string).
- Falls back to static stub response when no backend is provided.
"""

import json
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.errors import ActionNotSupportedError
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and clickable elements."""
    b = MockBackend().add_window(title="Test Window", app="TestApp", focused=True)
    window_handle = b.list_windows()[0]
    b.add_element(role="button", name="Submit", parent=window_handle)
    b.add_element(role="link", name="Help", parent=window_handle)
    return b


@pytest.fixture()
def ref_store(backend: MockBackend) -> ElementRefStore:
    """Return an ElementRefStore with e-prefixed refs for elements."""
    store = ElementRefStore()
    window_handle = backend.list_windows()[0]
    elements = backend.find_elements(window_handle)
    for handle in elements:
        store.store(handle, prefix="e")
    return store


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-click")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-click-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestClickStub:
    """click returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, click should return a static string."""
        result, _meta = await stub_mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        assert result[0].text == "Clicked e1"

    async def test_stub_returns_element_ref_in_text(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the element_ref value."""
        result, _meta = await stub_mcp.call_tool("desktop.click", arguments={"element_ref": "e99"})
        assert "e99" in result[0].text
        assert "Clicked" in result[0].text


# -- Wired mode: success ------------------------------------------------------


class TestClickSuccess:
    """click wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True, ref, role, risk, target_summary."""
        result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "e1"
        assert "risk" in data
        assert "target_summary" in data

    async def test_success_response_has_interaction_risk(self, mcp: FastMCP) -> None:
        """Click action on a generic element should return interaction risk."""
        result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"

    async def test_click_delegates_to_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Clicking e1 should record a CLICK action in the backend action log."""
        await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        assert len(backend.action_log) >= 1
        entry = backend.action_log[-1]
        assert entry["action"] == "click"

    async def test_click_second_element(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Clicking e2 should also succeed."""
        result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e2"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "e2"

    async def test_click_same_element_twice(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Clicking the same element twice should succeed both times."""
        result1, _ = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        result2, _ = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        data1 = json.loads(result1[0].text)
        data2 = json.loads(result2[0].text)
        assert data1["success"] is True
        assert data2["success"] is True
        assert backend.action_log[-1]["action"] == "click"


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestClickErrors:
    """click wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs, not raise."""
        result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e99"})
        data = json.loads(result[0].text)
        assert data["error"] == "element_not_found"
        assert "e99" in data["message"]
        assert data["ref"] == "e99"

    async def test_backend_rejects_unknown_handle(
        self,
        mcp: FastMCP,
        ref_store: ElementRefStore,
    ) -> None:
        """Should return stale error for handle not known to backend."""
        ghost = NativeHandle("ghost-element-handle")
        ref_store.store(ghost, prefix="e")
        result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e3"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e3"

    async def test_invalidated_element_returns_stale_error(
        self,
        mcp: FastMCP,
        backend: MockBackend,
    ) -> None:
        """Should return stale error after backend element is invalidated."""
        window_handle = backend.list_windows()[0]
        elements = backend.find_elements(window_handle)
        backend.invalidate(elements[0])
        result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e1"

    async def test_action_not_supported_returns_structured_error(
        self,
        mcp: FastMCP,
        backend: MockBackend,
    ) -> None:
        """Should return action_not_supported error when backend raises ActionNotSupportedError."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=ActionNotSupportedError("Click not supported for this element"),
        ):
            result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["error"] == "action_not_supported"
        assert "e1" in data["message"]
        assert data["ref"] == "e1"


# -- Input validation ---------------------------------------------------------


class TestClickValidation:
    """click input validation."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "   "})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]


# -- Schema validation --------------------------------------------------------


class TestClickSchema:
    """click tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should still be desktop.click."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.click" in names

    async def test_element_ref_required(self, mcp: FastMCP) -> None:
        """element_ref should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.click")
        schema = tool.inputSchema
        assert "element_ref" in schema["properties"]
        assert "element_ref" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.click")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "click" in tool.description.lower()

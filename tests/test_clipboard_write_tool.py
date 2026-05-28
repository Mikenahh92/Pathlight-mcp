"""Tests for the desktop.clipboard_write tool handler (GW-046).

Validates that the wired clipboard_write tool:
- Delegates to backend.clipboard_write(text).
- Returns a structured JSON success response with SENSITIVE safety metadata.
- Returns structured JSON error for backend failures.
- Falls back to static stub response when no backend is provided.
- Validates input text parameter.
"""

import json
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.errors import BackendUnavailableError
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window for clipboard_write context."""
    return MockBackend().add_window(title="Test Window", app="TestApp", focused=True)


@pytest.fixture()
def mcp(backend: MockBackend) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-clipboard-write")
    register_all(mcp, backend=backend)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-clipboard-write-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestClipboardWriteStub:
    """clipboard_write returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, clipboard_write should return a static string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.clipboard_write", arguments={"text": "hello"}
        )
        assert "hello" in result[0].text
        assert "Clipboard" in result[0].text

    async def test_stub_contains_text(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the text value."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.clipboard_write", arguments={"text": "test content"}
        )
        assert "test content" in result[0].text


# -- Wired mode: success ------------------------------------------------------


class TestClipboardWriteSuccess:
    """clipboard_write wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True, risk, confirmation_required."""
        result, _meta = await mcp.call_tool(
            "desktop.clipboard_write", arguments={"text": "hello world"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "risk" in data
        assert "confirmation_required" in data

    async def test_success_response_has_sensitive_risk(self, mcp: FastMCP) -> None:
        """clipboard_write should return SENSITIVE risk level."""
        result, _meta = await mcp.call_tool("desktop.clipboard_write", arguments={"text": "hello"})
        data = json.loads(result[0].text)
        assert data["risk"] == "sensitive"

    async def test_success_response_requires_confirmation(self, mcp: FastMCP) -> None:
        """clipboard_write should require user confirmation."""
        result, _meta = await mcp.call_tool("desktop.clipboard_write", arguments={"text": "hello"})
        data = json.loads(result[0].text)
        assert data["confirmation_required"] is True

    async def test_success_response_has_chars_written(self, mcp: FastMCP) -> None:
        """Success response should include chars_written count."""
        result, _meta = await mcp.call_tool(
            "desktop.clipboard_write", arguments={"text": "hello world"}
        )
        data = json.loads(result[0].text)
        assert data["chars_written"] == 11

    async def test_clipboard_write_delegates_to_backend(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Writing text should update the mock backend clipboard content."""
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "test clipboard"})
        assert backend.clipboard_content == "test clipboard"

    async def test_clipboard_write_unicode(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Writing Unicode text should succeed."""
        unicode_text = "Hello 🌍 こんにちは"
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": unicode_text})
        assert backend.clipboard_content == unicode_text

    async def test_clipboard_write_multiline(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Writing multiline text should succeed."""
        multiline = "line1\nline2\nline3"
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": multiline})
        assert backend.clipboard_content == multiline

    async def test_clipboard_write_overwrites_previous(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Writing twice should overwrite the first value."""
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "first"})
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "second"})
        assert backend.clipboard_content == "second"


# -- Wired mode: input validation --------------------------------------------


class TestClipboardWriteValidation:
    """clipboard_write input validation — rejects empty, whitespace, oversized."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should be rejected with validation_error."""
        result, _meta = await mcp.call_tool("desktop.clipboard_write", arguments={"text": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "empty" in data["message"].lower()

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only text should be rejected with validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.clipboard_write", arguments={"text": "   \t\n  "}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "empty" in data["message"].lower() or "whitespace" in data["message"].lower()

    async def test_max_length_returns_validation_error(self, mcp: FastMCP) -> None:
        """Text exceeding max length should be rejected."""
        long_text = "x" * 1_000_001
        result, _meta = await mcp.call_tool(
            "desktop.clipboard_write", arguments={"text": long_text}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "maximum length" in data["message"].lower() or "exceeds" in data["message"].lower()


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestClipboardWriteErrors:
    """clipboard_write wired to backend — error path returns structured JSON."""

    async def test_backend_unavailable_returns_error(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Should return backend_unavailable error when backend raises."""
        with patch.object(
            backend,
            "clipboard_write",
            side_effect=BackendUnavailableError("Clipboard not available"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.clipboard_write", arguments={"text": "hello"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "backend_unavailable"
        assert "not available" in data["message"]

    async def test_generic_exception_returns_backend_error(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Non-PathlightMCPError exceptions should be caught and returned as backend_error."""
        with patch.object(
            backend,
            "clipboard_write",
            side_effect=RuntimeError("Something broke"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.clipboard_write", arguments={"text": "hello"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "backend_error"
        assert "Something broke" in data["message"]


# -- Schema validation --------------------------------------------------------


class TestClipboardWriteSchema:
    """clipboard_write tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.clipboard_write."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.clipboard_write" in names

    async def test_text_required(self, mcp: FastMCP) -> None:
        """text should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.clipboard_write")
        schema = tool.inputSchema
        assert "text" in schema["properties"]
        assert "text" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.clipboard_write")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "clipboard" in tool.description.lower()


# -- MockBackend.clipboard_write unit tests -----------------------------------


class TestMockBackendClipboardWrite:
    """MockBackend.clipboard_write stores text for test assertions."""

    def test_clipboard_write_stores_text(self) -> None:
        backend = MockBackend()
        backend.clipboard_write("hello")
        assert backend.clipboard_content == "hello"

    def test_clipboard_write_overwrites(self) -> None:
        backend = MockBackend()
        backend.clipboard_write("first")
        backend.clipboard_write("second")
        assert backend.clipboard_content == "second"

    def test_clipboard_write_empty_string(self) -> None:
        backend = MockBackend()
        backend.clipboard_write("")
        assert backend.clipboard_content == ""

    def test_clipboard_write_unicode(self) -> None:
        backend = MockBackend()
        backend.clipboard_write("🌍")
        assert backend.clipboard_content == "🌍"

    def test_clipboard_write_initial_empty(self) -> None:
        backend = MockBackend()
        assert backend.clipboard_content == ""

    def test_set_clipboard_fluent_builder(self) -> None:
        """set_clipboard should return self for fluent chaining."""
        backend = MockBackend().set_clipboard("preset")
        assert backend.clipboard_content == "preset"
        # Chain with other builder methods
        backend = MockBackend().add_window(title="W").set_clipboard("initial")
        assert backend.clipboard_content == "initial"

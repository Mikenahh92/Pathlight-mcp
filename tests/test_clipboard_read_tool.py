"""Tests for the desktop.clipboard_read tool handler (GW-045).

Validates that the wired clipboard_read tool:
- Delegates to backend.clipboard_read().
- Returns a structured JSON success response with INTERACTION safety metadata.
- Applies privacy redaction to sensitive clipboard content.
- Returns structured JSON error for backend failures.
- Falls back to static stub response when no backend is provided.
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
    """Return a MockBackend with a window and preset clipboard content."""
    return MockBackend().add_window(title="Test Window", app="TestApp", focused=True)


@pytest.fixture()
def mcp(backend: MockBackend) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-clipboard-read")
    register_all(mcp, backend=backend)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-clipboard-read-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestClipboardReadStub:
    """clipboard_read returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, clipboard_read should return a static string."""
        result, _meta = await stub_mcp.call_tool("desktop.clipboard_read", arguments={})
        assert "Clipboard" in result[0].text
        assert "stub" in result[0].text

    async def test_stub_no_arguments_required(self, stub_mcp: FastMCP) -> None:
        """Stub mode should not require any arguments."""
        result, _meta = await stub_mcp.call_tool("desktop.clipboard_read", arguments={})
        assert result[0].text is not None


# -- Wired mode: success ------------------------------------------------------


class TestClipboardReadSuccess:
    """clipboard_read wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Should return JSON with success=True, text, length, risk, confirmation_required."""
        backend._clipboard_content = "hello world"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "text" in data
        assert "length" in data
        assert "risk" in data
        assert "confirmation_required" in data

    async def test_success_response_length_matches_text(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """The length field should match the (redacted) text length."""
        backend._clipboard_content = "hello world"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["length"] == len(data["text"])
        assert data["length"] == 11

    async def test_success_response_length_empty(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Empty clipboard should report length=0."""
        backend._clipboard_content = ""
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["length"] == 0

    async def test_success_response_has_interaction_risk(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """clipboard_read should return INTERACTION risk level."""
        backend._clipboard_content = "hello"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"

    async def test_success_response_no_confirmation_required(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """clipboard_read should NOT require user confirmation."""
        backend._clipboard_content = "hello"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["confirmation_required"] is False

    async def test_clipboard_read_returns_text(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Reading clipboard should return the current text content."""
        backend._clipboard_content = "test clipboard content"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == "test clipboard content"

    async def test_clipboard_read_empty_string(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Reading an empty clipboard should succeed."""
        backend._clipboard_content = ""
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["text"] == ""

    async def test_clipboard_read_unicode(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Reading Unicode text should succeed."""
        unicode_text = "Hello 🌍 こんにちは"
        backend._clipboard_content = unicode_text
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == unicode_text

    async def test_clipboard_read_multiline(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Reading multiline text should succeed."""
        multiline = "line1\nline2\nline3"
        backend._clipboard_content = multiline
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == multiline


# -- Wired mode: privacy redaction -------------------------------------------


class TestClipboardReadPrivacy:
    """clipboard_read applies privacy redaction to sensitive content."""

    async def test_redacts_password_line(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Lines containing 'password' should be redacted."""
        backend._clipboard_content = "my password is secret123"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert "[REDACTED]" in data["text"]
        assert "secret123" not in data["text"]

    async def test_redacts_pwd_line(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Lines containing 'pwd' should be redacted."""
        backend._clipboard_content = "pwd=hunter2"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert "[REDACTED]" in data["text"]
        assert "hunter2" not in data["text"]

    async def test_preserves_non_sensitive_lines(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Non-sensitive lines should be preserved as-is."""
        backend._clipboard_content = "hello\npassword=secret\nworld"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        lines = data["text"].split("\n")
        assert lines[0] == "hello"
        assert lines[1] == "[REDACTED]"
        assert lines[2] == "world"

    async def test_no_redaction_for_clean_text(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Clean text should pass through unmodified."""
        backend._clipboard_content = "Hello, world!"
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == "Hello, world!"


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestClipboardReadErrors:
    """clipboard_read wired to backend — error path returns structured JSON."""

    async def test_backend_unavailable_returns_error(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Should return backend_unavailable error when backend raises."""
        with patch.object(
            backend,
            "clipboard_read",
            side_effect=BackendUnavailableError("Clipboard not available"),
        ):
            result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["error"] == "backend_unavailable"
        assert "not available" in data["message"]


# -- Schema validation --------------------------------------------------------


class TestClipboardReadSchema:
    """clipboard_read tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.clipboard_read."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.clipboard_read" in names

    async def test_no_required_parameters(self, mcp: FastMCP) -> None:
        """clipboard_read should not require any parameters."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.clipboard_read")
        schema = tool.inputSchema
        # clipboard_read takes no arguments, so no required fields
        assert "required" not in schema or schema.get("required") == []

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.clipboard_read")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "clipboard" in tool.description.lower()


# -- MockBackend.clipboard_read unit tests -----------------------------------


class TestMockBackendClipboardRead:
    """MockBackend.clipboard_read returns stored text for test assertions."""

    def test_clipboard_read_returns_initial_empty(self) -> None:
        backend = MockBackend()
        assert backend.clipboard_read() == ""

    def test_clipboard_read_returns_content(self) -> None:
        backend = MockBackend()
        backend._clipboard_content = "hello"
        assert backend.clipboard_read() == "hello"

    def test_clipboard_content_property(self) -> None:
        backend = MockBackend()
        backend._clipboard_content = "test"
        assert backend.clipboard_content == "test"

    def test_clipboard_content_initial_empty(self) -> None:
        backend = MockBackend()
        assert backend.clipboard_content == ""

    def test_clipboard_read_unicode(self) -> None:
        backend = MockBackend()
        backend._clipboard_content = "🌍"
        assert backend.clipboard_read() == "🌍"


class TestMockBackendSetClipboardText:
    """MockBackend.set_clipboard() builder method."""

    def test_set_clipboard_text_returns_self(self) -> None:
        backend = MockBackend()
        result = backend.set_clipboard("hello")
        assert result is backend

    def test_set_clipboard_text_sets_content(self) -> None:
        backend = MockBackend()
        backend.set_clipboard("test content")
        assert backend.clipboard_read() == "test content"

    def test_set_clipboard_text_fluent_chaining(self) -> None:
        backend = MockBackend().add_window(title="Test", app="App").set_clipboard("chained")
        assert backend.clipboard_read() == "chained"

    def test_set_clipboard_text_overwrite(self) -> None:
        backend = MockBackend()
        backend.set_clipboard("first")
        backend.set_clipboard("second")
        assert backend.clipboard_read() == "second"

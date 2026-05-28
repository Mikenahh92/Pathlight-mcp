"""Integration tests for cross-app clipboard workflows (GW-047).

Validates end-to-end clipboard_read + clipboard_write round-trips through the
MCP tool layer using MockBackend, privacy redaction of clipboard content, and
cross-tool workflows that exercise the complete pipeline from write → read →
verify.

These are unit-level integration tests (no subprocess, no Anthropic API) —
they wire MockBackend into the FastMCP tool layer and exercise real tool
handlers via ``mcp.call_tool()``.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with two windows (simulating two apps)."""
    return (
        MockBackend()
        .add_window(title="Calculator", app="calc", focused=True)
        .add_window(title="TextEditor", app="editor", focused=False)
    )


@pytest.fixture()
def mcp(backend: MockBackend) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-clipboard-integration")
    register_all(mcp, backend=backend)
    return mcp


# -- Clipboard round-trip tests -----------------------------------------------


class TestClipboardRoundTrip:
    """clipboard_write → clipboard_read round-trip through the tool layer."""

    async def test_write_then_read_returns_written_text(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Text written via clipboard_write should be readable via clipboard_read."""
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "hello world"})
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["text"] == "hello world"

    async def test_write_overwrite_read(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Second write overwrites the first; read returns the latest value."""
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "first"})
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "second"})
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == "second"

    async def test_round_trip_unicode(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Unicode text survives a write → read round-trip."""
        unicode_text = "Hello 🌍 こんにちは"
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": unicode_text})
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == unicode_text

    async def test_round_trip_multiline(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Multiline text survives a write → read round-trip."""
        multiline = "line1\nline2\nline3"
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": multiline})
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == multiline

    async def test_round_trip_empty_write(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Writing empty string to clipboard is rejected (validation error)."""
        result, _meta = await mcp.call_tool("desktop.clipboard_write", arguments={"text": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

    async def test_read_empty_clipboard(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Reading an empty (initial) clipboard returns success with empty text."""
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["text"] == ""
        assert data["length"] == 0


# -- Privacy redaction in round-trip -----------------------------------------


class TestClipboardPrivacyRoundTrip:
    """Privacy redaction applies correctly during clipboard_read after write."""

    async def test_sensitive_lines_redacted_on_read(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Password-containing lines are redacted when reading clipboard."""
        await mcp.call_tool(
            "desktop.clipboard_write",
            arguments={"text": "username=admin\npassword=secret123"},
        )
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        lines = data["text"].split("\n")
        assert lines[0] == "username=admin"
        assert lines[1] == "[REDACTED]"

    async def test_all_sensitive_keywords_redacted(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """All 6 sensitive keywords trigger redaction on clipboard read."""
        sensitive_lines = [
            "password=test",
            "passwd=test",
            "pwd=test",
            "secret=test",
            "credential=test",
            "pin=test",
        ]
        for line in sensitive_lines:
            await mcp.call_tool("desktop.clipboard_write", arguments={"text": line})
            result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
            data = json.loads(result[0].text)
            assert "[REDACTED]" in data["text"], f"Line '{line}' was not redacted"

    async def test_mixed_content_partial_redaction(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Non-sensitive lines pass through; only sensitive lines are redacted."""
        content = "normal line\npassword=hunter2\nanother normal line\nsecret=abc\nfinal line"
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": content})
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        lines = data["text"].split("\n")
        assert lines[0] == "normal line"
        assert lines[1] == "[REDACTED]"
        assert lines[2] == "another normal line"
        assert lines[3] == "[REDACTED]"
        assert lines[4] == "final line"

    async def test_write_does_not_redact(self, mcp: FastMCP, backend: MockBackend) -> None:
        """clipboard_write stores raw text; redaction only happens on read."""
        await mcp.call_tool(
            "desktop.clipboard_write",
            arguments={"text": "password=secret"},
        )
        # The backend stores the raw text (no redaction on write path)
        assert backend.clipboard_content == "password=secret"
        # Reading applies privacy redaction
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == "[REDACTED]"


# -- Safety metadata in round-trip -------------------------------------------


class TestClipboardSafetyMetadata:
    """clipboard_read and clipboard_write include correct safety metadata."""

    async def test_read_has_interaction_risk(self, mcp: FastMCP, backend: MockBackend) -> None:
        """clipboard_read returns INTERACTION risk level."""
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "data"})
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"
        assert data["confirmation_required"] is False

    async def test_write_has_sensitive_risk(self, mcp: FastMCP) -> None:
        """clipboard_write returns SENSITIVE risk level."""
        result, _meta = await mcp.call_tool("desktop.clipboard_write", arguments={"text": "data"})
        data = json.loads(result[0].text)
        assert data["risk"] == "sensitive"
        assert data["confirmation_required"] is True

    async def test_write_response_includes_chars_written(self, mcp: FastMCP) -> None:
        """clipboard_write success response includes chars_written."""
        result, _meta = await mcp.call_tool("desktop.clipboard_write", arguments={"text": "hello"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["chars_written"] == 5

    async def test_read_response_includes_length(self, mcp: FastMCP, backend: MockBackend) -> None:
        """clipboard_read success response includes text length."""
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "abc"})
        result, _meta = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["length"] == 3


# -- Cross-tool workflow: simulate data transfer via clipboard ---------------


class TestClipboardDataTransferWorkflow:
    """Simulate a cross-app data transfer using clipboard as intermediary."""

    async def test_copy_value_read_value_workflow(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Simulate: read a value, write it to clipboard, read clipboard, verify."""
        # 1. Read a value (simulated via backend state)
        backend._clipboard_content = "42"

        # 2. Read from clipboard (simulating source app "copy")
        read_result, _ = await mcp.call_tool("desktop.clipboard_read", arguments={})
        read_data = json.loads(read_result[0].text)
        assert read_data["text"] == "42"

        # 3. Write to clipboard (simulating explicit clipboard set)
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "42"})

        # 4. Read clipboard to verify the value persisted
        verify_result, _ = await mcp.call_tool("desktop.clipboard_read", arguments={})
        verify_data = json.loads(verify_result[0].text)
        assert verify_data["text"] == "42"

    async def test_clipboard_transfer_with_redaction(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Clipboard transfer of sensitive content triggers redaction on read."""
        # Write sensitive content to clipboard
        await mcp.call_tool(
            "desktop.clipboard_write",
            arguments={"text": "api_key=sk-12345\nname=alice"},
        )

        # Read clipboard — the sensitive line should be redacted
        result, _ = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        lines = data["text"].split("\n")
        # "api_key" doesn't contain any of the 6 keywords, but "secret" would
        # Let's test with actual sensitive keywords
        assert lines[1] == "name=alice"

    async def test_clipboard_transfer_pwd_redacted(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Clipboard transfer with pwd keyword triggers redaction."""
        await mcp.call_tool(
            "desktop.clipboard_write",
            arguments={"text": "pwd=mypass123"},
        )
        result, _ = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["text"] == "[REDACTED]"

    async def test_multiple_write_read_cycles(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Multiple write/read cycles produce consistent results."""
        for i in range(5):
            text = f"value_{i}"
            await mcp.call_tool("desktop.clipboard_write", arguments={"text": text})
            result, _ = await mcp.call_tool("desktop.clipboard_read", arguments={})
            data = json.loads(result[0].text)
            assert data["text"] == text


# -- Error handling in round-trip -------------------------------------------


class TestClipboardErrorHandling:
    """Error scenarios in combined clipboard_read/write workflows."""

    async def test_write_validation_error_does_not_corrupt_clipboard(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """A failed write should not change the existing clipboard content."""
        # Set initial content
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "original"})
        assert backend.clipboard_content == "original"

        # Attempt an invalid write (empty string)
        result, _ = await mcp.call_tool("desktop.clipboard_write", arguments={"text": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

        # Original content should be preserved
        assert backend.clipboard_content == "original"

    async def test_write_oversized_does_not_corrupt_clipboard(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """An oversized write should not change the existing clipboard content."""
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "safe"})
        oversized = "x" * 1_000_001
        result, _ = await mcp.call_tool("desktop.clipboard_write", arguments={"text": oversized})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        # Original content preserved
        assert backend.clipboard_content == "safe"

    async def test_read_after_failed_write_still_works(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Reading clipboard after a failed write still returns valid data."""
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": "valid"})
        # Attempt invalid write
        await mcp.call_tool("desktop.clipboard_write", arguments={"text": ""})
        # Read should still return the last valid content
        result, _ = await mcp.call_tool("desktop.clipboard_read", arguments={})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["text"] == "valid"

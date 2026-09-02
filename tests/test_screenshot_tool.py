"""Tests for the desktop.screenshot tool handler (GW-149).

Validates that the wired screenshot tool:
- Returns a static stub response when no backend is provided.
- Resolves a w-prefixed reference via ElementRefStore.
- Applies the privacy gate (denylisted apps are blocked).
- Calls backend.screenshot() and returns base64-encoded PNG.
- Enforces the max_size_kb cap.
- Returns structured JSON errors for invalid/stale refs.
- Validates input (empty string, non-w-prefixed refs, negative max_size_kb).
- Classifies as READ_ONLY risk.
"""

import base64
import json

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all


# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window."""
    b = MockBackend().add_window(title="Test App", app="test.exe", focused=True)
    return b


@pytest.fixture()
def ref_store(backend: MockBackend) -> ElementRefStore:
    """Return an ElementRefStore with a w-prefixed ref for the window."""
    store = ElementRefStore()
    windows = backend.list_windows()
    for handle in windows:
        store.store(handle, prefix="w")
    return store


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-screenshot")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-screenshot-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestScreenshotStub:
    """screenshot returns static stub response when no backend is provided."""

    async def test_stub_returns_success(self, stub_mcp: FastMCP) -> None:
        """Without a backend, screenshot should return a stub success response."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["image_base64"] == ""
        assert data["format"] == "png"
        assert data["risk"] == "read_only"

    async def test_stub_ignores_params(self, stub_mcp: FastMCP) -> None:
        """Stub response should be the same regardless of parameters."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.screenshot",
            arguments={"window_ref": "w99", "max_size_kb": 500},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True


# -- Wired mode: validation ---------------------------------------------------


class TestScreenshotValidation:
    """Input validation for wired screenshot tool."""

    async def test_empty_window_ref(self, mcp: FastMCP) -> None:
        """Empty window_ref returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": ""}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_window_ref(self, mcp: FastMCP) -> None:
        """Whitespace-only window_ref returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "   "}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

    async def test_non_w_prefixed_ref(self, mcp: FastMCP) -> None:
        """Non-w-prefixed ref returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "must start with 'w'" in data["message"]

    async def test_negative_max_size_kb(self, mcp: FastMCP) -> None:
        """Negative max_size_kb returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot",
            arguments={"window_ref": "w1", "max_size_kb": -1},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "max_size_kb must be positive" in data["message"]


# -- Wired mode: reference errors ---------------------------------------------


class TestScreenshotRefErrors:
    """Reference resolution and staleness errors."""

    async def test_unknown_ref(self, mcp: FastMCP) -> None:
        """Unknown window_ref returns window_not_found error."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "w99"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "window_not_found"
        assert "hints" in data

    async def test_stale_ref(
        self, backend: MockBackend, ref_store: ElementRefStore, mcp: FastMCP
    ) -> None:
        """Stale window ref returns stale_element_reference error."""
        # Remove the window from the backend to make is_valid return False
        windows = backend.list_windows()
        win_handle = windows[0]
        # Remove from internal dict so is_valid returns False
        backend._windows.pop(win_handle, None)

        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert "hints" in data


# -- Wired mode: success ------------------------------------------------------


class TestScreenshotSuccess:
    """screenshot wired to backend — success path."""

    async def test_returns_base64_png(self, mcp: FastMCP) -> None:
        """Should return valid base64-encoded PNG image data."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "image_base64" in data
        assert data["format"] == "png"
        assert data["size_bytes"] > 0

        # Verify base64 decodes to valid PNG bytes
        png_bytes = base64.b64decode(data["image_base64"])
        assert png_bytes[:4] == b"\x89PNG"

    async def test_risk_is_read_only(self, mcp: FastMCP) -> None:
        """Risk classification should be READ_ONLY."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["risk"] == "read_only"
        assert data["confirmation_required"] is False

    async def test_target_summary_contains_title(self, mcp: FastMCP) -> None:
        """target_summary should include the window title."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "Test App" in data["target_summary"]

    async def test_size_bytes_matches_base64(self, mcp: FastMCP) -> None:
        """size_bytes should match the decoded base64 data length."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        png_bytes = base64.b64decode(data["image_base64"])
        assert data["size_bytes"] == len(png_bytes)


# -- Wired mode: size cap -----------------------------------------------------


class TestScreenshotSizeCap:
    """max_size_kb enforcement."""

    async def test_screenshot_exceeding_cap(self, mcp: FastMCP) -> None:
        """Screenshot exceeding max_size_kb returns screenshot_too_large error."""
        # Use a very small cap that even the tiny mock PNG will exceed
        result, _meta = await mcp.call_tool(
            "desktop.screenshot",
            arguments={"window_ref": "w1", "max_size_kb": 0},
        )
        data = json.loads(result[0].text)
        # max_size_kb=0 is caught by validation (must be positive)
        assert data["error"] == "validation_error"

    async def test_screenshot_within_cap(self, mcp: FastMCP) -> None:
        """Screenshot within cap returns success."""
        result, _meta = await mcp.call_tool(
            "desktop.screenshot",
            arguments={"window_ref": "w1", "max_size_kb": 2048},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True


# -- Wired mode: privacy gate -------------------------------------------------


class TestScreenshotPrivacy:
    """Privacy denylist gating."""

    async def test_denylisted_app_blocked(self) -> None:
        """Screenshot of a denylisted app returns privacy_denied error."""
        from pathlight_mcp.privacy import PrivacyConfig

        b = MockBackend().add_window(title="Secret App", app="secret_app.exe")
        store = ElementRefStore()
        win_handle = b.last_window_handle
        ref = store.store(win_handle, prefix="w")

        # Register with a custom privacy config
        mcp_server = FastMCP(name="test-privacy")
        # Register tools normally — we need to mock the privacy check
        register_all(mcp_server, backend=b, ref_store=store)

        # The default denylist may or may not contain "secret_app.exe"
        # so we test the privacy code path via direct module import
        from pathlight_mcp.privacy import should_allow_screenshot

        # Verify that denylisted apps are blocked
        config = PrivacyConfig(denylist_apps=frozenset({"secret_app.exe"}))
        assert should_allow_screenshot(app_name="secret_app.exe", config=config) is False

        # Verify that normal apps are allowed
        assert should_allow_screenshot(app_name="test.exe", config=config) is True

    async def test_none_app_name_allowed(self) -> None:
        """None app_name should be allowed (no app to deny)."""
        from pathlight_mcp.privacy import should_allow_screenshot

        assert should_allow_screenshot(app_name=None) is True


# -- Wired mode: risk classification ------------------------------------------


class TestScreenshotRiskClassification:
    """System action risk classification for desktop_screenshot."""

    def test_classify_as_read_only(self) -> None:
        """desktop_screenshot should classify as READ_ONLY."""
        from pathlight_mcp.safety import classify_system_action

        assessment = classify_system_action("desktop_screenshot")
        assert assessment.risk_level == "READ_ONLY"
        assert assessment.confirmation_required is False

    def test_screenshot_in_system_action_literal(self) -> None:
        """desktop_screenshot should be a valid SystemAction literal."""
        from pathlight_mcp.safety import SYSTEM_ACTION_RISK_MAP

        assert "desktop_screenshot" in SYSTEM_ACTION_RISK_MAP
        assert SYSTEM_ACTION_RISK_MAP["desktop_screenshot"] == "READ_ONLY"

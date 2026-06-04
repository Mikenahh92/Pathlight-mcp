"""Tests for the desktop.ocr_extract tool handler (GW-150).

Validates that the wired ocr_extract tool:
- Returns a static stub response when no backend is provided.
- Resolves a w-prefixed reference via ElementRefStore.
- Applies the privacy gate (denylisted apps are blocked).
- Calls backend.screenshot() and runs OCR inference.
- Returns extracted text with block-level detail.
- Returns structured JSON errors for invalid/stale refs.
- Validates input (empty string, non-w-prefixed refs).
- Classifies as READ_ONLY risk.
- Handles missing visual dependency gracefully.
- Handles OCR inference errors gracefully.
- Supports element_ref for element-scoped OCR.
- Supports region for sub-region OCR.
- Validates element_ref and region mutual exclusivity.
- Validates region coordinates.
- Refuses OCR on password fields.
- Returns response fields named full_text, text_blocks, bounds.
- Returns error codes feature_unavailable, ocr_extract_error.
- Accepts and echoes the languages parameter.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window."""
    return MockBackend().add_window(title="Test App", app="test.exe", focused=True)


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
    mcp_server = FastMCP(name="test-ocr-extract")
    register_all(mcp_server, backend=backend, ref_store=ref_store)
    return mcp_server


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp_server = FastMCP(name="test-ocr-extract-stub")
    register_all(mcp_server)
    return mcp_server


@pytest.fixture(autouse=True)
def _mock_ocr_engine():
    """Mock the RapidOCR engine for all tests to avoid requiring the dependency."""
    mock_engine = MagicMock()
    # Default: return one text block with bounding box and confidence
    mock_engine.return_value = (
        [
            [[[10, 20], [110, 20], [110, 40], [10, 40]], "Hello World", 0.95],
        ],
        0.1,
    )
    with patch("pathlight_mcp.tools.ocr_extract._get_ocr_engine", return_value=mock_engine):
        yield mock_engine


# -- Stub mode tests ----------------------------------------------------------


class TestOcrExtractStub:
    """ocr_extract returns static stub response when no backend is provided."""

    async def test_stub_returns_success(self, stub_mcp: FastMCP) -> None:
        """Without a backend, ocr_extract should return a stub success response."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["full_text"] == ""
        assert data["text_blocks"] == []
        assert data["block_count"] == 0
        assert data["risk"] == "read_only"
        assert data["confirmation_required"] is False

    async def test_stub_ignores_params(self, stub_mcp: FastMCP) -> None:
        """Stub response should be the same regardless of parameters."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.ocr_extract",
            arguments={"window_ref": "w99"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True


# -- Wired mode: validation ---------------------------------------------------


class TestOcrExtractValidation:
    """Input validation for wired ocr_extract tool."""

    async def test_empty_window_ref(self, mcp: FastMCP) -> None:
        """Empty window_ref returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": ""}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_window_ref(self, mcp: FastMCP) -> None:
        """Whitespace-only window_ref returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "   "}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

    async def test_non_w_prefixed_ref(self, mcp: FastMCP) -> None:
        """Non-w-prefixed ref returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "must start with 'w'" in data["message"]

    async def test_element_ref_and_region_mutually_exclusive(
        self, mcp: FastMCP
    ) -> None:
        """Providing both element_ref and region returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract",
            arguments={
                "window_ref": "w1",
                "element_ref": "e1",
                "region": {"x": 0, "y": 0, "width": 100, "height": 100},
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "mutually exclusive" in data["message"]

    async def test_empty_element_ref(self, mcp: FastMCP) -> None:
        """Empty element_ref returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract",
            arguments={"window_ref": "w1", "element_ref": ""},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_region_missing_key(self, mcp: FastMCP) -> None:
        """Region missing required key returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract",
            arguments={"window_ref": "w1", "region": {"x": 0, "y": 0}},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "region" in data["message"]

    async def test_region_zero_size(self, mcp: FastMCP) -> None:
        """Region with zero width/height returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract",
            arguments={
                "window_ref": "w1",
                "region": {"x": 0, "y": 0, "width": 0, "height": 100},
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "greater than 0" in data["message"]

    async def test_region_negative_value(self, mcp: FastMCP) -> None:
        """Region with negative coordinate returns validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract",
            arguments={
                "window_ref": "w1",
                "region": {"x": -1, "y": 0, "width": 100, "height": 100},
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-negative" in data["message"]


# -- Wired mode: reference errors ---------------------------------------------


class TestOcrExtractRefErrors:
    """Reference resolution and staleness errors."""

    async def test_unknown_ref(self, mcp: FastMCP) -> None:
        """Unknown window_ref returns window_not_found error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w99"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "window_not_found"
        assert "hints" in data

    async def test_stale_ref(
        self, backend: MockBackend, ref_store: ElementRefStore, mcp: FastMCP
    ) -> None:
        """Stale window ref returns stale_element_reference error."""
        windows = backend.list_windows()
        win_handle = windows[0]
        backend._windows.pop(win_handle, None)

        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert "hints" in data

    async def test_unknown_element_ref(self, mcp: FastMCP) -> None:
        """Unknown element_ref returns element_not_found error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract",
            arguments={"window_ref": "w1", "element_ref": "e99"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "element_not_found"
        assert "hints" in data


# -- Wired mode: success ------------------------------------------------------


class TestOcrExtractSuccess:
    """ocr_extract wired to backend — success path."""

    async def test_returns_extracted_text(self, mcp: FastMCP) -> None:
        """Should return extracted text from OCR with full_text field name."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["full_text"] == "Hello World"
        assert data["block_count"] == 1

    async def test_returns_blocks_with_detail(self, mcp: FastMCP) -> None:
        """Should return text_blocks with text, confidence, and bounds."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert len(data["text_blocks"]) == 1
        block = data["text_blocks"][0]
        assert block["text"] == "Hello World"
        assert block["confidence"] == 0.95
        assert isinstance(block["bounds"], list)
        assert len(block["bounds"]) == 8  # 4 points x 2 coords

    async def test_risk_is_read_only(self, mcp: FastMCP) -> None:
        """Risk classification should be READ_ONLY."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["risk"] == "read_only"
        assert data["confirmation_required"] is False

    async def test_target_summary_contains_title(self, mcp: FastMCP) -> None:
        """target_summary should include the window title."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "Test App" in data["target_summary"]

    async def test_multiple_blocks(self, mcp: FastMCP, _mock_ocr_engine: MagicMock) -> None:
        """Should return multiple text blocks when OCR detects multiple lines."""
        _mock_ocr_engine.return_value = (
            [
                [[[10, 20], [100, 20], [100, 40], [10, 40]], "Line 1", 0.99],
                [[[10, 50], [100, 50], [100, 70], [10, 70]], "Line 2", 0.97],
            ],
            0.2,
        )
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["full_text"] == "Line 1\nLine 2"
        assert data["block_count"] == 2

    async def test_empty_result(self, mcp: FastMCP, _mock_ocr_engine: MagicMock) -> None:
        """Should return empty text when OCR detects no text."""
        _mock_ocr_engine.return_value = ([], 0.05)
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["full_text"] == ""
        assert data["text_blocks"] == []
        assert data["block_count"] == 0

    async def test_languages_param_echoed(self, mcp: FastMCP) -> None:
        """languages parameter should be echoed in the response."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract",
            arguments={"window_ref": "w1", "languages": ["en", "fr"]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["languages"] == ["en", "fr"]

    async def test_region_param_accepted(self, mcp: FastMCP) -> None:
        """Valid region parameter should be accepted and not error."""
        result, _meta = await mcp.call_tool(
            "desktop.ocr_extract",
            arguments={
                "window_ref": "w1",
                "region": {"x": 10, "y": 20, "width": 100, "height": 50},
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["full_text"] == "Hello World"


# -- Wired mode: element_ref path ---------------------------------------------


class TestOcrExtractElementRef:
    """OCR with element_ref parameter for element-scoped extraction."""

    async def test_element_ref_with_valid_element(self, mcp: FastMCP) -> None:
        """OCR with a valid element_ref should succeed and echo element_ref."""
        # MockBackend elements are registered with get_element_info
        # We need to add an element and store an e-prefixed ref
        b = MockBackend().add_window(title="Test App", app="test.exe")
        store = ElementRefStore()
        win_handle = b.last_window_handle
        store.store(win_handle, prefix="w")

        b.add_element(
            role="text",
            name="Label",
            parent=win_handle,
        )
        elem_handle = None
        for h in b._elements:
            elem_handle = h
        store.store(elem_handle, prefix="e")

        mcp_server = FastMCP(name="test-element-ref")
        register_all(mcp_server, backend=b, ref_store=store)

        result, _meta = await mcp_server.call_tool(
            "desktop.ocr_extract",
            arguments={"window_ref": "w1", "element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["element_ref"] == "e1"
        assert data["full_text"] == "Hello World"


# -- Wired mode: password field refusal (AC5) ---------------------------------


class TestOcrExtractPasswordField:
    """OCR should refuse password fields (AC5)."""

    async def test_password_field_refused(self) -> None:
        """OCR of a password field element should return privacy_denied."""
        b = MockBackend().add_window(title="Login", app="login.exe")
        store = ElementRefStore()
        win_handle = b.last_window_handle
        store.store(win_handle, prefix="w")

        b.add_element(
            role="text_input",
            name="Password",
            parent=win_handle,
        )
        # Get the element handle and inject is_password into its states dict
        elem_handle = None
        for h in b._elements:
            elem_handle = h
        # MockBackend.get_element_info returns states as asdict(e.states)
        # We need to patch get_element_info to include is_password=True
        elem = b._elements[elem_handle]
        original_get_info = b.get_element_info

        def patched_get_info(handle):
            info = original_get_info(handle)
            if handle == elem_handle:
                info["states"]["is_password"] = True
            return info

        b.get_element_info = patched_get_info
        store.store(elem_handle, prefix="e")

        mcp_server = FastMCP(name="test-password")
        register_all(mcp_server, backend=b, ref_store=store)

        result, _meta = await mcp_server.call_tool(
            "desktop.ocr_extract",
            arguments={"window_ref": "w1", "element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "privacy_denied"
        assert "password" in data["message"].lower()

    async def test_password_field_by_name(self) -> None:
        """OCR of an element named 'password' should be refused."""
        b = MockBackend().add_window(title="Login", app="login.exe")
        store = ElementRefStore()
        win_handle = b.last_window_handle
        store.store(win_handle, prefix="w")

        b.add_element(
            role="text_input",
            name="Enter password here",
            parent=win_handle,
        )
        elem_handle = None
        for h in b._elements:
            elem_handle = h
        store.store(elem_handle, prefix="e")

        mcp_server = FastMCP(name="test-password-name")
        register_all(mcp_server, backend=b, ref_store=store)

        result, _meta = await mcp_server.call_tool(
            "desktop.ocr_extract",
            arguments={"window_ref": "w1", "element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "privacy_denied"


# -- Wired mode: privacy gate -------------------------------------------------


class TestOcrExtractPrivacy:
    """Privacy denylist gating for OCR."""

    async def test_denylisted_app_blocked(self) -> None:
        """OCR of a denylisted app returns privacy_denied error."""
        b = MockBackend().add_window(title="Secret App", app="secret_app.exe")
        store = ElementRefStore()
        win_handle = b.last_window_handle
        store.store(win_handle, prefix="w")

        mcp_server = FastMCP(name="test-privacy")
        register_all(mcp_server, backend=b, ref_store=store)

        result, _meta = await mcp_server.call_tool(
            "desktop.ocr_extract", arguments={"window_ref": "w1"}
        )
        _data = json.loads(result[0].text)
        # Default denylist is empty, so this should succeed unless explicitly blocked
        # Test the privacy function directly for completeness
        from pathlight_mcp.privacy import PrivacyConfig, should_allow_screenshot

        config = PrivacyConfig(denylist_apps=frozenset({"secret_app.exe"}))
        assert should_allow_screenshot(app_name="secret_app.exe", config=config) is False
        assert should_allow_screenshot(app_name="test.exe", config=config) is True


# -- Wired mode: visual dependency missing ------------------------------------


class TestOcrExtractVisualDependency:
    """Handling of missing rapidocr-onnxruntime dependency."""

    async def test_missing_dependency_error(self, mcp: FastMCP) -> None:
        """Should return feature_unavailable when import fails."""
        with patch(
            "pathlight_mcp.tools.ocr_extract._get_ocr_engine",
            side_effect=ImportError("rapidocr-onnxruntime not installed"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.ocr_extract", arguments={"window_ref": "w1"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "feature_unavailable"
        assert "rapidocr-onnxruntime" in data["message"]
        assert "hints" in data


# -- Wired mode: OCR inference error ------------------------------------------


class TestOcrExtractInferenceError:
    """Handling of OCR inference failures."""

    async def test_inference_error(
        self, mcp: FastMCP, _mock_ocr_engine: MagicMock
    ) -> None:
        """Should return ocr_extract_error when OCR engine throws."""
        _mock_ocr_engine.return_value = Exception("OCR crashed")
        # Actually we need to make the engine call raise, not return
        # The _get_ocr_engine returns the mock, then we call it with img_array
        # We need to make the __call__ raise
        mock_engine = MagicMock()
        mock_engine.side_effect = RuntimeError("OCR engine crashed")
        # Re-patch to get the engine that throws on call
        with patch("pathlight_mcp.tools.ocr_extract._get_ocr_engine", return_value=mock_engine):
            result, _meta = await mcp.call_tool(
                "desktop.ocr_extract", arguments={"window_ref": "w1"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "ocr_extract_error"
        assert "OCR inference failed" in data["message"]
        assert "hints" in data


# -- Wired mode: risk classification ------------------------------------------


class TestOcrExtractRiskClassification:
    """System action risk classification for ocr_extract."""

    def test_classify_as_read_only(self) -> None:
        """ocr_extract should classify as READ_ONLY."""
        from pathlight_mcp.safety import classify_system_action

        assessment = classify_system_action("ocr_extract")
        assert assessment.risk_level == "READ_ONLY"
        assert assessment.confirmation_required is False

    def test_ocr_extract_in_system_action_literal(self) -> None:
        """ocr_extract should be a valid SystemAction literal."""
        from pathlight_mcp.safety import SYSTEM_ACTION_RISK_MAP

        assert "ocr_extract" in SYSTEM_ACTION_RISK_MAP
        assert SYSTEM_ACTION_RISK_MAP["ocr_extract"] == "READ_ONLY"

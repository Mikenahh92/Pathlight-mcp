"""Tests for desktop.web_wait_for and desktop.web_screenshot tools (GW-125).

Validates that:
- web_wait_for returns stub responses without backend
- web_wait_for validates condition DSL (type, selector, text, etc.)
- web_wait_for evaluates all 8 condition types via CDP
- web_wait_for handles timeout correctly
- web_wait_for handles fatal errors from CDP
- web_wait_for handles duration condition
- web_wait_for handles missing/invalid window_ref
- web_wait_for handles missing web connection
- web_screenshot returns stub responses without backend
- web_screenshot validates mode, format, quality, max_size_kb
- web_screenshot captures viewport screenshot via CDP
- web_screenshot captures fullpage screenshot via CDP
- web_screenshot captures element screenshot via CDP
- web_screenshot rejects oversized screenshots
- web_screenshot handles element mode without selector/element_ref
- web_screenshot handles missing web connection
- Both tools return READ_ONLY risk classification
"""

import asyncio
import base64
import json
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.router import BackendRouter, _tag
from pathlight_mcp.backends.web import WebBackend
from pathlight_mcp.cdp._types import CDPTarget
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def native_backend() -> MockBackend:
    """Return a MockBackend simulating the native backend."""
    return MockBackend().add_window(title="Explorer", app="explorer.exe")


@pytest.fixture()
def ref_store() -> ElementRefStore:
    """Return a fresh ElementRefStore."""
    return ElementRefStore()


@pytest.fixture()
def mcp_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a BackendRouter."""
    router = BackendRouter(native=native_backend)
    mcp = FastMCP(name="test-web-wait-screenshot")
    register_all(mcp, backend=router, ref_store=ref_store)
    mcp._test_router = router
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance in stub mode (no backend)."""
    mcp = FastMCP(name="test-web-wait-screenshot-stub")
    register_all(mcp)
    return mcp


# -- Helpers ------------------------------------------------------------------


def _get_tool(mcp: FastMCP, name: str):
    """Get a tool callable from an MCP instance by name."""
    tools = mcp._tool_manager.list_tools()
    return next(t for t in tools if t.name == name)


def _make_mock_web_backend() -> MagicMock:
    """Create a mock WebBackend."""
    web = MagicMock(spec=WebBackend)
    web._disposed = False
    web._connected = True
    web._ax_cache = {}
    web._bounds_cache = {}
    return web


def _setup_web_ref(
    mcp: FastMCP,
    ref_store: ElementRefStore,
    web_mock: MagicMock | None = None,
) -> tuple[str, MagicMock]:
    """Set up a web backend on the router and store a window ref."""
    router = mcp._test_router
    if web_mock is None:
        web_mock = _make_mock_web_backend()

    router._web = web_mock
    router._backends["web"] = web_mock

    target = CDPTarget(
        id="target-1",
        type="page",
        title="Test Page",
        url="https://example.com",
    )
    tagged_handle = _tag(target, "web")
    ref = ref_store.store(tagged_handle, prefix="w")
    return ref, web_mock


def _make_mock_session() -> MagicMock:
    """Create a mock CDP session."""
    session = MagicMock()
    session.is_attached = True
    session.send_command.return_value = {}
    return session


# ===========================================================================
# web_wait_for tests
# ===========================================================================


class TestWebWaitForStub:
    """web_wait_for returns stub responses without a backend."""

    def test_stub_returns_success(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "page_loaded"},
                )
            )
        )
        assert result["success"] is True
        assert result["elapsed_ms"] == 0
        assert result["polls"] == 0

    def test_stub_includes_condition(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_wait_for")
        cond = {"type": "selector_appears", "selector": "#btn"}
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(window_ref="w1", condition=cond)
            )
        )
        assert result["condition"] == cond


class TestWebWaitForValidation:
    """web_wait_for validates input parameters."""

    def test_empty_window_ref(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="",
                    condition={"type": "page_loaded"},
                )
            )
        )
        assert result["error"] == "validation_error"
        assert "window_ref" in result["message"]

    def test_missing_condition_type(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(window_ref="w1", condition={})
            )
        )
        assert result["error"] == "validation_error"
        assert "type" in result["message"]

    def test_unknown_condition_type(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(window_ref="w1", condition={"type": "invalid_type"})
            )
        )
        assert result["error"] == "validation_error"
        assert "Unknown condition type" in result["message"]

    def test_selector_condition_without_selector(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "selector_appears"},
                )
            )
        )
        assert result["error"] == "validation_error"
        assert "selector" in result["message"]

    def test_text_present_without_text(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "text_present"},
                )
            )
        )
        assert result["error"] == "validation_error"
        assert "text" in result["message"]

    def test_url_contains_without_url(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "url_contains"},
                )
            )
        )
        assert result["error"] == "validation_error"
        assert "url" in result["message"]

    def test_duration_without_duration_ms(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "duration"},
                )
            )
        )
        assert result["error"] == "validation_error"
        assert "duration_ms" in result["message"]

    def test_negative_timeout_ms(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "page_loaded"},
                    timeout_ms=-1,
                )
            )
        )
        assert result["error"] == "validation_error"

    def test_excessive_timeout_ms(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "page_loaded"},
                    timeout_ms=120000,
                )
            )
        )
        assert result["error"] == "validation_error"
        assert "60000" in result["message"]

    def test_poll_interval_too_small(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "page_loaded"},
                    poll_interval_ms=10,
                )
            )
        )
        assert result["error"] == "validation_error"

    def test_poll_interval_too_large(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "page_loaded"},
                    poll_interval_ms=10000,
                )
            )
        )
        assert result["error"] == "validation_error"

    def test_condition_not_dict(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(window_ref="w1", condition="not a dict")
            )
        )
        assert result["error"] == "validation_error"

    def test_empty_selector_string(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "selector_appears", "selector": "  "},
                )
            )
        )
        assert result["error"] == "validation_error"


class TestWebWaitForWebErrors:
    """web_wait_for handles web session errors."""

    def test_invalid_window_ref(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w999",
                    condition={"type": "page_loaded"},
                )
            )
        )
        assert "error" in result

    def test_no_web_connection(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        """BackendRouter without web backend returns error."""
        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref="w1",
                    condition={"type": "page_loaded"},
                )
            )
        )
        assert "error" in result


class TestWebWaitForConditionEvaluation:
    """web_wait_for evaluates conditions against the page via CDP."""

    def test_page_loaded_success(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        # Mock Runtime.evaluate to return "complete"
        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "string", "value": "complete"}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                    timeout_ms=1000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True
        assert result["condition"]["type"] == "page_loaded"
        assert result["polls"] >= 1
        assert result["risk"] == "read_only"

    def test_selector_appears_success(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            if "DOM.getDocument" in method:
                return {"root": {"nodeId": 1, "nodeName": "#document"}}
            if "DOM.querySelector" in method:
                return {"nodeId": 42}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "selector_appears", "selector": "#btn"},
                    timeout_ms=1000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True

    def test_selector_disappears_success(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            if "DOM.getDocument" in method:
                return {"root": {"nodeId": 1, "nodeName": "#document"}}
            if "DOM.querySelector" in method:
                return {"nodeId": 0}  # Not found
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={
                        "type": "selector_disappears",
                        "selector": ".loading",
                    },
                    timeout_ms=1000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True

    def test_text_present_success(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "boolean", "value": True}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={
                        "type": "text_present",
                        "text": "Hello World",
                    },
                    timeout_ms=1000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True

    def test_url_contains_success(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "boolean", "value": True}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={
                        "type": "url_contains",
                        "url": "/dashboard",
                    },
                    timeout_ms=1000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True

    def test_element_visible_success(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "boolean", "value": True}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={
                        "type": "element_visible",
                        "selector": "#content",
                    },
                    timeout_ms=1000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True

    def test_network_idle_success(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "boolean", "value": True}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "network_idle"},
                    timeout_ms=1000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True

    def test_duration_condition(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "duration", "duration_ms": 50},
                    timeout_ms=5000,
                )
            )
        )
        assert result["success"] is True
        assert result["polls"] == 1
        assert result["elapsed_ms"] >= 40  # Should have waited ~50ms

    def test_timeout_expires(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        # Always return false for page_loaded
        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "string", "value": "loading"}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                    timeout_ms=200,
                    poll_interval_ms=50,
                )
            )
        )
        assert result["success"] is False
        assert result["polls"] >= 1
        assert "not met" in result["message"].lower()

    def test_cdp_error_keeps_polling(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """CDP errors during evaluation should not stop polling."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        call_count = [0]

        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                call_count[0] += 1
                if call_count[0] <= 2:
                    raise Exception("CDP connection lost")
                return {"result": {"type": "string", "value": "complete"}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                    timeout_ms=2000,
                    poll_interval_ms=50,
                )
            )
        )
        assert result["success"] is True
        assert call_count[0] >= 3

    def test_session_creation_fails(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        web_mock._get_or_create_session.side_effect = Exception("no session")

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                )
            )
        )
        assert "error" in result

    def test_read_only_risk_classification(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """web_wait_for returns READ_ONLY risk level."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "string", "value": "complete"}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                    timeout_ms=1000,
                )
            )
        )
        assert result["risk"] == "read_only"


class TestWebWaitForAllConditionTypes:
    """Verify all 8 condition types are accepted."""

    @pytest.mark.parametrize(
        "condition",
        [
            {"type": "page_loaded"},
            {"type": "network_idle"},
            {"type": "selector_appears", "selector": "#btn"},
            {"type": "selector_disappears", "selector": ".loading"},
            {"type": "element_visible", "selector": "#content"},
            {"type": "text_present", "text": "Hello"},
            {"type": "url_contains", "url": "/page"},
            {"type": "duration", "duration_ms": 100},
        ],
    )
    def test_condition_type_accepted(
        self,
        mcp_router: FastMCP,
        ref_store: ElementRefStore,
        condition,
    ):
        """All 8 condition types should be accepted (not return validation error for type)."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "boolean", "value": True}}
            if "DOM.getDocument" in method:
                return {"root": {"nodeId": 1, "nodeName": "#document"}}
            if "DOM.querySelector" in method:
                return {"nodeId": 42}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition=condition,
                    timeout_ms=500,
                    poll_interval_ms=100,
                )
            )
        )
        # Should not be a validation error about the condition type
        if "error" in result:
            assert "Unknown condition type" not in result.get("message", "")


# ===========================================================================
# web_screenshot tests
# ===========================================================================


class TestWebScreenshotStub:
    """web_screenshot returns stub responses without a backend."""

    def test_stub_returns_success(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_screenshot")
        result = json.loads(tool.fn(window_ref="w1"))
        assert result["success"] is True
        assert result["mode"] == "viewport"
        assert result["format"] == "png"

    def test_stub_includes_mode(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_screenshot")
        result = json.loads(tool.fn(window_ref="w1", mode="fullpage"))
        assert result["mode"] == "fullpage"

    def test_stub_includes_format(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_screenshot")
        result = json.loads(tool.fn(window_ref="w1", format="jpeg"))
        assert result["format"] == "jpeg"


class TestWebScreenshotValidation:
    """web_screenshot validates input parameters."""

    def test_empty_window_ref(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(tool.fn(window_ref=""))
        assert result["error"] == "validation_error"
        assert "window_ref" in result["message"]

    def test_invalid_mode(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref="w1", mode="invalid")
        )
        assert result["error"] == "validation_error"
        assert "mode" in result["message"]

    def test_invalid_format(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref="w1", format="gif")
        )
        assert result["error"] == "validation_error"
        assert "format" in result["message"]

    def test_quality_out_of_range(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref="w1", quality=150)
        )
        assert result["error"] == "validation_error"
        assert "quality" in result["message"]

    def test_negative_quality(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref="w1", quality=-1)
        )
        assert result["error"] == "validation_error"

    def test_zero_max_size_kb(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref="w1", max_size_kb=0)
        )
        assert result["error"] == "validation_error"

    def test_element_mode_without_selector_or_ref(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref="w1", mode="element")
        )
        assert result["error"] == "validation_error"
        assert "selector" in result["message"].lower() or "element" in result["message"].lower()

    def test_negative_timeout_ms(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref="w1", mode="element", selector="#x", timeout_ms=-1)
        )
        assert result["error"] == "validation_error"


class TestWebScreenshotCapture:
    """web_screenshot captures screenshots via CDP."""

    def test_viewport_capture(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        # Fake base64 PNG data that's > 1 KB
        fake_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048).decode()

        def mock_send(method, params=None, **kwargs):
            if "Page.captureScreenshot" in method:
                return {"data": fake_b64}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref=window_ref, mode="viewport", format="png")
        )
        assert result["success"] is True
        assert result["mode"] == "viewport"
        assert result["format"] == "png"
        assert result["data"] == fake_b64
        assert result["size_kb"] > 0
        assert result["risk"] == "read_only"

    def test_fullpage_capture(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        fake_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()

        def mock_send(method, params=None, **kwargs):
            if "Page.getLayoutMetrics" in method:
                return {
                    "contentSize": {"width": 1200, "height": 3000},
                }
            if "Page.captureScreenshot" in method:
                return {"data": fake_b64}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(window_ref=window_ref, mode="fullpage")
        )
        assert result["success"] is True
        assert result["mode"] == "fullpage"

    def test_element_capture_with_selector(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        fake_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()

        def mock_send(method, params=None, **kwargs):
            if "DOM.getDocument" in method:
                return {"root": {"nodeId": 1, "nodeName": "#document"}}
            if "DOM.querySelector" in method:
                return {"nodeId": 42}
            if "DOM.querySelectorAll" in method:
                return {"nodeIds": [42]}
            if "DOM.getBoxModel" in method:
                return {
                    "model": {
                        "border": [10.0, 20.0, 110.0, 20.0, 110.0, 50.0, 10.0, 50.0],
                        "content": [10.0, 20.0, 110.0, 20.0, 110.0, 50.0, 10.0, 50.0],
                        "width": 100,
                        "height": 30,
                    }
                }
            if "Page.captureScreenshot" in method:
                return {"data": fake_b64}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(
                window_ref=window_ref,
                mode="element",
                selector="#target",
            )
        )
        assert result["success"] is True
        assert result["mode"] == "element"

    def test_jpeg_format_with_quality(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        fake_b64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 20).decode()

        def mock_send(method, params=None, **kwargs):
            if "Page.captureScreenshot" in method:
                return {"data": fake_b64}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(
                window_ref=window_ref,
                format="jpeg",
                quality=80,
            )
        )
        assert result["success"] is True
        assert result["format"] == "jpeg"


class TestWebScreenshotSizeCap:
    """web_screenshot enforces max_size_kb safety cap."""

    def test_screenshot_exceeds_max_size(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        # Create a large fake screenshot (> 1 KB)
        large_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048
        large_b64 = base64.b64encode(large_data).decode()

        def mock_send(method, params=None, **kwargs):
            if "Page.captureScreenshot" in method:
                return {"data": large_b64}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(
            tool.fn(
                window_ref=window_ref,
                max_size_kb=1,  # 1 KB limit
            )
        )
        assert result["error"] == "screenshot_too_large"
        assert result["size_kb"] > 1
        assert "max_size_kb" in result


class TestWebScreenshotWebErrors:
    """web_screenshot handles web session errors."""

    def test_session_creation_fails(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        web_mock._get_or_create_session.side_effect = Exception("no session")

        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(tool.fn(window_ref=window_ref))
        assert "error" in result

    def test_invalid_window_ref(self, mcp_router: FastMCP, ref_store: ElementRefStore):
        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(tool.fn(window_ref="w999"))
        assert "error" in result

    def test_capture_screenshot_exception(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        def mock_send(method, params=None, **kwargs):
            raise Exception("CDP capture failed")

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(tool.fn(window_ref=window_ref))
        assert "error" in result


# ===========================================================================
# GW-128: CDP session resilience tests
# ===========================================================================


class TestWebWaitForStaleSessionRecovery:
    """web_wait_for detects stale sessions and recreates them (GW-128)."""

    def test_stale_session_recreated_and_polling_continues(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """When the session raises a stale error, polling should recreate it and continue."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        stale_session = _make_mock_session()
        fresh_session = _make_mock_session()

        call_count = [0]

        def stale_send(method, params=None, **kwargs):
            raise Exception("Not attached to target session")

        def fresh_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                return {"result": {"type": "string", "value": "complete"}}
            return {}

        stale_session.send_command.side_effect = stale_send
        fresh_session.send_command.side_effect = fresh_send

        # First call returns stale session, second call returns fresh session
        web_mock._get_or_create_session.side_effect = [
            stale_session,
            fresh_session,
        ]

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                    timeout_ms=2000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True
        assert web_mock._get_or_create_session.call_count == 2

    def test_stale_session_exhausted_returns_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """When stale session retries are exhausted, an error is returned."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        stale_session = _make_mock_session()

        def stale_send(method, params=None, **kwargs):
            raise Exception("Session not found in browser")

        stale_session.send_command.side_effect = stale_send

        # All session creation attempts return stale sessions
        web_mock._get_or_create_session.return_value = stale_session

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                    timeout_ms=500,
                    poll_interval_ms=50,
                )
            )
        )
        assert result["error"] == "web_wait_for_error"
        assert "re-attach" in result["message"].lower() or "invalidated" in result["message"].lower()

    def test_non_stale_error_keeps_polling(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """Non-stale CDP errors should still be treated as transient (keep polling)."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        call_count = [0]

        def mock_send(method, params=None, **kwargs):
            if "Runtime.evaluate" in method:
                call_count[0] += 1
                if call_count[0] <= 2:
                    raise Exception("Random CDP glitch")
                return {"result": {"type": "string", "value": "complete"}}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                    timeout_ms=2000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["success"] is True
        # Session should NOT have been recreated for non-stale errors
        assert web_mock._get_or_create_session.call_count == 1

    def test_session_recreation_failure_returns_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """If session recreation fails, an error should be returned."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        stale_session = _make_mock_session()

        def stale_send(method, params=None, **kwargs):
            raise Exception("Not attached to target session")

        stale_session.send_command.side_effect = stale_send

        # First call returns stale session, recreation raises
        web_mock._get_or_create_session.side_effect = [
            stale_session,
            Exception("Cannot create session"),
        ]

        tool = _get_tool(mcp_router, "desktop.web_wait_for")
        result = json.loads(
            asyncio.get_event_loop().run_until_complete(
                tool.fn(
                    window_ref=window_ref,
                    condition={"type": "page_loaded"},
                    timeout_ms=2000,
                    poll_interval_ms=100,
                )
            )
        )
        assert result["error"] == "web_wait_for_error"
        assert "recreate" in result["message"].lower() or "session" in result["message"].lower()


class TestIsStaleSessionException:
    """Tests for _is_stale_session_exception helper in web_wait_for (GW-128)."""

    def test_not_attached_message(self):
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        assert _is_stale_session_exception(Exception("Not attached to target"))

    def test_session_not_found_message(self):
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        assert _is_stale_session_exception(Exception("Session not found"))

    def test_session_is_closing_message(self):
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        assert _is_stale_session_exception(Exception("Session is closing"))

    def test_target_closed_message(self):
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        assert _is_stale_session_exception(Exception("Target closed"))

    def test_cdp_error_code_32000(self):
        from pathlight_mcp.cdp.protocol import CDPError
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        assert _is_stale_session_exception(CDPError(-32000, "Something went wrong"))

    def test_pathlight_mcp_error_not_attached(self):
        from pathlight_mcp.errors import PathlightMCPError
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        assert _is_stale_session_exception(PathlightMCPError("Session is not attached"))

    def test_non_stale_exception_returns_false(self):
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        assert not _is_stale_session_exception(Exception("Random error"))

    def test_cdp_error_non_stale_code_returns_false(self):
        from pathlight_mcp.cdp.protocol import CDPError
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        assert not _is_stale_session_exception(CDPError(-32601, "Method not found"))

    def test_chained_cause_detected(self):
        from pathlight_mcp.cdp.protocol import CDPError
        from pathlight_mcp.tools.web_wait_for import _is_stale_session_exception
        wrapped = Exception("Command failed")
        wrapped.__cause__ = CDPError(-32000, "Not attached to target")
        assert _is_stale_session_exception(wrapped)


class TestWebScreenshotRiskClassification:
    """web_screenshot returns READ_ONLY risk classification."""

    def test_read_only_risk(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)
        session = _make_mock_session()
        web_mock._get_or_create_session.return_value = session

        fake_b64 = base64.b64encode(b"\x89PNG" + b"\x00" * 20).decode()

        def mock_send(method, params=None, **kwargs):
            if "Page.captureScreenshot" in method:
                return {"data": fake_b64}
            return {}

        session.send_command.side_effect = mock_send

        tool = _get_tool(mcp_router, "desktop.web_screenshot")
        result = json.loads(tool.fn(window_ref=window_ref))
        assert result["risk"] == "read_only"

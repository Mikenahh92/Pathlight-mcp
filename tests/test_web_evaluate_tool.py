"""Tests for desktop.web_evaluate tool handler (GW-099).

Validates that:
- web_evaluate executes JavaScript via CDP Runtime.evaluate
- web_evaluate returns SENSITIVE risk metadata
- web_evaluate enforces rate limiting via EvaluateRateLimiter
- web_evaluate sanitizes results via redact_web_content
- web_evaluate passes timeout to Runtime.evaluate
- web_evaluate handles validation errors (empty ref, empty expression, negative timeout)
- web_evaluate returns error when no web connection exists
- web_evaluate returns error for invalid/non-web window references
- web_evaluate returns error when evaluation fails (JS exception, timeout)
- web_evaluate returns error when backend is not a BackendRouter
- web_evaluate returns stub response in unwired mode
- web_evaluate classifies result types correctly
- web_evaluate hints are registered
- web_evaluate is classified as SENSITIVE system action
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.router import BackendRouter
from pathlight_mcp.backends.web import WebBackend
from pathlight_mcp.cdp._types import CDPTarget
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.safety import EvaluateRateLimiter
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
def unlimited_rate_limiter() -> MagicMock:
    """Return a mock rate limiter that always allows calls."""
    limiter = MagicMock(spec=EvaluateRateLimiter)
    limiter.is_allowed.return_value = True
    limiter.remaining = 10
    return limiter


@pytest.fixture()
def mcp_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a BackendRouter."""
    router = BackendRouter(native=native_backend)
    mcp = FastMCP(name="test-web-eval")
    register_all(mcp, backend=router, ref_store=ref_store)
    return mcp


@pytest.fixture()
def mcp_no_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with a plain MockBackend (no router)."""
    mcp = FastMCP(name="test-web-eval-no-router")
    register_all(mcp, backend=native_backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance in stub mode (no backend)."""
    mcp = FastMCP(name="test-web-eval-stub")
    register_all(mcp)
    return mcp


def _make_mock_web_backend(pages: list[CDPTarget] | None = None) -> MagicMock:
    """Create a mock WebBackend with optional page targets."""
    web = MagicMock(spec=WebBackend)
    if pages is None:
        pages = [
            CDPTarget(id="target-1", type="page", title="Test Page", url="https://example.com"),
        ]
    web.list_windows.return_value = pages
    web._disposed = False
    web._connected = True
    web._ax_cache = {}
    web._bounds_cache = {}
    return web


def _setup_connected_router(
    mcp_router: FastMCP, ref_store: ElementRefStore
) -> tuple[MagicMock, str]:
    """Set up a connected web backend and return (mock_web, window_ref)."""
    tools = mcp_router._tool_manager.list_tools()
    web_connect = next(t for t in tools if t.name == "desktop.web_connect")

    mock_web = _make_mock_web_backend()
    with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
        result_json = web_connect.fn(host="localhost", port=9222)

    result = json.loads(result_json)
    assert result["success"] is True
    return mock_web, result["pages"][0]["ref"]


def _get_web_evaluate_tool(mcp: FastMCP):
    """Get the web_evaluate tool callable from an MCP instance."""
    tools = mcp._tool_manager.list_tools()
    return next(t for t in tools if t.name == "desktop.web_evaluate")


# -- Stub mode tests ----------------------------------------------------------


class TestWebEvaluateStub:
    """web_evaluate in stub mode (no backend)."""

    def test_stub_returns_result_json(self, stub_mcp: FastMCP) -> None:
        """In stub mode, web_evaluate returns a JSON with success and result."""
        web_evaluate = _get_web_evaluate_tool(stub_mcp)
        result = json.loads(web_evaluate.fn(window_ref="w1", expression="1+1"))
        assert result["success"] is True
        assert result["result"] is None
        assert result["type"] == "undefined"


# -- Wired mode: success tests ------------------------------------------------


class TestWebEvaluateWired:
    """web_evaluate with a BackendRouter and active web connection."""

    def test_evaluates_expression(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate executes JS and returns the result."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_session.target.id = "target-1"
        mock_session.is_attached = True
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                mock_runtime = MockRuntime.return_value
                mock_runtime.evaluate.return_value = 42

                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="6 * 7"
                )

        result = json.loads(result_json)
        assert result["success"] is True
        assert result["result"] == 42
        assert result["type"] == "number"
        mock_runtime.evaluate.assert_called_once_with(
            "6 * 7",
            return_by_value=True,
            await_promise=False,
            timeout=5.0,
        )

    def test_returns_sensitive_risk_metadata(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate response includes SENSITIVE-tier risk metadata."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = "hello"
                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="'hello'"
                )

        result = json.loads(result_json)
        assert result["risk"] == "sensitive"
        assert result["confirmation_required"] is True

    def test_custom_timeout(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate passes custom timeout to Runtime.evaluate."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = None
                result_json = web_evaluate.fn(
                    window_ref=window_ref,
                    expression="document.title",
                    timeout=10.0,
                )

        result = json.loads(result_json)
        assert result["success"] is True
        MockRuntime.return_value.evaluate.assert_called_once_with(
            "document.title",
            return_by_value=True,
            await_promise=False,
            timeout=10.0,
        )

    def test_timeout_zero_passes_none(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """When timeout is 0, None is passed to Runtime.evaluate."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = None
                result_json = web_evaluate.fn(
                    window_ref=window_ref,
                    expression="1+1",
                    timeout=0,
                )

        result = json.loads(result_json)
        assert result["success"] is True
        MockRuntime.return_value.evaluate.assert_called_once_with(
            "1+1",
            return_by_value=True,
            await_promise=False,
            timeout=None,
        )

    def test_await_promise(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate passes await_promise=True to Runtime.evaluate."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = "resolved"
                result_json = web_evaluate.fn(
                    window_ref=window_ref,
                    expression="fetch('/api').then(r => r.json())",
                    await_promise=True,
                )

        result = json.loads(result_json)
        assert result["success"] is True
        MockRuntime.return_value.evaluate.assert_called_once_with(
            "fetch('/api').then(r => r.json())",
            return_by_value=True,
            await_promise=True,
            timeout=5.0,
        )

    def test_string_result_type(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """String results are classified as 'string' type."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = "hello world"
                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="'hello world'"
                )

        result = json.loads(result_json)
        assert result["type"] == "string"

    def test_boolean_result_type(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """Boolean results are classified as 'boolean' type."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = True
                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="true"
                )

        result = json.loads(result_json)
        assert result["type"] == "boolean"

    def test_null_result_type(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """None results are classified as 'undefined' type."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = None
                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="undefined"
                )

        result = json.loads(result_json)
        assert result["type"] == "undefined"

    def test_object_result_type(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """Dict results are classified as 'object' type."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = {"key": "value"}
                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="({key: 'value'})"
                )

        result = json.loads(result_json)
        assert result["type"] == "object"

    def test_array_result_type(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """List results are classified as 'array' type."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = [1, 2, 3]
                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="[1,2,3]"
                )

        result = json.loads(result_json)
        assert result["type"] == "array"


# -- Result sanitization tests -------------------------------------------------


class TestWebEvaluateSanitization:
    """Result sanitization via redact_web_content."""

    def test_sanitizes_string_result(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """String results containing sensitive patterns are redacted."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.return_value = "password=test123"
                with patch(
                    "pathlight_mcp.tools.web_evaluate.redact_web_content",
                    side_effect=lambda t: "[REDACTED]" if "password" in t else t,
                ):
                    result_json = web_evaluate.fn(
                        window_ref=window_ref, expression="document.cookie"
                    )

        result = json.loads(result_json)
        assert result["success"] is True
        assert result["result"] == "[REDACTED]"

    def test_sanitizes_nested_dict_strings(self) -> None:
        """String values inside dict results are sanitized recursively."""
        from pathlight_mcp.tools.web_evaluate import _sanitize_result

        data = {"safe": "hello", "secret": "password=abc"}
        with patch(
            "pathlight_mcp.tools.web_evaluate.redact_web_content",
            side_effect=lambda t: "[REDACTED]" if "password" in t else t,
        ):
            result = _sanitize_result(data)
        assert result["safe"] == "hello"
        assert result["secret"] == "[REDACTED]"

    def test_sanitizes_nested_list_strings(self) -> None:
        """String values inside list results are sanitized recursively."""
        from pathlight_mcp.tools.web_evaluate import _sanitize_result

        data = ["hello", "api_key=secret123"]
        with patch(
            "pathlight_mcp.tools.web_evaluate.redact_web_content",
            side_effect=lambda t: "[REDACTED]" if "api_key" in t else t,
        ):
            result = _sanitize_result(data)
        assert result[0] == "hello"
        assert result[1] == "[REDACTED]"

    def test_sanitizes_error_message(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """Error messages from evaluation exceptions are sanitized."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.side_effect = Exception(
                    "Error: password=secret in context"
                )
                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="bad_code"
                )

        result = json.loads(result_json)
        assert result["error"] == "web_evaluate_error"
        # The error message should have been passed through redact_web_content
        assert "password=secret" not in result["message"]


# -- Rate limiting tests -------------------------------------------------------


class TestWebEvaluateRateLimiting:
    """Rate limiting via EvaluateRateLimiter."""

    def test_rate_limited_returns_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """When rate limit is exceeded, web_evaluate returns a rate_limited error."""
        _mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        # Create a rate limiter that always denies
        mock_limiter = MagicMock(spec=EvaluateRateLimiter)
        mock_limiter.is_allowed.return_value = False
        mock_limiter.remaining = 0

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", mock_limiter):
            result_json = web_evaluate.fn(
                window_ref=window_ref, expression="1+1"
            )

        result = json.loads(result_json)
        assert result["error"] == "rate_limited"
        assert "rate limit" in result["message"].lower()
        assert len(result["hints"]) > 0

    def test_rate_limiter_checked_before_evaluation(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """Rate limiter is checked before attempting evaluation."""
        _mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_limiter = MagicMock(spec=EvaluateRateLimiter)
        mock_limiter.is_allowed.return_value = False
        mock_limiter.remaining = 0

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", mock_limiter), patch(
            "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
        ) as MockRuntime:
            web_evaluate.fn(window_ref=window_ref, expression="1+1")
            MockRuntime.return_value.evaluate.assert_not_called()


# -- Validation tests ---------------------------------------------------------


class TestWebEvaluateValidation:
    """Input validation for web_evaluate."""

    def test_validation_empty_window_ref(self, mcp_router: FastMCP) -> None:
        """web_evaluate returns validation error for empty window_ref."""
        web_evaluate = _get_web_evaluate_tool(mcp_router)
        result_json = web_evaluate.fn(window_ref="", expression="1+1")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "window_ref" in result["message"]

    def test_validation_whitespace_window_ref(self, mcp_router: FastMCP) -> None:
        """web_evaluate returns validation error for whitespace-only window_ref."""
        web_evaluate = _get_web_evaluate_tool(mcp_router)
        result_json = web_evaluate.fn(window_ref="   ", expression="1+1")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    def test_validation_empty_expression(self, mcp_router: FastMCP) -> None:
        """web_evaluate returns validation error for empty expression."""
        web_evaluate = _get_web_evaluate_tool(mcp_router)
        result_json = web_evaluate.fn(window_ref="w1", expression="")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "expression" in result["message"]

    def test_validation_whitespace_expression(self, mcp_router: FastMCP) -> None:
        """web_evaluate returns validation error for whitespace-only expression."""
        web_evaluate = _get_web_evaluate_tool(mcp_router)
        result_json = web_evaluate.fn(window_ref="w1", expression="   ")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    def test_validation_negative_timeout(self, mcp_router: FastMCP) -> None:
        """web_evaluate returns validation error for negative timeout."""
        web_evaluate = _get_web_evaluate_tool(mcp_router)
        result_json = web_evaluate.fn(
            window_ref="w1", expression="1+1", timeout=-1
        )
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "timeout" in result["message"]


# -- Error path tests ---------------------------------------------------------


class TestWebEvaluateErrors:
    """Error handling for web_evaluate."""

    def test_no_web_connection_error(
        self, mcp_router: FastMCP, unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate returns error when no web backend is connected."""
        web_evaluate = _get_web_evaluate_tool(mcp_router)
        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            result_json = web_evaluate.fn(
                window_ref="w1", expression="1+1"
            )
        result = json.loads(result_json)
        assert result["error"] == "web_evaluate_error"
        assert "web_connect" in result["message"]

    def test_no_router_error(
        self, mcp_no_router: FastMCP, unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate returns error when backend is not a BackendRouter."""
        web_evaluate = _get_web_evaluate_tool(mcp_no_router)
        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            result_json = web_evaluate.fn(
                window_ref="w1", expression="1+1"
            )
        result = json.loads(result_json)
        assert result["error"] == "web_evaluate_error"
        assert "BackendRouter" in result["message"]

    def test_invalid_window_ref_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate returns error for unknown window reference."""
        _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            result_json = web_evaluate.fn(
                window_ref="w999", expression="1+1"
            )
        result = json.loads(result_json)
        assert result["error"] == "web_evaluate_error"
        assert "not found" in result["message"].lower()

    def test_non_web_window_ref_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        native_backend: MockBackend, unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate returns error for native (non-web) window refs."""
        _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        from pathlight_mcp.backends.types import NativeHandle

        native_handle = NativeHandle("native-window-0")
        native_ref = ref_store.store(native_handle, prefix="w")

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            result_json = web_evaluate.fn(
                window_ref=native_ref, expression="1+1"
            )
        result = json.loads(result_json)
        assert result["error"] == "web_evaluate_error"
        assert "not a web window" in result["message"]

    def test_session_creation_failure(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate returns error when session creation fails."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_web._get_or_create_session.side_effect = Exception("Target not found")

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            result_json = web_evaluate.fn(
                window_ref=window_ref, expression="1+1"
            )
        result = json.loads(result_json)
        assert result["error"] == "web_evaluate_error"
        assert "Target not found" in result["message"]

    def test_evaluation_failure(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        unlimited_rate_limiter: MagicMock,
    ) -> None:
        """web_evaluate returns error when JS evaluation fails."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        web_evaluate = _get_web_evaluate_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch("pathlight_mcp.tools.web_evaluate._rate_limiter", unlimited_rate_limiter):
            with patch(
                "pathlight_mcp.cdp.domains.runtime.RuntimeDomain"
            ) as MockRuntime:
                MockRuntime.return_value.evaluate.side_effect = Exception(
                    "SyntaxError: Unexpected token"
                )
                result_json = web_evaluate.fn(
                    window_ref=window_ref, expression="invalid js {"
                )

        result = json.loads(result_json)
        assert result["error"] == "web_evaluate_error"
        assert "SyntaxError" in result["message"]


# -- Safety classification tests ----------------------------------------------


class TestWebEvaluateSafetyClassification:
    """Verify web_evaluate is classified as SENSITIVE."""

    def test_web_evaluate_is_sensitive(self) -> None:
        """web_evaluate system action is SENSITIVE."""
        from pathlight_mcp.safety import SYSTEM_ACTION_RISK_MAP

        assert SYSTEM_ACTION_RISK_MAP["web_evaluate"] == "SENSITIVE"

    def test_classify_web_evaluate(self) -> None:
        """classify_system_action returns SENSITIVE for web_evaluate."""
        from pathlight_mcp.safety import classify_system_action

        result = classify_system_action("web_evaluate", target="document.cookie")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True


# -- Hint registry tests ------------------------------------------------------


class TestWebEvaluateHints:
    """Verify web_evaluate error hints are registered."""

    def test_web_evaluate_error_hints(self) -> None:
        """web_evaluate_error has registered hints."""
        from pathlight_mcp.hints import hints_for

        hints = hints_for("web_evaluate_error")
        assert len(hints) > 0
        assert any("web_connect" in h for h in hints)

    def test_web_evaluate_error_hints_include_timeout(self) -> None:
        """web_evaluate_error hints mention timeout."""
        from pathlight_mcp.hints import hints_for

        hints = hints_for("web_evaluate_error")
        assert any("timeout" in h.lower() for h in hints)

    def test_web_evaluate_error_hints_include_syntax(self) -> None:
        """web_evaluate_error hints mention syntax errors."""
        from pathlight_mcp.hints import hints_for

        hints = hints_for("web_evaluate_error")
        assert any("syntax" in h.lower() for h in hints)


# -- Tool registry tests ------------------------------------------------------


class TestWebEvaluateRegistry:
    """Verify web_evaluate is properly registered."""

    def test_web_evaluate_in_tool_list(self, mcp_router: FastMCP) -> None:
        """desktop.web_evaluate is in the registered tool list."""
        tools = mcp_router._tool_manager.list_tools()
        names = [t.name for t in tools]
        assert "desktop.web_evaluate" in names

    def test_web_evaluate_not_in_backend_modules(self) -> None:
        """web_evaluate is NOT in _BACKEND_TOOL_MODULES (web tools bypass ABC)."""
        from pathlight_mcp.tools import _BACKEND_TOOL_MODULES

        assert ".web_evaluate" not in _BACKEND_TOOL_MODULES

    def test_web_evaluate_in_tool_modules(self) -> None:
        """web_evaluate IS in _TOOL_MODULES."""
        from pathlight_mcp.tools import _TOOL_MODULES

        assert ".web_evaluate" in _TOOL_MODULES

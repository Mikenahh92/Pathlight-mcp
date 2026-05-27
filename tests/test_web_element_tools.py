"""Tests for desktop.web_click, desktop.web_type, and desktop.web_hover tools (GW-122).

Validates that:
- web_click resolves a CSS selector and dispatches mouse click events
- web_click supports implicit auto-wait for selector resolution
- web_click handles validation errors (missing params, invalid inputs)
- web_click handles double-click via click_count parameter
- web_click supports right-click and middle-click via button parameter
- web_type resolves a CSS selector, focuses, and inserts text
- web_type supports clear mode (Ctrl+A + Backspace before typing)
- web_type supports slowly mode (per-character dispatchKeyEvent)
- web_type handles validation errors
- web_hover resolves a CSS selector and dispatches mouseMoved event
- web_hover handles validation errors
- All three tools return INTERACTION risk metadata
- All three tools return error when no web connection exists
- All three tools return error for invalid window references
- All three tools return stub responses in unwired mode
- Selector timeout returns structured error with hints
- Ambiguous selector returns structured error with match count
- Element reference resolution path works
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.router import BackendRouter
from guidewire.backends.web import WebBackend
from guidewire.cdp._types import AXNode, BoxModel, CDPTarget, DOMNode
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all

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
    mcp = FastMCP(name="test-web-element-tools")
    register_all(mcp, backend=router, ref_store=ref_store)
    # Store router on the MCP for test access
    mcp._test_router = router
    return mcp


@pytest.fixture()
def mcp_no_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with a plain MockBackend (no router)."""
    mcp = FastMCP(name="test-web-element-tools-no-router")
    register_all(mcp, backend=native_backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance in stub mode (no backend)."""
    mcp = FastMCP(name="test-web-element-tools-stub")
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
    """Set up a web backend on the router and store a window ref.

    Returns:
        Tuple of (window_ref, web_mock).
    """
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
    from guidewire.backends.router import _tag

    tagged_handle = _tag(target, "web")
    ref = ref_store.store(tagged_handle, prefix="w")
    return ref, web_mock


def _make_dom_and_input_mocks():
    """Create mock DOM and Input domains with standard element setup.

    Returns:
        Tuple of (mock_dom, mock_inp) where query_selector returns node_id 42
        and get_box_model returns bounds at (10,20) with size 100x30.
    """
    mock_dom = MagicMock()
    mock_dom.get_document.return_value = DOMNode(
        node_id=1, node_name="#document"
    )
    mock_dom.query_selector.return_value = 42
    mock_dom.query_selector_all.return_value = [42]  # single match by default
    mock_dom.get_box_model.return_value = BoxModel(
        border=(10.0, 20.0, 110.0, 20.0, 110.0, 50.0, 10.0, 50.0),
        content=(10.0, 20.0, 110.0, 20.0, 110.0, 50.0, 10.0, 50.0),
        width=100,
        height=30,
    )
    mock_dom.scroll_into_view_if_needed.return_value = None

    mock_inp = MagicMock()

    return mock_dom, mock_inp


# -- web_click: stub mode tests -----------------------------------------------


class TestWebClickStub:
    """web_click returns stub responses without a backend."""

    def test_stub_returns_success(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="w1", selector="#btn"))
        assert result["success"] is True
        assert result["selector"] == "#btn"

    def test_stub_includes_click_count(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="w1", selector="#btn", click_count=2))
        assert result["click_count"] == 2

    def test_stub_includes_button(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="w1", selector="#btn", button="right"))
        assert result["button"] == "right"


# -- web_type: stub mode tests ------------------------------------------------


class TestWebTypeStub:
    """web_type returns stub responses without a backend."""

    def test_stub_returns_success(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_type")
        result = json.loads(
            tool.fn(window_ref="w1", text="hello", selector="#input")
        )
        assert result["success"] is True
        assert result["text_length"] == 5

    def test_stub_includes_slowly(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_type")
        result = json.loads(
            tool.fn(window_ref="w1", text="hi", selector="#input", slowly=True)
        )
        assert result["slowly"] is True


# -- web_hover: stub mode tests -----------------------------------------------


class TestWebHoverStub:
    """web_hover returns stub responses without a backend."""

    def test_stub_returns_success(self, stub_mcp: FastMCP):
        tool = _get_tool(stub_mcp, "desktop.web_hover")
        result = json.loads(tool.fn(window_ref="w1", selector="#menu"))
        assert result["success"] is True
        assert result["selector"] == "#menu"


# -- web_click: validation tests ----------------------------------------------


class TestWebClickValidation:
    """web_click validates input parameters."""

    def test_missing_window_ref(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="", selector="#btn"))
        assert result["error"] == "validation_error"
        assert "window_ref" in result["message"]

    def test_missing_both_selector_and_ref(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="w1"))
        assert result["error"] == "validation_error"

    def test_empty_selector(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="w1", selector="  "))
        assert result["error"] == "validation_error"

    def test_invalid_click_count(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(
            tool.fn(window_ref="w1", selector="#btn", click_count=5)
        )
        assert result["error"] == "validation_error"
        assert "click_count" in result["message"]

    def test_invalid_button(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(
            tool.fn(window_ref="w1", selector="#btn", button="invalid")
        )
        assert result["error"] == "validation_error"
        assert "button" in result["message"]

    def test_negative_timeout(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(
            tool.fn(window_ref="w1", selector="#btn", timeout_ms=-1)
        )
        assert result["error"] == "validation_error"


# -- web_type: validation tests -----------------------------------------------


class TestWebTypeValidation:
    """web_type validates input parameters."""

    def test_missing_window_ref(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_type")
        result = json.loads(
            tool.fn(window_ref="", text="hi", selector="#input")
        )
        assert result["error"] == "validation_error"

    def test_missing_selector_and_ref(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_type")
        result = json.loads(tool.fn(window_ref="w1", text="hi"))
        assert result["error"] == "validation_error"

    def test_negative_timeout(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_type")
        result = json.loads(
            tool.fn(window_ref="w1", text="hi", selector="#input", timeout_ms=-1)
        )
        assert result["error"] == "validation_error"


# -- web_hover: validation tests ----------------------------------------------


class TestWebHoverValidation:
    """web_hover validates input parameters."""

    def test_missing_window_ref(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_hover")
        result = json.loads(tool.fn(window_ref="", selector="#menu"))
        assert result["error"] == "validation_error"

    def test_missing_selector_and_ref(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_hover")
        result = json.loads(tool.fn(window_ref="w1"))
        assert result["error"] == "validation_error"


# -- web_click: no web connection tests ----------------------------------------


class TestWebClickNoConnection:
    """web_click errors when no web backend is configured."""

    def test_no_web_connection(self, mcp_router: FastMCP):
        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="w1", selector="#btn"))
        assert result["error"] == "web_element_error"

    def test_not_a_router(self, mcp_no_router: FastMCP):
        tool = _get_tool(mcp_no_router, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="w1", selector="#btn"))
        assert result["error"] == "web_element_error"

    def test_invalid_window_ref(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        _setup_web_ref(mcp_router, ref_store)[0]
        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(tool.fn(window_ref="w999", selector="#btn"))
        assert result["error"] == "web_element_error"
        assert "not found" in result["message"].lower()


# -- web_click: success tests -------------------------------------------------


class TestWebClickSuccess:
    """web_click resolves selector and dispatches click."""

    def test_click_by_selector(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom, mock_inp = _make_dom_and_input_mocks()
            mock_dom_cls.return_value = mock_dom
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_click")
            result = json.loads(
                tool.fn(window_ref=window_ref, selector="#submit-btn")
            )

            assert result["success"] is True
            assert result["selector"] == "#submit-btn"
            assert result["risk"] == "interaction"
            assert result["confirmation_required"] is False

            # Verify click was dispatched at center of element (10+100/2=60, 20+30/2=35)
            mock_inp.dispatch_mouse_event.assert_any_call(
                "mousePressed", 60.0, 35.0, button="left", click_count=1
            )
            mock_inp.dispatch_mouse_event.assert_any_call(
                "mouseReleased", 60.0, 35.0, button="left", click_count=1
            )

    def test_click_double_click(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom, mock_inp = _make_dom_and_input_mocks()
            mock_dom_cls.return_value = mock_dom
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_click")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    selector="#item",
                    click_count=2,
                )
            )

            assert result["success"] is True
            assert result["click_count"] == 2

            mock_inp.dispatch_mouse_event.assert_any_call(
                "mousePressed", 60.0, 35.0, button="left", click_count=2
            )

    def test_click_right_click(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom, mock_inp = _make_dom_and_input_mocks()
            mock_dom_cls.return_value = mock_dom
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_click")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    selector="#ctx-target",
                    button="right",
                )
            )

            assert result["success"] is True
            assert result["button"] == "right"

            mock_inp.dispatch_mouse_event.assert_any_call(
                "mousePressed", 60.0, 35.0, button="right", click_count=1
            )
            mock_inp.dispatch_mouse_event.assert_any_call(
                "mouseReleased", 60.0, 35.0, button="right", click_count=1
            )

    def test_click_middle_click(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom, mock_inp = _make_dom_and_input_mocks()
            mock_dom_cls.return_value = mock_dom
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_click")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    selector="#link",
                    button="middle",
                )
            )

            assert result["success"] is True
            assert result["button"] == "middle"

            mock_inp.dispatch_mouse_event.assert_any_call(
                "mousePressed", 60.0, 35.0, button="middle", click_count=1
            )


# -- web_type: success tests --------------------------------------------------


class TestWebTypeSuccess:
    """web_type resolves selector and inserts text."""

    def test_type_by_selector(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom, mock_inp = _make_dom_and_input_mocks()
            mock_dom_cls.return_value = mock_dom
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_type")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    text="hello@example.com",
                    selector="#email",
                )
            )

            assert result["success"] is True
            assert result["text_length"] == 17
            assert result["risk"] == "interaction"

            # Verify insert_text was called with the correct text
            mock_inp.insert_text.assert_called_once_with("hello@example.com")

            # Verify focus was called on the element
            mock_dom.focus.assert_called_once_with(node_id=42)

    def test_type_with_clear(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom, mock_inp = _make_dom_and_input_mocks()
            mock_dom_cls.return_value = mock_dom
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_type")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    text="new value",
                    selector="#field",
                    clear=True,
                )
            )

            assert result["success"] is True
            assert result["clear"] is True

            # Verify Ctrl+A was dispatched before insert
            key_calls = mock_inp.dispatch_key_event.call_args_list
            assert len(key_calls) >= 2  # keyDown + keyUp for Ctrl+A
            mock_inp.insert_text.assert_called_once_with("new value")

    def test_type_slowly_mode(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """slowly=True dispatches per-character key events instead of insertText."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom, mock_inp = _make_dom_and_input_mocks()
            mock_dom_cls.return_value = mock_dom
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_type")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    text="ab",
                    selector="#field",
                    slowly=True,
                )
            )

            assert result["success"] is True
            assert result["slowly"] is True

            # insert_text should NOT be called in slowly mode
            mock_inp.insert_text.assert_not_called()

            # Instead, dispatchKeyEvent should be called for each char:
            # keyDown + char + keyUp per character = 6 calls for "ab"
            key_calls = mock_inp.dispatch_key_event.call_args_list
            assert len(key_calls) == 6

            # Verify first character 'a': keyDown, char, keyUp
            assert key_calls[0] == (("keyDown",), {"key": "a", "text": "a"})
            assert key_calls[1] == (("char",), {"key": "a", "text": "a"})
            assert key_calls[2] == (("keyUp",), {"key": "a"})

            # Verify second character 'b': keyDown, char, keyUp
            assert key_calls[3] == (("keyDown",), {"key": "b", "text": "b"})
            assert key_calls[4] == (("char",), {"key": "b", "text": "b"})
            assert key_calls[5] == (("keyUp",), {"key": "b"})


# -- web_hover: success tests -------------------------------------------------


class TestWebHoverSuccess:
    """web_hover resolves selector and dispatches mouseMoved."""

    def test_hover_by_selector(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom, mock_inp = _make_dom_and_input_mocks()
            mock_dom_cls.return_value = mock_dom
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_hover")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    selector=".menu-item",
                )
            )

            assert result["success"] is True
            assert result["selector"] == ".menu-item"
            assert result["risk"] == "interaction"

            # Verify mouseMoved was dispatched at element center
            mock_inp.dispatch_mouse_event.assert_called_once_with(
                "mouseMoved", 60.0, 35.0, button="none"
            )


# -- selector timeout tests ---------------------------------------------------


class TestSelectorTimeout:
    """Selector resolution returns timeout error when element not found."""

    def test_selector_timeout(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls:
            mock_dom = MagicMock()
            mock_dom.get_document.return_value = DOMNode(
                node_id=1, node_name="#document"
            )
            mock_dom.query_selector.return_value = None  # No match
            mock_dom_cls.return_value = mock_dom

            tool = _get_tool(mcp_router, "desktop.web_click")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    selector="#nonexistent",
                    timeout_ms=200,  # Short timeout
                )
            )

            assert result["error"] == "selector_timeout"
            assert "#nonexistent" in result["message"]
            assert "200ms" in result["message"]
            assert len(result["hints"]) > 0


# -- ambiguous selector tests -------------------------------------------------


class TestAmbiguousSelector:
    """Selector resolution returns ambiguous error when multiple elements match."""

    def test_ambiguous_selector(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls:
            mock_dom = MagicMock()
            mock_dom.get_document.return_value = DOMNode(
                node_id=1, node_name="#document"
            )
            mock_dom.query_selector.return_value = 42  # First match
            mock_dom.query_selector_all.return_value = [42, 43, 44]  # 3 matches
            mock_dom.get_box_model.return_value = BoxModel(
                border=(10.0, 20.0, 110.0, 20.0, 110.0, 50.0, 10.0, 50.0),
                width=100,
                height=30,
            )
            mock_dom_cls.return_value = mock_dom

            tool = _get_tool(mcp_router, "desktop.web_click")
            result = json.loads(
                tool.fn(
                    window_ref=window_ref,
                    selector="button",
                    timeout_ms=200,
                )
            )

            assert result["error"] == "ambiguous_selector"
            assert "button" in result["message"]
            assert result["match_count"] == 3
            assert len(result["hints"]) > 0

    def test_ambiguous_selector_hints_registered(self):
        """ambiguous_selector error code has registered hints."""
        from guidewire.hints import hints_for

        hints = hints_for("ambiguous_selector")
        assert len(hints) > 0


# -- element_ref resolution tests ---------------------------------------------


class TestElementRefResolution:
    """Element reference resolution path works for all three tools."""

    def test_element_ref_not_found(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """web_click returns element_not_found for unknown element_ref."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(
            tool.fn(window_ref=window_ref, element_ref="e999")
        )
        assert result["error"] == "element_not_found"
        assert "e999" in result["message"]

    def test_element_ref_resolves_from_cache(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """web_click resolves element_ref from bounds cache and dispatches click."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        # Store an element ref that maps to a web element
        from guidewire.backends.router import _tag

        ax_node_id = "ax-node-42"
        tagged_el = _tag(ax_node_id, "web")
        el_ref = ref_store.store(tagged_el, prefix="e")

        # Populate the bounds cache
        web_mock._bounds_cache[ax_node_id] = {
            "x": 50.0,
            "y": 100.0,
            "width": 200.0,
            "height": 40.0,
        }

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom = MagicMock()
            mock_dom_cls.return_value = mock_dom

            mock_inp = MagicMock()
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_click")
            result = json.loads(
                tool.fn(window_ref=window_ref, element_ref=el_ref)
            )

            assert result["success"] is True
            # Click at center: (50 + 200/2, 100 + 40/2) = (150, 120)
            mock_inp.dispatch_mouse_event.assert_any_call(
                "mousePressed", 150.0, 120.0, button="left", click_count=1
            )
            mock_inp.dispatch_mouse_event.assert_any_call(
                "mouseReleased", 150.0, 120.0, button="left", click_count=1
            )

    def test_element_ref_resolves_from_ax_cache(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """web_hover resolves element_ref from AX cache bounds."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        from guidewire.backends.router import _tag

        ax_node_id = "ax-node-99"
        tagged_el = _tag(ax_node_id, "web")
        el_ref = ref_store.store(tagged_el, prefix="e")

        # Populate the AX cache with inline bounds
        web_mock._ax_cache[ax_node_id] = AXNode(
            node_id=ax_node_id,
            role="button",
            name="Test",
            bounds={"x": 20.0, "y": 30.0, "width": 80.0, "height": 20.0},
        )

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        with patch("guidewire.cdp.domains.dom.DOMDomain") as mock_dom_cls, patch(
            "guidewire.cdp.domains.input.InputDomain"
        ) as mock_inp_cls:
            mock_dom = MagicMock()
            mock_dom_cls.return_value = mock_dom

            mock_inp = MagicMock()
            mock_inp_cls.return_value = mock_inp

            tool = _get_tool(mcp_router, "desktop.web_hover")
            result = json.loads(
                tool.fn(window_ref=window_ref, element_ref=el_ref)
            )

            assert result["success"] is True
            # Hover at center: (20 + 80/2, 30 + 20/2) = (60, 40)
            mock_inp.dispatch_mouse_event.assert_called_once_with(
                "mouseMoved", 60.0, 40.0, button="none"
            )

    def test_element_ref_non_web_backend(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ):
        """web_click rejects element_ref that resolves to non-web backend."""
        window_ref, web_mock = _setup_web_ref(mcp_router, ref_store)

        # Store an element ref tagged with 'native' backend
        from guidewire.backends.router import _tag

        tagged_el = _tag("native-handle-1", "native")
        el_ref = ref_store.store(tagged_el, prefix="e")

        mock_session = MagicMock()
        mock_session.target = MagicMock()
        mock_session.target.id = "target-1"
        web_mock._get_or_create_session.return_value = mock_session

        tool = _get_tool(mcp_router, "desktop.web_click")
        result = json.loads(
            tool.fn(window_ref=window_ref, element_ref=el_ref)
        )
        assert result["error"] == "web_element_error"
        assert "not a web element" in result["message"]


# -- risk metadata tests ------------------------------------------------------


class TestRiskMetadata:
    """All three tools return INTERACTION risk level."""

    def test_web_click_interaction_risk(self):
        from guidewire.safety import SYSTEM_ACTION_RISK_MAP

        assert SYSTEM_ACTION_RISK_MAP["web_click"] == "INTERACTION"

    def test_web_type_interaction_risk(self):
        from guidewire.safety import SYSTEM_ACTION_RISK_MAP

        assert SYSTEM_ACTION_RISK_MAP["web_type"] == "INTERACTION"

    def test_web_hover_interaction_risk(self):
        from guidewire.safety import SYSTEM_ACTION_RISK_MAP

        assert SYSTEM_ACTION_RISK_MAP["web_hover"] == "INTERACTION"


# -- tool registration tests --------------------------------------------------


class TestToolRegistration:
    """All three tools are properly registered."""

    def test_all_three_tools_registered(self, mcp_router: FastMCP):
        tool_names = [t.name for t in mcp_router._tool_manager.list_tools()]
        assert "desktop.web_click" in tool_names
        assert "desktop.web_type" in tool_names
        assert "desktop.web_hover" in tool_names

    def test_tools_not_in_backend_modules(self):
        from guidewire.tools import _BACKEND_TOOL_MODULES

        assert ".web_click" not in _BACKEND_TOOL_MODULES
        assert ".web_type" not in _BACKEND_TOOL_MODULES
        assert ".web_hover" not in _BACKEND_TOOL_MODULES


# -- CDP allowlist tests ------------------------------------------------------


class TestCDPAllowlist:
    """DOM.focus and DOM.scrollIntoViewIfNeeded are on the CDP allowlist."""

    def test_dom_focus_allowed(self):
        from guidewire.safety import is_cdp_method_allowed

        assert is_cdp_method_allowed("DOM.focus") is True

    def test_dom_scroll_into_view_allowed(self):
        from guidewire.safety import is_cdp_method_allowed

        assert is_cdp_method_allowed("DOM.scrollIntoViewIfNeeded") is True


# -- hints registry tests -----------------------------------------------------


class TestHintsRegistry:
    """New error code hints are registered."""

    def test_web_element_error_hints(self):
        from guidewire.hints import hints_for

        hints = hints_for("web_element_error")
        assert len(hints) > 0
        assert any("selector" in h.lower() for h in hints)

    def test_selector_timeout_hints(self):
        from guidewire.hints import hints_for

        hints = hints_for("selector_timeout")
        assert len(hints) > 0
        assert any("timeout" in h.lower() for h in hints)


# -- _web_selector module tests -----------------------------------------------


class TestWebSelectorModule:
    """The _web_selector module is importable and provides shared functions."""

    def test_module_importable(self):
        from guidewire.tools._web_selector import (
            DEFAULT_TIMEOUT_MS,
            POLL_INTERVAL,
            resolve_element,
            resolve_web_session,
        )

        assert DEFAULT_TIMEOUT_MS == 5000
        assert POLL_INTERVAL == 0.2
        assert callable(resolve_element)
        assert callable(resolve_web_session)

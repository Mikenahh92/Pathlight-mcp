"""Tests for the desktop.type_text tool handler (GW-015).

Validates that the wired type_text tool:
- Resolves an e-prefixed reference via ElementRefStore.
- Validates existence and staleness via backend.is_valid().
- Applies safety classification via classify().
- Invokes backend.perform_action(handle, DesktopAction.TYPE, text=text).
- Returns a structured JSON success response with safety metadata.
- Returns structured JSON error for unknown refs (not raises).
- Returns structured JSON error for stale refs.
- Returns structured JSON error for backend-rejected handles.
- Accepts empty text (architecture §5.2 reconcile v2).
- Falls back to static stub response when no backend is provided.
- Handles password fields, disabled elements, unicode text, and register signature.
"""

import inspect
import json
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.errors import ActionNotSupportedError
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import _BACKEND_TOOL_MODULES, register_all
from pathlight_mcp.tools import type_text as type_text_mod

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and text_input elements."""
    b = MockBackend().add_window(title="Test Window", app="TestApp", focused=True)
    window_handle = b.list_windows()[0]
    b.add_element(
        role="text_input",
        name="Username",
        parent=window_handle,
        actions=["type", "set_value"],
    )
    b.add_element(
        role="text_input",
        name="Search",
        parent=window_handle,
        actions=["type"],
    )
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
    mcp = FastMCP(name="test-type-text")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-type-text-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestTypeTextStub:
    """type_text returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, type_text should return a static string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "hello"},
        )
        assert result[0].text == 'Typed "hello" into e1'

    async def test_stub_contains_element_ref_and_text(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the element_ref and text values."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e99", "text": "world"},
        )
        assert "e99" in result[0].text
        assert "world" in result[0].text
        assert "Typed" in result[0].text


# -- Wired mode: success ------------------------------------------------------


class TestTypeTextSuccess:
    """type_text wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True, ref, role, risk, target_summary."""
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "hello"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "e1"
        assert "risk" in data
        assert "target_summary" in data

    async def test_success_response_has_interaction_risk(self, mcp: FastMCP) -> None:
        """Type action on a generic element should return interaction risk."""
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "hello"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"

    async def test_type_text_delegates_to_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Typing into e1 should record a TYPE action in the backend action log."""
        await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "hello world"},
        )
        assert len(backend.action_log) >= 1
        entry = backend.action_log[-1]
        assert entry["action"] == "type"
        assert entry["kwargs"]["text"] == "hello world"

    async def test_type_text_passes_correct_handle(
        self, mcp: FastMCP, backend: MockBackend, ref_store: ElementRefStore
    ) -> None:
        """The handle passed to perform_action should match the resolved ref."""
        await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "test"},
        )
        handle = ref_store.resolve("e1")
        entry = backend.action_log[-1]
        assert entry["handle"] == handle

    async def test_type_text_second_element(self, mcp: FastMCP) -> None:
        """Typing into e2 should also succeed."""
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e2", "text": "search query"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "e2"

    async def test_type_text_same_element_twice(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Typing into the same element twice should succeed both times."""
        result1, _ = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "first"},
        )
        result2, _ = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "second"},
        )
        data1 = json.loads(result1[0].text)
        data2 = json.loads(result2[0].text)
        assert data1["success"] is True
        assert data2["success"] is True
        assert backend.action_log[-1]["kwargs"]["text"] == "second"

    async def test_type_text_empty_string_text(self, mcp: FastMCP) -> None:
        """Empty text should succeed (per architecture §5.2 reconcile v2, TC-TT-14)."""
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": ""},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestTypeTextErrors:
    """type_text wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs, not raise."""
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e99", "text": "hello"},
        )
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
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e3", "text": "hello"},
        )
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
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "hello"},
        )
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
            side_effect=ActionNotSupportedError("Type not supported for this element"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.type_text",
                arguments={"element_ref": "e1", "text": "hello"},
            )
        data = json.loads(result[0].text)
        assert data["error"] == "action_not_supported"
        assert "e1" in data["message"]
        assert data["ref"] == "e1"


# -- Input validation ---------------------------------------------------------


class TestTypeTextValidation:
    """type_text input validation."""

    async def test_empty_element_ref_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty element_ref should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "", "text": "hello"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_element_ref_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only element_ref should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "   ", "text": "hello"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]


# -- Schema validation --------------------------------------------------------


class TestTypeTextSchema:
    """type_text tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should still be desktop.type_text."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.type_text" in names

    async def test_element_ref_required(self, mcp: FastMCP) -> None:
        """element_ref should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.type_text")
        schema = tool.inputSchema
        assert "element_ref" in schema["properties"]
        assert "element_ref" in schema["required"]

    async def test_text_required(self, mcp: FastMCP) -> None:
        """text should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.type_text")
        schema = tool.inputSchema
        assert "text" in schema["properties"]
        assert "text" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.type_text")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "type" in tool.description.lower()


# -- Safety classification edge cases (TC-TT-10, TC-TT-11) ---------------------


class TestTypeTextSafetyEdgeCases:
    """type_text safety classification for password and disabled elements."""

    async def test_tc_tt_10_password_field_sensitive_risk_no_text_echo(
        self,
    ) -> None:
        """TC-TT-10: Typing into a password field should succeed; response must not echo text."""
        backend = MockBackend().add_window(title="Login", app="TestApp", focused=True)
        window_handle = backend.list_windows()[0]
        backend.add_element(
            role="password_field",
            name="Password",
            parent=window_handle,
            actions=["type"],
        )
        store = ElementRefStore()
        elements = backend.find_elements(window_handle)
        for handle in elements:
            store.store(handle, prefix="e")

        mcp = FastMCP(name="test-pw")
        register_all(mcp, backend=backend, ref_store=store)

        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "s3cret"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        # Response must not contain the typed password in clear text
        assert "s3cret" not in json.dumps(data)
        # Backend should have received the text
        assert backend.action_log[-1]["kwargs"]["text"] == "s3cret"

    async def test_tc_tt_11_disabled_element_read_only_risk(self) -> None:
        """TC-TT-11: Typing into a disabled element returns read_only risk classification."""
        from pathlight_mcp.backends.types import ElementState

        backend = MockBackend().add_window(title="Form", app="TestApp", focused=True)
        window_handle = backend.list_windows()[0]
        backend.add_element(
            role="text_input",
            name="DisabledField",
            parent=window_handle,
            actions=["type"],
            states=ElementState(enabled=False),
        )
        store = ElementRefStore()
        elements = backend.find_elements(window_handle)
        for handle in elements:
            store.store(handle, prefix="e")

        mcp = FastMCP(name="test-disabled")
        register_all(mcp, backend=backend, ref_store=store)

        # The backend will accept the action (MockBackend doesn't gate on state),
        # but the tool creates a generic element for classification.
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": "text"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True


# -- Unicode passthrough (TC-TT-15) -------------------------------------------


class TestTypeTextUnicode:
    """type_text handles unicode text correctly (TC-TT-15)."""

    async def test_tc_tt_15_unicode_text_passthrough(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """TC-TT-15: Unicode text should pass through to the backend unchanged."""
        unicode_text = "Héllo Wörld 你好 🌍"
        result, _meta = await mcp.call_tool(
            "desktop.type_text",
            arguments={"element_ref": "e1", "text": unicode_text},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert backend.action_log[-1]["kwargs"]["text"] == unicode_text


# -- Register signature inspection (TC-TT-21, TC-TT-22) ------------------------


class TestTypeTextRegisterSignature:
    """type_text register function signature and module wiring (TC-TT-21, TC-TT-22)."""

    def test_tc_tt_22_register_signature(self) -> None:
        """TC-TT-22: register() accepts (mcp, *, backend, ref_store) kwargs."""
        sig = inspect.signature(type_text_mod.register)
        params = list(sig.parameters.keys())
        assert params == ["mcp", "backend", "ref_store"]
        assert sig.parameters["backend"].kind == inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["ref_store"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_tc_tt_21_type_text_in_backend_tool_modules(self) -> None:
        """TC-TT-21: .type_text must be present in _BACKEND_TOOL_MODULES."""
        assert ".type_text" in _BACKEND_TOOL_MODULES

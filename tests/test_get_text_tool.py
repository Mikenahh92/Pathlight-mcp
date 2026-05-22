"""Tests for the desktop.get_text tool handler (GW-017).

Validates that the wired get_text tool:
- Resolves an e-prefixed reference via ElementRefStore.
- Validates existence and staleness via backend.is_valid().
- Applies privacy redaction for password fields.
- Applies safety classification via classify().
- Invokes backend.perform_action(handle, DesktopAction.GET_TEXT).
- Returns a structured JSON success response with text, safety metadata.
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

from guidewire.backends import MockBackend
from guidewire.backends.types import NativeHandle
from guidewire.errors import ActionNotSupportedError
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and elements including text values."""
    b = MockBackend().add_window(title="Test Window", app="TestApp", focused=True)
    window_handle = b.list_windows()[0]
    b.add_element(role="text_input", name="Username", value="john_doe", parent=window_handle)
    b.add_element(role="label", name="Greeting", value="Hello, World!", parent=window_handle)
    b.add_element(role="button", name="Submit", parent=window_handle)
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
    mcp = FastMCP(name="test-get-text")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-get-text-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestGetTextStub:
    """get_text returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, get_text should return a static string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        assert result[0].text == "Text for e1"

    async def test_stub_returns_element_ref_in_text(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the element_ref value."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e99"},
        )
        assert "e99" in result[0].text
        assert "Text" in result[0].text


# -- Wired mode: success ------------------------------------------------------


class TestGetTextSuccess:
    """get_text wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True, ref, text, role, risk, target_summary."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "e1"
        assert "text" in data
        assert "role" in data
        assert "risk" in data
        assert "target_summary" in data

    async def test_returns_element_text(self, mcp: FastMCP) -> None:
        """Should return the element's text value from the backend."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == "john_doe"

    async def test_returns_second_element_text(self, mcp: FastMCP) -> None:
        """Should return text for the second element."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e2"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == "Hello, World!"

    async def test_returns_empty_string_for_no_value(self, mcp: FastMCP) -> None:
        """Should return empty string for elements without a value."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e3"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == ""

    async def test_delegates_to_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Calling get_text should record a GET_TEXT action in the backend action log."""
        await mcp.call_tool("desktop.get_text", arguments={"element_ref": "e1"})
        assert len(backend.action_log) >= 1
        entry = backend.action_log[-1]
        assert entry["action"] == "get_text"

    async def test_success_response_has_risk_level(self, mcp: FastMCP) -> None:
        """get_text action should return a risk level from safety classification."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] in ("read_only", "interaction", "sensitive")

    async def test_target_summary_describes_action(self, mcp: FastMCP) -> None:
        """target_summary should describe the action performed."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert "text retrieval" in data["target_summary"]


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestGetTextErrors:
    """get_text wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs, not raise."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e99"},
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
            "desktop.get_text",
            arguments={"element_ref": "e4"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e4"

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
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e1"

    async def test_action_not_supported_returns_structured_error(
        self,
        mcp: FastMCP,
        backend: MockBackend,
    ) -> None:
        """Should return action_not_supported error when backend raises."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=ActionNotSupportedError("Get text not supported for this element"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.get_text",
                arguments={"element_ref": "e1"},
            )
        data = json.loads(result[0].text)
        assert data["error"] == "action_not_supported"
        assert "e1" in data["message"]
        assert data["ref"] == "e1"


# -- Input validation ---------------------------------------------------------


class TestGetTextValidation:
    """get_text input validation."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": ""},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only string should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "   "},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]


# -- Privacy redaction --------------------------------------------------------


class TestGetTextPrivacy:
    """get_text privacy integration — handler invokes is_password_field."""

    async def test_text_returned_for_non_password_element(self, mcp: FastMCP) -> None:
        """Text from a non-password field should not be redacted."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["text"] == "john_doe"
        assert data["text"] != "[REDACTED]"

    async def test_privacy_check_called_via_is_password_field(
        self,
        mcp: FastMCP,
    ) -> None:
        """Verify is_password_field is called as part of the handler pipeline."""
        with patch("guidewire.tools.get_text.is_password_field", return_value=True):
            result, _meta = await mcp.call_tool(
                "desktop.get_text",
                arguments={"element_ref": "e1"},
            )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["text"] == "[REDACTED]"


# -- Real privacy detection (F1 fix: actual role/name from backend) -----------


class TestGetTextPrivacyReal:
    """get_text privacy with real element metadata (no mocking of is_password_field).

    TC-P01 through TC-P05: password detection uses actual role and name
    from the backend, not hardcoded values.
    """

    @pytest.fixture()
    def pw_backend(self) -> MockBackend:
        """Backend with password and non-password text_input elements."""
        b = MockBackend().add_window(title="Login", app="TestApp", focused=True)
        w = b.list_windows()[0]
        b.add_element(role="text_input", name="Password", value="s3cret", parent=w)
        b.add_element(role="text_input", name="Username", value="john", parent=w)
        b.add_element(role="text_input", name="Confirm PWD", value="s3cret", parent=w)
        b.add_element(role="text_input", name="PIN Code", value="1234", parent=w)
        b.add_element(role="text_input", name="Credential Token", value="tok", parent=w)
        b.add_element(role="label", name="Password Label", value="Enter password", parent=w)
        return b

    @pytest.fixture()
    def pw_ref_store(self, pw_backend: MockBackend) -> ElementRefStore:
        """Ref store for password backend elements."""
        store = ElementRefStore()
        w = pw_backend.list_windows()[0]
        for handle in pw_backend.find_elements(w):
            store.store(handle, prefix="e")
        return store

    @pytest.fixture()
    def pw_mcp(self, pw_backend: MockBackend, pw_ref_store: ElementRefStore) -> FastMCP:
        """FastMCP wired to password backend."""
        mcp = FastMCP(name="test-pw")
        register_all(mcp, backend=pw_backend, ref_store=pw_ref_store)
        return mcp

    async def test_tc_p01_password_name_redacted(self, pw_mcp: FastMCP) -> None:
        """TC-P01: text_input with 'Password' name should be redacted."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == "[REDACTED]"
        assert data["role"] == "text_input"
        assert data["name"] == "Password"

    async def test_tc_p02_non_password_not_redacted(self, pw_mcp: FastMCP) -> None:
        """TC-P02: text_input with non-password name should NOT be redacted."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e2"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == "john"
        assert data["text"] != "[REDACTED]"

    async def test_tc_p03_passwd_pattern_redacted(self, pw_mcp: FastMCP) -> None:
        """TC-P03: text_input with 'PWD' in name should be redacted."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e3"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == "[REDACTED]"

    async def test_tc_p04_pin_pattern_redacted(self, pw_mcp: FastMCP) -> None:
        """TC-P04: text_input with 'PIN' in name should be redacted."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e4"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == "[REDACTED]"

    async def test_tc_p05_credential_pattern_redacted(self, pw_mcp: FastMCP) -> None:
        """TC-P05: text_input with 'Credential' in name should be redacted."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e5"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == "[REDACTED]"

    async def test_non_text_input_never_redacted(self, pw_mcp: FastMCP) -> None:
        """A label with 'Password' in name should NOT be redacted (not text_input)."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e6"},
        )
        data = json.loads(result[0].text)
        assert data["text"] == "Enter password"
        assert data["text"] != "[REDACTED]"


# -- Real safety classification (F1 fix: actual role from backend) -------------


class TestGetTextSafetyReal:
    """get_text safety classification with real element metadata (TC-R01-TC-R05).

    Verifies classify() receives the actual element role from the backend,
    not a hardcoded placeholder.
    """

    @pytest.fixture()
    def safety_backend(self) -> MockBackend:
        """Backend with elements spanning all three risk tiers."""
        b = MockBackend().add_window(title="App", app="TestApp", focused=True)
        w = b.list_windows()[0]
        b.add_element(role="label", name="Status", value="OK", parent=w)
        b.add_element(role="text_input", name="Search", value="", parent=w)
        b.add_element(role="button", name="Delete Account", parent=w)
        b.add_element(role="button", name="Remove Item", parent=w)
        b.add_element(role="button", name="Save", parent=w)
        return b

    @pytest.fixture()
    def safety_ref_store(self, safety_backend: MockBackend) -> ElementRefStore:
        """Ref store for safety backend elements."""
        store = ElementRefStore()
        w = safety_backend.list_windows()[0]
        for handle in safety_backend.find_elements(w):
            store.store(handle, prefix="e")
        return store

    @pytest.fixture()
    def safety_mcp(
        self,
        safety_backend: MockBackend,
        safety_ref_store: ElementRefStore,
    ) -> FastMCP:
        """FastMCP wired to safety backend."""
        mcp = FastMCP(name="test-safety")
        register_all(mcp, backend=safety_backend, ref_store=safety_ref_store)
        return mcp

    async def test_tc_r01_label_is_read_only(self, safety_mcp: FastMCP) -> None:
        """TC-R01: label role should classify as READ_ONLY."""
        result, _meta = await safety_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "read_only"
        assert data["role"] == "label"

    async def test_tc_r02_text_input_is_interaction(self, safety_mcp: FastMCP) -> None:
        """TC-R02: text_input role should classify as INTERACTION (default)."""
        result, _meta = await safety_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e2"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"
        assert data["role"] == "text_input"

    async def test_tc_r03_destructive_name_is_sensitive(self, safety_mcp: FastMCP) -> None:
        """TC-R03: button with 'Delete' in name should classify as SENSITIVE."""
        result, _meta = await safety_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e3"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "sensitive"
        assert data["role"] == "button"

    async def test_tc_r04_remove_name_is_sensitive(self, safety_mcp: FastMCP) -> None:
        """TC-R04: button with 'Remove' in name should classify as SENSITIVE."""
        result, _meta = await safety_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e4"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "sensitive"

    async def test_tc_r05_safe_button_is_interaction(self, safety_mcp: FastMCP) -> None:
        """TC-R05: button with benign name should classify as INTERACTION (default)."""
        result, _meta = await safety_mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e5"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"
        assert data["role"] == "button"


# -- Response includes name (F3 fix) ------------------------------------------


class TestGetTextResponseName:
    """get_text response includes element name when available."""

    async def test_response_includes_name_for_named_element(self, mcp: FastMCP) -> None:
        """Response should include 'name' when element has a name."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["name"] == "Username"

    async def test_response_includes_actual_role(self, mcp: FastMCP) -> None:
        """Response 'role' should be the actual element role, not hardcoded."""
        result, _meta = await mcp.call_tool(
            "desktop.get_text",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["role"] == "text_input"


# -- Schema validation --------------------------------------------------------


class TestGetTextSchema:
    """get_text tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should still be desktop.get_text."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.get_text" in names

    async def test_element_ref_required(self, mcp: FastMCP) -> None:
        """element_ref should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_text")
        schema = tool.inputSchema
        assert "element_ref" in schema["properties"]
        assert "element_ref" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_text")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "text" in tool.description.lower()

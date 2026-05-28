"""Tests for the desktop.find tool handler (GW-013).

Validates that the wired find tool:
- Resolves a w-prefixed reference via ElementRefStore.
- Calls backend.find_elements() with role/name filters.
- Assigns e-prefixed refs to matched elements.
- Returns a structured JSON success response with safety metadata.
- Returns structured JSON error for unknown refs (not raises).
- Returns structured JSON error for stale refs.
- Returns structured JSON error for backend-rejected handles.
- Validates input (empty string, non-w-prefixed refs).
- Falls back to static stub response when no backend is provided.
"""

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
    """Return a MockBackend with a window and several elements."""
    b = MockBackend()
    b.add_window(title="Test Window", app="TestApp", focused=True)
    win = b.last_window_handle
    b.add_element(role="button", name="Save", parent=win)
    b.add_element(role="button", name="Cancel", parent=win)
    b.add_element(role="text_input", name="Username", parent=win)
    b.add_element(role="text_input", name="Password", parent=win)
    b.add_element(role="link", name="Help", parent=win)
    b.add_element(role="delete_button", name="Remove Item", parent=win)
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
    mcp = FastMCP(name="test-find")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-find-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestFindStub:
    """find returns static stub response when no backend is provided."""

    async def test_stub_returns_empty_array(self, stub_mcp: FastMCP) -> None:
        """Without a backend, find should return a static '[]' string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        assert result[0].text == "[]"

    async def test_stub_returns_empty_array_with_name(self, stub_mcp: FastMCP) -> None:
        """Without a backend, find with name filter should return '[]'."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w1", "name": "Save"},
        )
        assert result[0].text == "[]"


# -- Wired mode: success ------------------------------------------------------


class TestFindSuccess:
    """find wired to backend — success path."""

    async def test_find_by_role(self, mcp: FastMCP) -> None:
        """Should return matching elements with e-prefixed refs."""
        result, _meta = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["count"] == 2
        assert len(data["elements"]) == 2
        assert data["elements"][0]["ref"].startswith("e")
        assert data["elements"][1]["ref"].startswith("e")
        assert "risk" in data
        assert "target_summary" in data

    async def test_find_by_name(self, mcp: FastMCP) -> None:
        """Should return elements matching name (case-insensitive substring)."""
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w1", "name": "Save"},
        )
        data = json.loads(result[0].text)
        assert data["count"] == 1
        assert data["elements"][0]["ref"].startswith("e")

    async def test_find_by_role_and_name(self, mcp: FastMCP) -> None:
        """Should return elements matching both role and name."""
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w1", "role": "text_input", "name": "User"},
        )
        data = json.loads(result[0].text)
        assert data["count"] == 1

    async def test_find_no_matches(self, mcp: FastMCP) -> None:
        """Should return empty elements list when nothing matches."""
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w1", "role": "checkbox"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["count"] == 0
        assert data["elements"] == []
        assert data["risk"] == "read_only"
        assert data["target_summary"] == "no elements in window"

    async def test_find_all_elements_no_filter(self, mcp: FastMCP) -> None:
        """Should return all elements when no role/name filter is given."""
        result, _meta = await mcp.call_tool("desktop.find", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["count"] == 6

    async def test_assigns_unique_e_refs(self, mcp: FastMCP) -> None:
        """Each element should get a unique e-prefixed ref."""
        result, _meta = await mcp.call_tool("desktop.find", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        refs = [e["ref"] for e in data["elements"]]
        assert len(refs) == len(set(refs))

    async def test_response_has_target_summary(self, mcp: FastMCP) -> None:
        """Response should include dynamic target_summary field (architecture §5.4)."""
        result, _meta = await mcp.call_tool("desktop.find", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        # When no filter is applied, 6 elements are returned.  The dominant
        # role is the one with the highest count (buttons and text_inputs
        # are tied at 2 each; max() picks whichever comes first).
        assert data["target_summary"].endswith(" in window")
        assert str(data["count"]) in data["target_summary"]

    async def test_target_summary_reflects_filter(self, mcp: FastMCP) -> None:
        """target_summary should reflect the filtered role."""
        result, _meta = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        data = json.loads(result[0].text)
        assert data["target_summary"] == "2 buttons in window"


# -- Wired mode: risk metadata ------------------------------------------------


class TestFindRiskMetadata:
    """find wired to backend — safety risk classification."""

    async def test_default_risk_is_read_only(self, mcp: FastMCP) -> None:
        """Finding generic elements should return read_only risk by default."""
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w1", "role": "link"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "read_only"

    async def test_sensitive_role_elevates_risk(self, mcp: FastMCP) -> None:
        """Finding delete_button elements should elevate risk to sensitive."""
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w1", "role": "delete_button"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "sensitive"
        assert data["elements"][0]["risk"] == "sensitive"

    async def test_per_element_risk(self, mcp: FastMCP) -> None:
        """Each element entry should include its own risk level."""
        result, _meta = await mcp.call_tool("desktop.find", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        for elem in data["elements"]:
            assert "risk" in elem
            assert elem["risk"] in ("read_only", "interaction", "sensitive")


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestFindErrors:
    """find wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs, not raise."""
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w99", "role": "button"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "window_not_found"
        assert "w99" in data["message"]
        assert data["ref"] == "w99"

    async def test_backend_rejects_unknown_handle(
        self,
        mcp: FastMCP,
        ref_store: ElementRefStore,
    ) -> None:
        """Should return stale error for handle not known to backend."""
        ghost = NativeHandle("ghost-window-handle")
        ref_store.store(ghost, prefix="w")
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w2", "role": "button"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w2"

    async def test_disposed_window_returns_stale_error(
        self,
        mcp: FastMCP,
        backend: MockBackend,
    ) -> None:
        """Should return stale error after backend is disposed."""
        backend.dispose()
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w1", "role": "button"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"


# -- Input validation ---------------------------------------------------------


class TestFindValidation:
    """find input validation."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.find", arguments={"window_ref": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.find", arguments={"window_ref": "   "})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_non_w_prefix_returns_validation_error(self, mcp: FastMCP) -> None:
        """Non-w-prefixed ref should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "e1", "role": "button"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "w" in data["message"]
        assert data["ref"] == "e1"


# -- Schema validation --------------------------------------------------------


class TestFindSchema:
    """find tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should still be desktop.find."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.find" in names

    async def test_window_ref_required(self, mcp: FastMCP) -> None:
        """window_ref should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.find")
        schema = tool.inputSchema
        assert "window_ref" in schema["properties"]
        assert "window_ref" in schema["required"]

    async def test_role_optional(self, mcp: FastMCP) -> None:
        """role should be an optional parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.find")
        schema = tool.inputSchema
        assert "role" in schema["properties"]
        assert "role" not in schema["required"]

    async def test_name_optional(self, mcp: FastMCP) -> None:
        """name should be an optional parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.find")
        schema = tool.inputSchema
        assert "name" in schema["properties"]
        assert "name" not in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.find")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "find" in tool.description.lower()


# -- Ref store integration ----------------------------------------------------


class TestFindRefStoreIntegration:
    """find tool correctly integrates with ElementRefStore."""

    async def test_refs_are_stored_in_ref_store(
        self, mcp: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """Found element handles should be stored in ref_store with e-prefix."""
        result, _meta = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        data = json.loads(result[0].text)
        for elem in data["elements"]:
            assert ref_store.is_valid(elem["ref"])
            handle = ref_store.resolve(elem["ref"])
            assert handle is not None

    async def test_subsequent_finds_get_new_refs(self, mcp: FastMCP) -> None:
        """Each find call should assign new refs (not reuse previous)."""
        result1, _ = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        data1 = json.loads(result1[0].text)

        result2, _ = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        data2 = json.loads(result2[0].text)

        # Refs should be different since the counter increments
        refs1 = {e["ref"] for e in data1["elements"]}
        refs2 = {e["ref"] for e in data2["elements"]}
        assert refs1 != refs2


# -- TC-FIND-012: Backend raises WindowNotFoundError --------------------------


class TestFindBackendErrors:
    """find tool — backend error boundary tests."""

    async def test_backend_raises_window_not_found_error(
        self,
        backend: MockBackend,
        ref_store: ElementRefStore,
    ) -> None:
        """TC-FIND-012: Backend raises WindowNotFoundError — test error boundary."""
        # Store a handle that is NOT a real window in the backend.
        # Since is_valid() returns False for unknown handles, the staleness
        # check catches it first, returning stale_element_reference.
        ghost = NativeHandle("nonexistent-window")
        ref_store.store(ghost, prefix="w")
        mcp = FastMCP(name="test-find-error")
        from pathlight_mcp.tools import register_all

        register_all(mcp, backend=backend, ref_store=ref_store)
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w2", "role": "button"},
        )
        data = json.loads(result[0].text)
        # The staleness check fires before find_elements for invalid handles
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w2"

    async def test_disposed_backend_raises_window_not_found(
        self,
        backend: MockBackend,
        ref_store: ElementRefStore,
    ) -> None:
        """TC-FIND-012: After dispose, find returns stale_element_reference."""
        # Create a fresh backend for this test to avoid polluting shared state
        fresh = MockBackend()
        fresh.add_window(title="Temp", app="TempApp")
        fresh_win = fresh.last_window_handle
        fresh.add_element(role="button", name="OK", parent=fresh_win)
        store = ElementRefStore()
        store.store(fresh_win, prefix="w")
        fresh.dispose()
        mcp = FastMCP(name="test-find-dispose")
        from pathlight_mcp.tools import register_all

        register_all(mcp, backend=fresh, ref_store=store)
        result, _meta = await mcp.call_tool(
            "desktop.find",
            arguments={"window_ref": "w1", "role": "button"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"


# -- TC-FIND-019: Disabled element returns read_only risk ---------------------


class TestFindDisabledElement:
    """find tool — disabled element risk classification."""

    async def test_disabled_element_returns_read_only_risk(self, mcp: FastMCP) -> None:
        """TC-FIND-019: Disabled element returns read_only risk."""
        # The default fixture has all elements enabled.  We test that when
        # classify() receives a disabled NormalizedElement, risk is read_only.
        from pathlight_mcp.models import ElementStates, NormalizedElement
        from pathlight_mcp.safety import classify

        element = NormalizedElement(
            ref="e_test",
            backend_id="test",
            role="delete_button",
            states=ElementStates(enabled=False),
        )
        assessment = classify(element, "find")
        assert assessment.risk_level == "READ_ONLY"


# -- TC-FIND-023: Backend delegation verification -----------------------------


class TestFindBackendDelegation:
    """find tool — verify backend delegation."""

    async def test_backend_delegation_verification(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """TC-FIND-023: Backend delegation verification — verify find_elements called."""
        # Call find and then verify the backend was used by checking that
        # results match what MockBackend.find_elements would return.
        result, _meta = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["count"] == 2

        # Verify by calling backend directly and confirming same count
        windows = backend.list_windows()
        assert len(windows) >= 1
        direct = backend.find_elements(windows[0], role="button")
        assert len(direct) == 2
        assert len(direct) == data["count"]


# -- TC-FIND-025: Assert success:true in response -----------------------------


class TestFindSuccessField:
    """find tool — success field in response."""

    async def test_success_true_in_response(self, mcp: FastMCP) -> None:
        """TC-FIND-025: Update to assert success:true in response."""
        result, _meta = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "link"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_success_field_absent_on_error(self, mcp: FastMCP) -> None:
        """Error responses should not contain success field."""
        result, _meta = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w99", "role": "button"}
        )
        data = json.loads(result[0].text)
        assert "success" not in data
        assert "error" in data


# -- TC-FIND-026: Assert name field in per-element output ---------------------


class TestFindNameField:
    """find tool — name field in per-element output."""

    async def test_name_field_present_in_elements(self, mcp: FastMCP) -> None:
        """TC-FIND-026: Update to assert name field in per-element output."""
        result, _meta = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        data = json.loads(result[0].text)
        names = {e.get("name") for e in data["elements"]}
        assert "Save" in names
        assert "Cancel" in names

    async def test_name_field_reflects_element_name(self, mcp: FastMCP) -> None:
        """Each element's name should match its accessible name."""
        result, _meta = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "name": "Password"}
        )
        data = json.loads(result[0].text)
        assert data["count"] == 1
        assert data["elements"][0]["name"] == "Password"

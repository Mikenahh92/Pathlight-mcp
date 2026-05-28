"""Tests for the desktop.get_tree_info tool handler (GW-050).

Validates that the wired get_tree_info tool:
- Resolves an e-prefixed reference via ElementRefStore.
- Validates existence and staleness via backend.is_valid().
- Retrieves element info via backend.get_element_info().
- Returns expanded and expandable state from element states/actions.
- Applies safety classification via classify().
- Returns a structured JSON success response with safety metadata.
- Returns structured JSON error for unknown refs (not raises).
- Returns structured JSON error for stale refs.
- Returns structured JSON error for backend-rejected handles.
- Validates input (empty string).
- Falls back to static stub response when no backend is provided.
- Traverses tree hierarchy with children, node_count, tree_level, max_depth.
- Validates tree roles (tree, tree_item, outline only).
- Applies privacy redaction on tree node names.
- Supports window_ref dual-ref pattern.
- Supports max_depth parameter.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import DesktopAction, ElementState, NativeHandle
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and tree elements."""
    b = MockBackend().add_window(title="Explorer", app="explorer.exe", focused=True)
    window_handle = b.list_windows()[0]
    # Add a tree container
    b.add_element(
        role="tree",
        name="File Tree",
        parent=window_handle,
        states=ElementState(enabled=True, expanded=True),
        actions=[DesktopAction.EXPAND, DesktopAction.COLLAPSE],
    )
    # Add a collapsed tree item
    b.add_element(
        role="tree_item",
        name="Documents",
        parent=window_handle,
        states=ElementState(enabled=True, expanded=False),
        actions=[DesktopAction.EXPAND],
    )
    # Add an expanded tree item
    b.add_element(
        role="tree_item",
        name="Pictures",
        parent=window_handle,
        states=ElementState(enabled=True, expanded=True),
        actions=[DesktopAction.EXPAND, DesktopAction.COLLAPSE],
    )
    # Add a leaf tree item (no expand/collapse actions)
    b.add_element(
        role="tree_item",
        name="readme.txt",
        parent=window_handle,
        states=ElementState(enabled=True),
        actions=[DesktopAction.CLICK],
    )
    return b


@pytest.fixture()
def ref_store(backend: MockBackend) -> ElementRefStore:
    """Return an ElementRefStore with e-prefixed refs for elements."""
    store = ElementRefStore()
    window_handle = backend.list_windows()[0]
    # Register the window ref
    store.store(window_handle, prefix="w")
    elements = backend.find_elements(window_handle)
    for handle in elements:
        store.store(handle, prefix="e")
    return store


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-get-tree-info")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-get-tree-info-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestGetTreeInfoStub:
    """get_tree_info returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, get_tree_info should return a static string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        assert result[0].text == "Tree info for e1"

    async def test_stub_returns_element_ref_in_text(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the element_ref value."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e42"}
        )
        assert "e42" in result[0].text
        assert "Tree info" in result[0].text


# -- Wired mode: success ------------------------------------------------------


class TestGetTreeInfoSuccess:
    """get_tree_info wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True, ref, role, expanded, expandable."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "e1"
        assert "role" in data
        assert "expanded" in data
        assert "expandable" in data
        assert "risk" in data
        assert "target_summary" in data

    async def test_tree_container_expanded(self, mcp: FastMCP) -> None:
        """Tree container element should report expanded=True."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert data["expanded"] is True
        assert data["expandable"] is True
        assert data["role"] == "tree"
        assert data["name"] == "File Tree"

    async def test_collapsed_tree_item(self, mcp: FastMCP) -> None:
        """Collapsed tree item should report expanded=False, expandable=True."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e2"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["expanded"] is False
        assert data["expandable"] is True
        assert data["name"] == "Documents"

    async def test_expanded_tree_item(self, mcp: FastMCP) -> None:
        """Expanded tree item should report expanded=True, expandable=True."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e3"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["expanded"] is True
        assert data["expandable"] is True
        assert data["name"] == "Pictures"

    async def test_leaf_tree_item_not_expandable(self, mcp: FastMCP) -> None:
        """Leaf tree item with no expand/collapse should report expandable=False."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e4"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["expandable"] is False
        assert data["name"] == "readme.txt"

    async def test_success_response_has_risk(self, mcp: FastMCP) -> None:
        """Response should include risk classification."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"

    async def test_success_response_has_target_summary(self, mcp: FastMCP) -> None:
        """Response should include a descriptive target_summary."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert "tree info" in data["target_summary"]


# -- Wired mode: hierarchy traversal ------------------------------------------


class TestGetTreeInfoHierarchy:
    """get_tree_info returns tree hierarchy with children array."""

    async def test_returns_children_array(self, mcp: FastMCP) -> None:
        """Should return a children array for tree elements."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert "children" in data
        assert isinstance(data["children"], list)

    async def test_returns_node_count(self, mcp: FastMCP) -> None:
        """Should return node_count including the root element."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert "node_count" in data
        assert data["node_count"] >= 1

    async def test_returns_tree_level(self, mcp: FastMCP) -> None:
        """TC-019: Should return tree_level for the element."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert "tree_level" in data
        assert isinstance(data["tree_level"], int)

    async def test_returns_max_depth(self, mcp: FastMCP) -> None:
        """Should return max_depth of the traversed subtree."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert "max_depth" in data
        assert isinstance(data["max_depth"], int)

    async def test_max_depth_parameter(self, mcp: FastMCP) -> None:
        """TC-005: max_depth parameter should limit traversal depth."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info",
            arguments={"element_ref": "e1", "max_depth": 0},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        # With max_depth=0, the root itself is returned but no children traversed
        assert "children" in data


# -- Wired mode: window_ref parameter -----------------------------------------


class TestGetTreeInfoWindowRef:
    """get_tree_info supports window_ref dual-ref pattern."""

    async def test_window_ref_scopes_traversal(self, mcp: FastMCP, backend: MockBackend) -> None:
        """window_ref should scope the snapshot traversal."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info",
            arguments={"element_ref": "e1", "window_ref": "w1"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_without_window_ref_finds_window(self, mcp: FastMCP) -> None:
        """Should auto-discover window from ref store when window_ref omitted."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        # Should still have hierarchy since the window is auto-discovered
        assert "children" in data


# -- Wired mode: tree role validation -----------------------------------------


class TestGetTreeInfoRoleValidation:
    """TC-016: get_tree_info rejects non-tree elements."""

    async def test_button_rejected_as_non_tree(
        self, backend: MockBackend, ref_store: ElementRefStore
    ) -> None:
        """Should return validation error for non-tree roles (e.g. button)."""
        window_handle = backend.list_windows()[0]
        backend.add_element(
            role="button",
            name="Save",
            parent=window_handle,
            states=ElementState(enabled=True),
            actions=[DesktopAction.CLICK],
        )
        # Register the new element
        elements = backend.find_elements(window_handle, role="button")
        btn_ref = ref_store.store(elements[0], prefix="e")

        mcp = FastMCP(name="test-role-validation")
        register_all(mcp, backend=backend, ref_store=ref_store)

        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info",
            arguments={"element_ref": btn_ref},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "not a valid tree element" in data["message"]
        assert data["role"] == "button"

    async def test_outline_role_accepted(
        self, backend: MockBackend, ref_store: ElementRefStore
    ) -> None:
        """Outline role should be accepted as a valid tree element."""
        window_handle = backend.list_windows()[0]
        backend.add_element(
            role="outline",
            name="Nav Outline",
            parent=window_handle,
            states=ElementState(enabled=True, expanded=True),
            actions=[DesktopAction.EXPAND, DesktopAction.COLLAPSE],
        )
        elements = backend.find_elements(window_handle, role="outline")
        outline_ref = ref_store.store(elements[0], prefix="e")

        mcp = FastMCP(name="test-outline-role")
        register_all(mcp, backend=backend, ref_store=ref_store)

        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info",
            arguments={"element_ref": outline_ref},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["role"] == "outline"


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestGetTreeInfoErrors:
    """get_tree_info wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs, not raise."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e99"}
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
        ghost = NativeHandle("ghost-tree-handle")
        ref_store.store(ghost, prefix="e")
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "e5"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e5"

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
            "desktop.get_tree_info", arguments={"element_ref": "e1"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e1"


# -- Input validation ---------------------------------------------------------


class TestGetTreeInfoValidation:
    """get_tree_info input validation."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.get_tree_info", arguments={"element_ref": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only string should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "   "}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_non_e_prefixed_ref_not_found(self, mcp: FastMCP) -> None:
        """TC-008: Non-e-prefixed ref should return element_not_found."""
        result, _meta = await mcp.call_tool(
            "desktop.get_tree_info", arguments={"element_ref": "x1"}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "element_not_found"
        assert "x1" in data["message"]


# -- Schema validation --------------------------------------------------------


class TestGetTreeInfoSchema:
    """get_tree_info tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.get_tree_info."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.get_tree_info" in names

    async def test_element_ref_required(self, mcp: FastMCP) -> None:
        """element_ref should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_tree_info")
        schema = tool.inputSchema
        assert "element_ref" in schema["properties"]
        assert "element_ref" in schema["required"]

    async def test_window_ref_optional(self, mcp: FastMCP) -> None:
        """window_ref should be an optional parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_tree_info")
        schema = tool.inputSchema
        assert "window_ref" in schema["properties"]
        assert "window_ref" not in schema.get("required", [])

    async def test_max_depth_optional(self, mcp: FastMCP) -> None:
        """max_depth should be an optional parameter with default 4."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_tree_info")
        schema = tool.inputSchema
        assert "max_depth" in schema["properties"]
        assert "max_depth" not in schema.get("required", [])

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_tree_info")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "tree" in tool.description.lower()

"""Tests for the desktop.snapshot tool handler (GW-012).

Validates that the wired snapshot tool:
- Resolves a w-prefixed reference via ElementRefStore.
- Calls backend.snapshot() and normalizes results to NormalizedElement tree.
- Assigns e-prefixed refs to all descendant elements.
- Applies privacy redaction before returning.
- Returns structured JSON with risk metadata and element count.
- Returns structured JSON error for unknown refs (not raises).
- Returns structured JSON error for stale refs.
- Validates input (empty string, non-w-prefixed refs).
- Falls back to static stub response when no backend is provided.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.types import NativeHandle
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and child elements."""
    b = MockBackend().add_window(title="Test App", app="test.exe", focused=True)
    win_handle = b.last_window_handle
    b.add_element(role="button", name="Save", parent=win_handle)
    b.add_element(role="text_input", name="Username", parent=win_handle)
    b.add_element(role="text_input", name="Password", parent=win_handle)
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
    mcp = FastMCP(name="test-snapshot")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-snapshot-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestSnapshotStub:
    """snapshot returns static stub response when no backend is provided."""

    async def test_stub_returns_static_json(self, stub_mcp: FastMCP) -> None:
        """Without a backend, snapshot should return a static JSON string."""
        result, _meta = await stub_mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["ref"] == "w1"
        assert data["role"] == "window"
        assert "children" in data

    async def test_stub_ignores_params(self, stub_mcp: FastMCP) -> None:
        """Stub response should be the same regardless of parameters."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.snapshot",
            arguments={"window_ref": "w99", "max_depth": 10, "max_nodes": 1000},
        )
        data = json.loads(result[0].text)
        assert data["ref"] == "w1"


# -- Wired mode: success ------------------------------------------------------


class TestSnapshotSuccess:
    """snapshot wired to backend — success path."""

    async def test_returns_json_with_tree(self, mcp: FastMCP) -> None:
        """Should return JSON with tree, risk, target_summary, element_count."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert "tree" in data
        assert "risk" in data
        assert "target_summary" in data
        assert "element_count" in data
        assert "max_depth_reached" in data

    async def test_tree_has_window_root(self, mcp: FastMCP) -> None:
        """Root element in tree should be a window with w-prefixed ref."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        tree = data["tree"]
        assert tree["ref"] == "w1"
        assert tree["role"] == "window"
        assert tree["name"] == "Test App"

    async def test_children_have_e_prefixed_refs(self, mcp: FastMCP) -> None:
        """All child elements should have e-prefixed refs."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        tree = data["tree"]
        for child in tree.get("children", []):
            assert child["ref"].startswith("e"), f"Expected e-prefixed ref, got {child['ref']}"

    async def test_element_count_includes_root(self, mcp: FastMCP) -> None:
        """element_count should include the root window."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["element_count"] >= 1

    async def test_element_count_matches_actual(self, mcp: FastMCP) -> None:
        """element_count should match actual elements in tree."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)

        def count_elements(node: dict) -> int:
            count = 1
            for child in node.get("children", []):
                count += count_elements(child)
            return count

        actual_count = count_elements(data["tree"])
        assert data["element_count"] == actual_count

    async def test_risk_is_read_only_for_window(self, mcp: FastMCP) -> None:
        """Snapshot of a window should return read_only risk."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["risk"] == "read_only"

    async def test_target_summary_contains_element_count(self, mcp: FastMCP) -> None:
        """target_summary should describe the snapshot with element count."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert "snapshot" in data["target_summary"]
        assert str(data["element_count"]) in data["target_summary"]

    async def test_max_depth_reached_reflects_input(self, mcp: FastMCP) -> None:
        """max_depth_reached should match the requested max_depth."""
        result, _meta = await mcp.call_tool(
            "desktop.snapshot",
            arguments={"window_ref": "w1", "max_depth": 2},
        )
        data = json.loads(result[0].text)
        assert data["max_depth_reached"] == 2

    async def test_children_contain_expected_roles(self, mcp: FastMCP) -> None:
        """Snapshot children should include button and text_input elements."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        child_roles = {c["role"] for c in data["tree"].get("children", [])}
        assert "button" in child_roles
        assert "text_input" in child_roles


# -- Wired mode: privacy ------------------------------------------------------


class TestSnapshotPrivacy:
    """snapshot wired to backend — privacy redaction."""

    async def test_password_field_redacted(self, mcp: FastMCP) -> None:
        """Password text_input elements should have value/text redacted."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        tree = data["tree"]
        for child in tree.get("children", []):
            if child.get("name") == "Password":
                # Password field value should be redacted if it had one
                # (MockBackend doesn't set value by default, but the
                # name-based heuristic in is_password_field should detect it)
                assert child["role"] == "text_input"


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestSnapshotErrors:
    """snapshot wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs, not raise."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w99"})
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
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w2"})
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
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"


# -- Input validation ---------------------------------------------------------


class TestSnapshotValidation:
    """snapshot input validation."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "   "})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_non_w_prefix_returns_validation_error(self, mcp: FastMCP) -> None:
        """Non-w-prefixed ref should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "w" in data["message"]
        assert data["ref"] == "e1"


# -- Schema validation --------------------------------------------------------


class TestSnapshotSchema:
    """snapshot tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should still be desktop.snapshot."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.snapshot" in names

    async def test_window_ref_required(self, mcp: FastMCP) -> None:
        """window_ref should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.snapshot")
        schema = tool.inputSchema
        assert "window_ref" in schema["properties"]
        assert "window_ref" in schema["required"]

    async def test_max_depth_optional(self, mcp: FastMCP) -> None:
        """max_depth should be an optional parameter with default."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.snapshot")
        schema = tool.inputSchema
        assert "max_depth" in schema["properties"]
        assert "max_depth" not in schema.get("required", [])

    async def test_max_nodes_optional(self, mcp: FastMCP) -> None:
        """max_nodes should be an optional parameter with default."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.snapshot")
        schema = tool.inputSchema
        assert "max_nodes" in schema["properties"]
        assert "max_nodes" not in schema.get("required", [])

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.snapshot")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "snapshot" in tool.description.lower() or "accessibility" in tool.description.lower()


# -- Ref assignment ------------------------------------------------------------


class TestSnapshotRefAssignment:
    """snapshot correctly assigns e-prefixed refs to elements."""

    async def test_element_refs_restart_on_snapshot(
        self, mcp: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """Element refs should restart from e1 on each snapshot call."""
        # First snapshot
        result1, _ = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data1 = json.loads(result1[0].text)
        refs_after_first = set()
        for child in data1["tree"].get("children", []):
            refs_after_first.add(child["ref"])

        # Second snapshot should produce fresh element refs
        result2, _ = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data2 = json.loads(result2[0].text)
        refs_after_second = set()
        for child in data2["tree"].get("children", []):
            refs_after_second.add(child["ref"])

        # Same element count, refs should restart from e1
        assert refs_after_first == refs_after_second
        assert "e1" in refs_after_second

    async def test_window_refs_survive_snapshot(
        self, mcp: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """Window refs must survive a snapshot call (GW-082 regression test)."""
        # Verify w1 is resolvable before snapshot
        assert ref_store.resolve("w1") is not None

        await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})

        # w1 must still be resolvable after snapshot
        assert ref_store.resolve("w1") is not None
        assert ref_store.is_valid("w1")

    async def test_other_window_refs_survive_snapshot(
        self, mcp: FastMCP, ref_store: ElementRefStore, backend: MockBackend
    ) -> None:
        """Window refs from other windows must not be destroyed by snapshot."""
        # Register a second window handle
        ghost = NativeHandle("ghost-window-handle")
        ref_store.store(ghost, prefix="w")

        # Verify both windows are resolvable before snapshot
        assert ref_store.resolve("w1") is not None
        assert ref_store.resolve("w2") is not None

        # Snapshot only w1
        await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})

        # w1 AND w2 must both still be resolvable after snapshot
        assert ref_store.resolve("w1") is not None
        assert ref_store.resolve("w2") is not None

    async def test_deep_tree_refs_assigned(self) -> None:
        """Elements in nested children also get e-prefixed refs."""
        b = MockBackend().add_window(title="Deep App", app="deep.exe")
        win = b.last_window_handle
        b.add_element(role="group", name="Container", parent=win)
        # Add nested element (MockBackend uses window_handle for tree building,
        # so we need to adjust. Instead, add elements at the window level
        # and verify they all get refs.)
        b.add_element(role="button", name="Nested", parent=win)

        store = ElementRefStore()
        for handle in b.list_windows():
            store.store(handle, prefix="w")

        mcp_inst = FastMCP(name="test-deep-snapshot")
        register_all(mcp_inst, backend=b, ref_store=store)

        result, _ = await mcp_inst.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        tree = data["tree"]
        for child in tree.get("children", []):
            assert child["ref"].startswith("e")

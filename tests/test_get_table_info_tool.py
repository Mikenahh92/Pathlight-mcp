"""Tests for the desktop.get_table_info tool handler (GW-049).

Validates that the wired get_table_info tool:
- Resolves an e-prefixed reference via ElementRefStore.
- Validates existence and staleness via backend.is_valid().
- Delegates to backend.perform_action(handle, GET_TABLE_INFO, ...) for table data.
- Applies safety classification via classify().
- Returns a structured JSON success response with table data.
- Returns structured JSON error for unknown refs (not raises).
- Returns structured JSON error for stale refs.
- Returns structured JSON error for non-table elements.
- Validates input (empty string, negative max_rows, negative max_columns).
- Falls back to static stub response when no backend is provided.
- Supports action parameter with info/read_cell/read_row/read_column sub-commands.
"""

import json
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.errors import ActionNotSupportedError
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and a table element."""
    b = MockBackend().add_window(title="Test Window", app="TestApp", focused=True)
    window_handle = b.list_windows()[0]
    b.add_table(
        name="Contacts",
        headers=["Name", "Email", "Phone"],
        rows=[
            ["Alice", "alice@example.com", "555-0001"],
            ["Bob", "bob@example.com", "555-0002"],
            ["Charlie", "charlie@example.com", "555-0003"],
        ],
        parent=window_handle,
    )
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
    mcp = FastMCP(name="test-get-table-info")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-get-table-info-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestGetTableInfoStub:
    """get_table_info returns static stub response when no backend is provided."""

    async def test_stub_returns_json(self, stub_mcp: FastMCP) -> None:
        """Without a backend, get_table_info should return a static JSON object."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert "row_count" in data
        assert "column_count" in data
        assert "headers" in data
        assert "rows" in data

    async def test_stub_returns_element_ref(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the element_ref value."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e42"},
        )
        data = json.loads(result[0].text)
        assert data["element_ref"] == "e42"


# -- Wired mode: success ------------------------------------------------------


class TestGetTableInfoSuccess:
    """get_table_info wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True and table data."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "e1"
        assert data["row_count"] == 3
        assert data["column_count"] == 3
        assert data["headers"] == ["Name", "Email", "Phone"]
        assert len(data["rows"]) == 3

    async def test_returns_cell_data(self, mcp: FastMCP) -> None:
        """Should return cell values with row/column indices."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        # First row, first column
        cell = data["rows"][0][0]
        assert cell["row"] == 0
        assert cell["column"] == 0
        assert cell["value"] == "Alice"

    async def test_returns_all_rows(self, mcp: FastMCP) -> None:
        """Should return all rows in the table."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert len(data["rows"]) == 3
        assert data["rows"][1][0]["value"] == "Bob"
        assert data["rows"][2][0]["value"] == "Charlie"

    async def test_returns_all_columns(self, mcp: FastMCP) -> None:
        """Should return all columns per row."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert len(data["rows"][0]) == 3
        assert data["rows"][0][1]["value"] == "alice@example.com"
        assert data["rows"][0][2]["value"] == "555-0001"

    async def test_delegates_to_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Calling get_table_info should record get_table_info in action log."""
        await mcp.call_tool("desktop.get_table_info", arguments={"element_ref": "e1"})
        assert len(backend.action_log) >= 1
        entry = backend.action_log[-1]
        assert entry["action"] == "get_table_info"

    async def test_success_response_has_risk_level(self, mcp: FastMCP) -> None:
        """get_table_info should return a risk level from safety classification."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert "risk" in data
        assert data["risk"] in ("read_only", "interaction", "sensitive")

    async def test_target_summary_describes_action(self, mcp: FastMCP) -> None:
        """target_summary should describe the action performed."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert "table data retrieval" in data["target_summary"]

    async def test_response_includes_name(self, mcp: FastMCP) -> None:
        """Response should include element name when available."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data.get("name") == "Contacts"


# -- Wired mode: max_rows / max_columns limits --------------------------------


class TestGetTableInfoLimits:
    """get_table_info respects max_rows and max_columns parameters."""

    async def test_max_rows_limits_output(self, mcp: FastMCP) -> None:
        """Should limit returned rows to max_rows."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "max_rows": 2},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert len(data["rows"]) == 2
        # row_count still reflects total
        assert data["row_count"] == 3

    async def test_max_columns_limits_output(self, mcp: FastMCP) -> None:
        """Should limit returned columns to max_columns."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "max_columns": 2},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert len(data["headers"]) == 2
        assert len(data["rows"][0]) == 2
        # column_count still reflects total
        assert data["column_count"] == 3


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestGetTableInfoErrors:
    """get_table_info wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs, not raise."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
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
        ghost = NativeHandle("ghost-table-handle")
        ref_store.store(ghost, prefix="e")
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e3"},
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
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e1"

    async def test_non_table_element_returns_action_not_supported(
        self,
        mcp: FastMCP,
    ) -> None:
        """Should return action_not_supported for non-table elements."""
        # e2 is a button element, not a table
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e2"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "action_not_supported"
        assert "e2" in data["message"]

    async def test_action_not_supported_from_backend_returns_structured_error(
        self,
        mcp: FastMCP,
        backend: MockBackend,
    ) -> None:
        """Should return action_not_supported error when backend raises."""
        from pathlight_mcp.backends.types import DesktopAction

        original_perform_action = backend.perform_action

        def _patched_perform_action(handle, action, **kwargs):
            if action == DesktopAction.GET_TABLE_INFO:
                raise ActionNotSupportedError("No table pattern")
            return original_perform_action(handle, action, **kwargs)

        with patch.object(
            backend,
            "perform_action",
            side_effect=_patched_perform_action,
        ):
            result, _meta = await mcp.call_tool(
                "desktop.get_table_info",
                arguments={"element_ref": "e1"},
            )
        data = json.loads(result[0].text)
        assert data["error"] == "action_not_supported"
        assert "e1" in data["message"]
        assert data["ref"] == "e1"


# -- Input validation ---------------------------------------------------------


class TestGetTableInfoValidation:
    """get_table_info input validation."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": ""},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only string should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "   "},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_negative_max_rows_returns_validation_error(self, mcp: FastMCP) -> None:
        """Negative max_rows should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "max_rows": -1},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "max_rows" in data["message"]

    async def test_negative_max_columns_returns_validation_error(self, mcp: FastMCP) -> None:
        """Negative max_columns should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "max_columns": -5},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "max_columns" in data["message"]


# -- Schema validation --------------------------------------------------------


class TestGetTableInfoSchema:
    """get_table_info tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.get_table_info."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.get_table_info" in names

    async def test_element_ref_required(self, mcp: FastMCP) -> None:
        """element_ref should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_table_info")
        schema = tool.inputSchema
        assert "element_ref" in schema["properties"]
        assert "element_ref" in schema["required"]

    async def test_max_rows_has_default(self, mcp: FastMCP) -> None:
        """max_rows should have a default value of 100."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_table_info")
        schema = tool.inputSchema
        assert "max_rows" in schema["properties"]
        assert schema["properties"]["max_rows"].get("default") == 100

    async def test_max_columns_has_default(self, mcp: FastMCP) -> None:
        """max_columns should have a default value of 50."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_table_info")
        schema = tool.inputSchema
        assert "max_columns" in schema["properties"]
        assert schema["properties"]["max_columns"].get("default") == 50

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.get_table_info")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "table" in tool.description.lower()


# -- Empty table edge case ----------------------------------------------------


class TestGetTableInfoEmptyTable:
    """get_table_info handles empty tables gracefully."""

    @pytest.fixture()
    def empty_backend(self) -> MockBackend:
        """Backend with an empty table (headers only, no data rows)."""
        b = MockBackend().add_window(title="Empty", app="TestApp")
        w = b.list_windows()[0]
        b.add_table(
            name="EmptyTable",
            headers=["Col1", "Col2"],
            rows=[],
            parent=w,
        )
        return b

    @pytest.fixture()
    def empty_ref_store(self, empty_backend: MockBackend) -> ElementRefStore:
        store = ElementRefStore()
        w = empty_backend.list_windows()[0]
        for handle in empty_backend.find_elements(w):
            store.store(handle, prefix="e")
        return store

    @pytest.fixture()
    def empty_mcp(
        self,
        empty_backend: MockBackend,
        empty_ref_store: ElementRefStore,
    ) -> FastMCP:
        mcp = FastMCP(name="test-empty-table")
        register_all(mcp, backend=empty_backend, ref_store=empty_ref_store)
        return mcp

    async def test_empty_table_returns_zero_rows(self, empty_mcp: FastMCP) -> None:
        """Empty table should have row_count=0 and empty rows list."""
        result, _meta = await empty_mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["row_count"] == 0
        assert data["column_count"] == 2
        assert data["headers"] == ["Col1", "Col2"]
        assert data["rows"] == []


# -- Table with None values ---------------------------------------------------


class TestGetTableInfoNullValues:
    """get_table_info handles None cell values correctly."""

    @pytest.fixture()
    def null_backend(self) -> MockBackend:
        """Backend with a table containing None values."""
        b = MockBackend().add_window(title="NullTest", app="TestApp")
        w = b.list_windows()[0]
        b.add_table(
            name="SparseTable",
            headers=["A", "B"],
            rows=[
                ["val1", None],
                [None, "val2"],
            ],
            parent=w,
        )
        return b

    @pytest.fixture()
    def null_ref_store(self, null_backend: MockBackend) -> ElementRefStore:
        store = ElementRefStore()
        w = null_backend.list_windows()[0]
        for handle in null_backend.find_elements(w):
            store.store(handle, prefix="e")
        return store

    @pytest.fixture()
    def null_mcp(
        self,
        null_backend: MockBackend,
        null_ref_store: ElementRefStore,
    ) -> FastMCP:
        mcp = FastMCP(name="test-null-table")
        register_all(mcp, backend=null_backend, ref_store=null_ref_store)
        return mcp

    async def test_none_cell_value_preserved(self, null_mcp: FastMCP) -> None:
        """None cell values should be returned as null in JSON."""
        result, _meta = await null_mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["rows"][0][1]["value"] is None
        assert data["rows"][1][0]["value"] is None
        assert data["rows"][0][0]["value"] == "val1"
        assert data["rows"][1][1]["value"] == "val2"


# -- Action-based API tests ---------------------------------------------------


class TestGetTableInfoActionParameter:
    """get_table_info supports action parameter with sub-commands."""

    async def test_info_action_returns_full_table(self, mcp: FastMCP) -> None:
        """action=info should return full table data."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "action": "info"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["action"] == "info"
        assert "row_count" in data
        assert "column_count" in data
        assert "headers" in data
        assert "rows" in data

    async def test_read_cell_action_returns_single_cell(self, mcp: FastMCP) -> None:
        """action=read_cell should return a single cell value."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "action": "read_cell", "row": 0, "column": 0},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["action"] == "read_cell"
        assert data["row"] == 0
        assert data["column"] == 0
        assert data["value"] == "Alice"

    async def test_read_row_action_returns_row_cells(self, mcp: FastMCP) -> None:
        """action=read_row should return all cells in the row."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "action": "read_row", "row": 1},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["action"] == "read_row"
        assert data["row"] == 1
        assert len(data["cells"]) == 3
        assert data["cells"][0]["value"] == "Bob"

    async def test_read_column_action_returns_column_cells(self, mcp: FastMCP) -> None:
        """action=read_column should return all cells in the column."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "action": "read_column", "column": 1},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["action"] == "read_column"
        assert data["column"] == 1
        assert data["header"] == "Email"
        assert len(data["cells"]) == 3

    async def test_invalid_action_returns_validation_error(self, mcp: FastMCP) -> None:
        """Invalid action should return validation error."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "action": "delete"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "action" in data["message"]

    async def test_negative_row_returns_validation_error(self, mcp: FastMCP) -> None:
        """Negative row index should return validation error."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "row": -1},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "row" in data["message"]

    async def test_negative_column_returns_validation_error(self, mcp: FastMCP) -> None:
        """Negative column index should return validation error."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "column": -1},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "column" in data["message"]


# -- Safety classification tests ----------------------------------------------


class TestGetTableInfoSafetyClassification:
    """get_table_info applies safety classification correctly."""

    async def test_table_role_is_read_only(self, mcp: FastMCP) -> None:
        """Table element should be classified as read_only risk."""
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "read_only"

    async def test_classify_uses_get_table_info_action(self, mcp: FastMCP) -> None:
        """Safety classify should be called with 'get_table_info' action."""
        # This is verified by the response including risk metadata
        result, _meta = await mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert "risk" in data
        # Table role is READ_ONLY in ROLE_RISK_MAP
        assert data["risk"] == "read_only"


# -- Privacy / password field tests -------------------------------------------


class TestGetTableInfoPrivacy:
    """get_table_info safety classification handles password field roles."""

    @pytest.fixture()
    def pw_backend(self) -> MockBackend:
        """Backend with a table containing a password column."""
        b = MockBackend().add_window(title="Login", app="TestApp")
        w = b.list_windows()[0]
        b.add_table(
            name="Credentials",
            headers=["Username", "Password"],
            rows=[
                ["admin", "s3cret"],
                ["user", "p@ssw0rd"],
            ],
            parent=w,
        )
        return b

    @pytest.fixture()
    def pw_ref_store(self, pw_backend: MockBackend) -> ElementRefStore:
        store = ElementRefStore()
        w = pw_backend.list_windows()[0]
        for handle in pw_backend.find_elements(w):
            store.store(handle, prefix="e")
        return store

    @pytest.fixture()
    def pw_mcp(self, pw_backend: MockBackend, pw_ref_store: ElementRefStore) -> FastMCP:
        mcp = FastMCP(name="test-pw-table")
        register_all(mcp, backend=pw_backend, ref_store=pw_ref_store)
        return mcp

    async def test_password_cell_values_returned_as_is(
        self,
        pw_mcp: FastMCP,
    ) -> None:
        """Cell values including passwords are returned from backend as-is.

        The get_table_info tool reads raw cell data via perform_action.
        Privacy redaction is applied at the snapshot/redact_element level,
        not at the table data retrieval level (read-only operation).
        """
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        # Verify table data is returned correctly
        assert data["rows"][0][1]["value"] == "s3cret"
        assert data["rows"][1][1]["value"] == "p@ssw0rd"

    async def test_password_table_risk_is_read_only(
        self,
        pw_mcp: FastMCP,
    ) -> None:
        """Table element with password column headers is still read_only."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1"},
        )
        data = json.loads(result[0].text)
        # Table role is always READ_ONLY regardless of content
        assert data["risk"] == "read_only"

    async def test_read_cell_password_column(self, pw_mcp: FastMCP) -> None:
        """Reading a password cell via read_cell returns the value."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "action": "read_cell", "row": 0, "column": 1},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["value"] == "s3cret"

    async def test_read_column_password(self, pw_mcp: FastMCP) -> None:
        """Reading the password column via read_column returns all values."""
        result, _meta = await pw_mcp.call_tool(
            "desktop.get_table_info",
            arguments={"element_ref": "e1", "action": "read_column", "column": 1},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["header"] == "Password"
        assert len(data["cells"]) == 2

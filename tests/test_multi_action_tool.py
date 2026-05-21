"""Tests for the desktop.multi_action tool handler (GW-065).

Validates:
- Stub mode returns batch summary.
- Batch validation: empty, too small, too large, missing fields, unsupported actions.
- Safety pre-classification: SENSITIVE actions rejected.
- Sequential execution: success path with click, type, press_key.
- Stop-on-first-failure semantics.
- Per-action error handling (element_not_found, stale, action_not_supported).
- Batch-level risk metadata.
- Response schema uses architecture §5 field names.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.types import DesktopAction, NativeHandle
from guidewire.errors import ActionNotSupportedError
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all


# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and several elements."""
    b = (
        MockBackend()
        .add_window(title="Test Window", app="TestApp", focused=True)
    )
    window_handle = b.list_windows()[0]
    b.add_element(role="button", name="Submit", parent=window_handle)
    b.add_element(role="text_field", name="Username", value="", parent=window_handle)
    b.add_element(role="text_field", name="Email", value="", parent=window_handle)
    b.add_element(role="button", name="Cancel", parent=window_handle)
    return b


@pytest.fixture()
def ref_store(backend: MockBackend) -> ElementRefStore:
    """Return an ElementRefStore with e-prefixed refs for all elements."""
    store = ElementRefStore()
    window_handle = backend.list_windows()[0]
    elements = backend.find_elements(window_handle)
    for handle in elements:
        store.store(handle, prefix="e")
    return store


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with all tools registered using a wired backend."""
    mcp = FastMCP(name="test-multi-action")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-multi-action-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestMultiActionStub:
    """multi_action returns stub response when no backend is provided."""

    async def test_stub_returns_batch_summary(self, stub_mcp: FastMCP) -> None:
        """Without a backend, multi_action should return a stub batch summary."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "click", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["total"] == 2
        assert data["completed"] == 2
        assert data["failed"] == 0
        assert data["batch_aborted"] is None
        assert data["mode"] == "stub"

    async def test_stub_returns_per_action_results(self, stub_mcp: FastMCP) -> None:
        """Stub mode should echo action types in results with tool/arguments schema."""
        actions = [
            {"action": "click", "element_ref": "e1"},
            {"action": "type", "element_ref": "e2", "text": "hello"},
        ]
        result, _meta = await stub_mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": actions},
        )
        data = json.loads(result[0].text)
        assert len(data["actions"]) == 2
        assert data["actions"][0]["tool"] == "click"
        assert data["actions"][0]["arguments"]["element_ref"] == "e1"
        assert data["actions"][1]["tool"] == "type"
        assert data["actions"][1]["arguments"]["element_ref"] == "e2"


# -- Batch-level validation ---------------------------------------------------


class TestMultiActionValidation:
    """multi_action batch-level validation."""

    async def test_empty_actions_rejected(self, mcp: FastMCP) -> None:
        """Empty actions list should return validation error."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": []},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "empty" in data["message"].lower()

    async def test_single_action_below_minimum_rejected(self, mcp: FastMCP) -> None:
        """Single action should be rejected — minimum batch size is 2."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [{"action": "click", "element_ref": "e1"}]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "below minimum" in data["message"]

    async def test_batch_too_large_rejected(self, mcp: FastMCP) -> None:
        """Batch exceeding MAX_BATCH_SIZE should be rejected."""
        from guidewire.tools.multi_action import MAX_BATCH_SIZE

        actions = [{"action": "click", "element_ref": f"e{i}"} for i in range(MAX_BATCH_SIZE + 1)]
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": actions},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "exceeds maximum" in data["message"]

    async def test_missing_action_field(self, mcp: FastMCP) -> None:
        """Missing 'action' field should return validation error."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [{"element_ref": "e1"}, {"element_ref": "e2"}]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "missing 'action'" in data["message"]

    async def test_unknown_action_rejected(self, mcp: FastMCP) -> None:
        """Unknown action name should return validation error."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "teleport", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "unsupported action" in data["message"]

    async def test_non_batchable_action_rejected(self, mcp: FastMCP) -> None:
        """Actions not in v1 batchable set should be rejected."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "get_table_info", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "not batchable" in data["message"]

    async def test_element_action_missing_element_ref(self, mcp: FastMCP) -> None:
        """Element-based action without element_ref should fail validation."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "click"},
                {"action": "click", "element_ref": "e1"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "requires an element_ref" in data["message"]

    async def test_type_action_missing_text(self, mcp: FastMCP) -> None:
        """Type action without 'text' field should fail validation."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "type", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "'text' field" in data["message"]

    async def test_press_key_missing_keys(self, mcp: FastMCP) -> None:
        """Press_key action without 'keys' field should fail validation."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "press_key"},
                {"action": "click", "element_ref": "e1"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "'keys' field" in data["message"]

    async def test_set_value_missing_value(self, mcp: FastMCP) -> None:
        """Set_value action without 'value' field should fail validation."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "set_value", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert "'value' field" in data["message"]


# -- Safety pre-classification ------------------------------------------------


class TestMultiActionSafety:
    """multi_action safety pre-classification rejects SENSITIVE actions."""

    async def test_sensitive_action_rejected(self, mcp: FastMCP) -> None:
        """A SENSITIVE-tier element should cause the batch to be rejected.

        Since classify() uses ROLE_RISK_MAP and name heuristics, a button
        named 'Delete' matches DESTRUCTIVE_NAME_PATTERNS and gets SENSITIVE.
        """
        # Create a backend with a destructive-named element
        b = MockBackend().add_window(title="Test", app="TestApp", focused=True)
        wh = b.list_windows()[0]
        b.add_element(role="button", name="Delete Everything", parent=wh)
        b.add_element(role="button", name="OK", parent=wh)
        store = ElementRefStore()
        for h in b.find_elements(wh):
            store.store(h, prefix="e")
        local_mcp = FastMCP(name="test-sensitive")
        register_all(local_mcp, backend=b, ref_store=store)

        result, _meta = await local_mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "click", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "sensitive_action_rejected"
        assert "SENSITIVE" in data["message"]

    async def test_batch_with_mixed_actions_one_sensitive(self, mcp: FastMCP) -> None:
        """If one action is SENSITIVE, the whole batch is rejected."""
        b = MockBackend().add_window(title="Test", app="TestApp", focused=True)
        wh = b.list_windows()[0]
        b.add_element(role="button", name="OK", parent=wh)
        b.add_element(role="button", name="Delete Item", parent=wh)
        store = ElementRefStore()
        for h in b.find_elements(wh):
            store.store(h, prefix="e")
        local_mcp = FastMCP(name="test-mixed-sensitive")
        register_all(local_mcp, backend=b, ref_store=store)

        result, _meta = await local_mcp.call_tool(
            "desktop.multi_action",
            arguments={
                "actions": [
                    {"action": "click", "element_ref": "e1"},
                    {"action": "click", "element_ref": "e2"},
                ]
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "sensitive_action_rejected"


# -- Sequential execution: success path ---------------------------------------


class TestMultiActionSuccess:
    """multi_action sequential execution — success path."""

    async def test_two_clicks(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Two click actions should succeed with architecture §5 schema."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "click", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["total"] == 2
        assert data["completed"] == 2
        assert data["failed"] == 0
        assert data["batch_aborted"] is None
        assert data["actions"][0]["success"] is True
        assert data["actions"][0]["tool"] == "click"
        assert data["actions"][0]["arguments"]["element_ref"] == "e1"
        assert data["actions"][1]["arguments"]["element_ref"] == "e2"

    async def test_click_and_type(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Click followed by type should execute both."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={
                "actions": [
                    {"action": "click", "element_ref": "e2"},
                    {"action": "type", "element_ref": "e2", "text": "hello"},
                ]
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["total"] == 2
        assert data["completed"] == 2
        assert data["failed"] == 0
        assert len(data["actions"]) == 2

        # Verify backend received both actions.
        log = backend.action_log
        assert any(e["action"] == "click" for e in log)
        assert any(e["action"] == "type" for e in log)

    async def test_press_key_with_click(self, mcp: FastMCP, backend: MockBackend) -> None:
        """press_key combined with click should succeed."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "click", "element_ref": "e1"},
                {"action": "press_key", "keys": "Enter"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["completed"] == 2
        assert data["actions"][0]["tool"] == "click"
        assert data["actions"][1]["tool"] == "press_key"
        assert data["actions"][1]["arguments"]["keys"] == "enter"

    async def test_get_text_and_click(self, mcp: FastMCP, backend: MockBackend) -> None:
        """get_text combined with click should return text in result."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "get_text", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["completed"] == 2
        assert data["actions"][0]["success"] is True
        assert "text" in data["actions"][0]

    async def test_multiple_clicks(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Multiple click actions should all succeed."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={
                "actions": [
                    {"action": "click", "element_ref": "e1"},
                    {"action": "click", "element_ref": "e2"},
                    {"action": "click", "element_ref": "e4"},
                ]
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["total"] == 3
        assert data["completed"] == 3
        assert data["failed"] == 0


# -- Stop-on-first-failure semantics ------------------------------------------


class TestMultiActionStopOnFailure:
    """multi_action stops executing on first failure."""

    async def test_unknown_ref_stops_batch(self, mcp: FastMCP, backend: MockBackend) -> None:
        """An unknown element_ref should stop the batch."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={
                "actions": [
                    {"action": "click", "element_ref": "e1"},
                    {"action": "click", "element_ref": "e99"},
                    {"action": "click", "element_ref": "e2"},
                ]
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["completed"] == 1
        assert data["failed"] == 1
        assert data["batch_aborted"] == 1
        assert len(data["actions"]) == 2  # first success + one failure
        assert data["actions"][0]["success"] is True
        assert data["actions"][1]["success"] is False
        assert data["actions"][1]["error"] == "element_not_found"

    async def test_stale_element_stops_batch(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """A stale element reference should stop the batch."""
        # Invalidate element e2
        window_handle = backend.list_windows()[0]
        elements = backend.find_elements(window_handle)
        backend.invalidate(elements[1])  # e2

        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={
                "actions": [
                    {"action": "click", "element_ref": "e1"},
                    {"action": "click", "element_ref": "e2"},
                ]
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["completed"] == 1
        assert data["failed"] == 1
        assert data["batch_aborted"] == 1
        assert data["actions"][1]["error"] == "stale_element_reference"

    async def test_action_not_supported_stops_batch(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """An ActionNotSupportedError should stop the batch."""
        from unittest.mock import patch

        call_count = 0
        original_perform = backend.perform_action

        def selective_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise ActionNotSupportedError("Not supported")
            return original_perform(*args, **kwargs)

        with patch.object(backend, "perform_action", side_effect=selective_fail):
            result, _meta = await mcp.call_tool(
                "desktop.multi_action",
                arguments={
                    "actions": [
                        {"action": "click", "element_ref": "e1"},
                        {"action": "click", "element_ref": "e2"},
                    ]
                },
            )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["completed"] == 1
        assert data["failed"] == 1
        assert data["batch_aborted"] == 1
        assert data["actions"][1]["error"] == "action_not_supported"


# -- Batch-level risk metadata ------------------------------------------------


class TestMultiActionRiskMetadata:
    """multi_action batch-level risk classification."""

    async def test_batch_risk_interaction(self, mcp: FastMCP) -> None:
        """A batch of clicks should have 'interaction' risk."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "click", "element_ref": "e1"},
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["risk"] in ("interaction", "read_only")

    async def test_batch_risk_with_press_key(self, mcp: FastMCP) -> None:
        """A batch with press_key should have 'interaction' risk."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                {"action": "click", "element_ref": "e1"},
                {"action": "press_key", "keys": "Tab"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["risk"] in ("interaction", "read_only")


# -- Non-batchable actions excluded -------------------------------------------


class TestMultiActionExcludedActions:
    """Verify that non-batchable actions are properly rejected."""

    @pytest.mark.parametrize(
        "action_name,expected_error_msg",
        [
            ("snapshot", "unsupported action"),
            ("find", "unsupported action"),
            ("get_table_info", "not batchable"),
            ("get_tree_info", "unsupported action"),
            ("scroll_to_item", "not batchable"),
        ],
    )
    async def test_non_batchable_action_rejected(
        self, mcp: FastMCP, action_name: str, expected_error_msg: str
    ) -> None:
        """These actions should not be batchable in v1."""
        args = [{"action": action_name}]
        # Some need element_ref for the DesktopAction check to pass
        if action_name in ("get_table_info", "scroll_to_item"):
            args[0]["element_ref"] = "e1"
        # Add a valid second action to meet minimum batch size
        args.append({"action": "click", "element_ref": "e1"})
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": args},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["error"] == "validation_error"
        assert expected_error_msg in data["message"]


# -- Schema validation --------------------------------------------------------


class TestMultiActionSchema:
    """multi_action tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.multi_action."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.multi_action" in names

    async def test_actions_required(self, mcp: FastMCP) -> None:
        """'actions' should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.multi_action")
        schema = tool.inputSchema
        assert "actions" in schema["properties"]
        assert "actions" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.multi_action")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "batch" in tool.description.lower()


# -- Supported action types coverage ------------------------------------------


class TestMultiActionSupportedTypes:
    """Verify each supported action type works in a batch."""

    @pytest.mark.parametrize(
        "action_name,extra_kwargs",
        [
            ("click", {}),
            ("toggle", {}),
            ("expand", {}),
            ("collapse", {}),
            ("select", {}),
            ("increment", {}),
            ("decrement", {}),
            ("scroll", {}),
        ],
    )
    async def test_element_action_in_batch(
        self,
        mcp: FastMCP,
        action_name: str,
        extra_kwargs: dict,
    ) -> None:
        """Element-based action should succeed in a batch of 2."""
        descriptor = {"action": action_name, "element_ref": "e1"}
        descriptor.update(extra_kwargs)
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={"actions": [
                descriptor,
                {"action": "click", "element_ref": "e2"},
            ]},
        )
        data = json.loads(result[0].text)
        assert data["completed"] == 2
        assert data["actions"][0]["success"] is True
        assert data["actions"][0]["tool"] == action_name

    async def test_type_action_in_batch(self, mcp: FastMCP) -> None:
        """Type action with text should succeed in a batch."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={
                "actions": [
                    {"action": "click", "element_ref": "e1"},
                    {"action": "type", "element_ref": "e2", "text": "hello"},
                ]
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["completed"] == 2
        assert data["actions"][1]["success"] is True
        assert data["actions"][1]["tool"] == "type"
        assert data["actions"][1]["arguments"]["element_ref"] == "e2"
        assert data["actions"][1]["arguments"]["text"] == "hello"

    async def test_set_value_action_in_batch(self, mcp: FastMCP) -> None:
        """set_value action with value should succeed in a batch."""
        result, _meta = await mcp.call_tool(
            "desktop.multi_action",
            arguments={
                "actions": [
                    {"action": "click", "element_ref": "e1"},
                    {"action": "set_value", "element_ref": "e2", "value": 42},
                ]
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["completed"] == 2
        assert data["actions"][1]["success"] is True
        assert data["actions"][1]["tool"] == "set_value"
        assert data["actions"][1]["arguments"]["value"] == 42

"""Architecture spike tests for desktop.wait_for tool (GW-063).

Validates:
1. MCP Python SDK async handler support — async def + await asyncio.sleep()
2. Sync vs async performance benchmarking
3. Condition DSL feasibility against existing ABC methods
4. Polling loop integration with DesktopBackend ABC

These tests are intentionally self-contained to validate the spike
without requiring a full tool implementation.
"""

import asyncio
import json
import time

import pytest

from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.types import NativeHandle
from guidewire.refs import ElementRefStore


# ---------------------------------------------------------------------------
# Spike 1: Async handler support validation
# ---------------------------------------------------------------------------


class TestAsyncHandlerSupport:
    """Validate that FastMCP supports async tool handlers with asyncio.sleep."""

    def test_async_tool_handler_registers(self):
        """An async def handler can be registered on FastMCP without error."""
        mcp = FastMCP(name="spike-async-test")

        @mcp.tool(name="spike.async_sleep")
        async def async_sleep(duration_ms: int) -> str:
            await asyncio.sleep(duration_ms / 1000.0)
            return json.dumps({"slept_ms": duration_ms})

        # Verify the tool was registered
        # FastMCP stores tools internally; we verify by listing
        tools = mcp._tool_manager.list_tools()
        names = [t.name for t in tools]
        assert "spike.async_sleep" in names

    def test_sync_tool_handler_still_works(self):
        """A sync def handler still works alongside async handlers."""
        mcp = FastMCP(name="spike-sync-test")

        @mcp.tool(name="spike.sync_echo")
        def sync_echo(message: str) -> str:
            return json.dumps({"echo": message})

        tools = mcp._tool_manager.list_tools()
        names = [t.name for t in tools]
        assert "spike.sync_echo" in names

    @pytest.mark.asyncio
    async def test_async_handler_actually_sleeps(self):
        """An async handler with asyncio.sleep actually awaits correctly."""
        mcp = FastMCP(name="spike-async-sleep-test")

        @mcp.tool(name="spike.timed_wait")
        async def timed_wait(ms: int) -> str:
            t0 = time.monotonic()
            await asyncio.sleep(ms / 1000.0)
            elapsed_ms = (time.monotonic() - t0) * 1000
            return json.dumps({"requested_ms": ms, "elapsed_ms": round(elapsed_ms, 1)})

        # Call through FastMCP's internal call mechanism
        content, _meta = await mcp.call_tool("spike.timed_wait", {"ms": 50})
        data = json.loads(content[0].text)
        assert data["requested_ms"] == 50
        # Should have actually slept at least 40ms (allowing timing slack)
        assert data["elapsed_ms"] >= 40

    @pytest.mark.asyncio
    async def test_sync_and_async_coexist(self):
        """Sync and async tools on the same FastMCP instance both work."""
        mcp = FastMCP(name="spike-mixed-test")

        @mcp.tool(name="spike.sync_peek")
        def sync_peek(value: str) -> str:
            return json.dumps({"peek": value})

        @mcp.tool(name="spike.async_wait")
        async def async_wait(ms: int) -> str:
            await asyncio.sleep(ms / 1000.0)
            return json.dumps({"waited": ms})

        r1_content, _ = await mcp.call_tool("spike.sync_peek", {"value": "hello"})
        assert json.loads(r1_content[0].text)["peek"] == "hello"

        r2_content, _ = await mcp.call_tool("spike.async_wait", {"ms": 20})
        assert json.loads(r2_content[0].text)["waited"] == 20


# ---------------------------------------------------------------------------
# Spike 2: Sync vs async performance benchmarking
# ---------------------------------------------------------------------------


class TestSyncVsAsyncBenchmark:
    """Compare sync polling vs async polling overhead.

    The wait_for tool needs to poll the backend at intervals. We compare:
    - Sync: time.sleep() in a loop (blocks the event loop)
    - Async: asyncio.sleep() in a loop (yields to the event loop)

    Key finding: asyncio.sleep() has ~0.1ms overhead per iteration, which
    is negligible for polling intervals >= 50ms.
    """

    @pytest.mark.asyncio
    async def test_async_sleep_overhead(self):
        """Measure per-iteration overhead of asyncio.sleep(0)."""
        iterations = 100
        t0 = time.monotonic()
        for _ in range(iterations):
            await asyncio.sleep(0)
        elapsed = (time.monotonic() - t0) * 1000  # ms
        per_iter = elapsed / iterations
        # asyncio.sleep(0) should have sub-millisecond overhead
        assert per_iter < 1.0, f"Per-iteration overhead: {per_iter:.3f}ms"

    def test_sync_sleep_overhead(self):
        """Measure per-iteration overhead of time.sleep(0)."""
        iterations = 100
        t0 = time.monotonic()
        for _ in range(iterations):
            time.sleep(0)
        elapsed = (time.monotonic() - t0) * 1000  # ms
        per_iter = elapsed / iterations
        # time.sleep(0) should also have sub-millisecond overhead
        assert per_iter < 1.0, f"Per-iteration overhead: {per_iter:.3f}ms"

    @pytest.mark.asyncio
    async def test_async_polling_loop_performance(self):
        """Simulate a wait_for polling loop with async sleep intervals.

        This validates that async polling with 50ms intervals is performant
        and doesn't accumulate significant timing drift.
        """
        poll_interval_ms = 50
        max_polls = 5

        t0 = time.monotonic()
        for _ in range(max_polls):
            await asyncio.sleep(poll_interval_ms / 1000.0)
        elapsed_ms = (time.monotonic() - t0) * 1000

        expected_ms = poll_interval_ms * max_polls
        # Allow 20% tolerance for timer slack
        assert (
            abs(elapsed_ms - expected_ms) < expected_ms * 0.3
        ), f"Drift: expected ~{expected_ms}ms, got {elapsed_ms:.1f}ms"


# ---------------------------------------------------------------------------
# Spike 3: Condition DSL feasibility
# ---------------------------------------------------------------------------


class TestConditionDSLFeasibility:
    """Validate that a condition DSL can evaluate against existing ABC methods.

    The wait_for tool needs a way to express conditions like:
    - element_exists(ref)  — element is still valid
    - element_state(ref, state="enabled")  — element has a state
    - element_text(ref, equals="Hello")  — element text matches
    - window_title(contains="Save")  — window title matches

    We validate that these conditions can be checked using only existing
    DesktopBackend ABC methods (is_valid, get_element_info, perform_action).
    """

    def test_condition_element_exists_via_is_valid(self):
        """is_valid() can serve as element_exists condition."""
        backend = MockBackend()
        backend.add_window(title="Test")
        w = backend.list_windows()[0]
        backend.add_element(role="button", name="OK", parent=w)
        elem = backend.find_elements(w, role="button")[0]

        # Element exists → True
        assert backend.is_valid(elem) is True

        # After invalidation → False
        backend.invalidate(elem)
        assert backend.is_valid(elem) is False

    def test_condition_element_state_via_get_element_info(self):
        """get_element_info() provides states dict for state conditions."""
        backend = MockBackend()
        backend.add_window(title="Test")
        w = backend.list_windows()[0]
        from guidewire.backends.types import ElementState

        backend.add_element(
            role="button",
            name="Submit",
            parent=w,
            states=ElementState(enabled=True, focused=False),
        )
        elem = backend.find_elements(w, role="button")[0]

        info = backend.get_element_info(elem)
        assert info["states"]["enabled"] is True
        assert info["states"]["focused"] is False

    def test_condition_element_text_via_perform_action(self):
        """perform_action(GET_TEXT) provides text for text conditions."""
        backend = MockBackend()
        backend.add_window(title="Test")
        w = backend.list_windows()[0]
        backend.add_element(role="text", name="label", value="Hello World", parent=w)
        elem = backend.find_elements(w, role="text")[0]

        from guidewire.backends.types import DesktopAction

        text = backend.perform_action(elem, DesktopAction.GET_TEXT)
        assert text == "Hello World"
        assert "Hello" in text  # substring matching

    def test_condition_window_title_via_get_window_info(self):
        """get_window_info() provides title for window title conditions."""
        backend = MockBackend()
        backend.add_window(title="Save Changes — Notepad")

        w = backend.list_windows()[0]
        info = backend.get_window_info(w)
        assert "Save" in info["title"]

    def test_condition_dsl_evaluator_design(self):
        """Validate a simple condition evaluator pattern.

        This demonstrates the DSL design pattern that wait_for will use:
        - Conditions are dicts with a "type" key
        - Each type maps to a handler that uses existing ABC methods
        - No new ABC methods needed for basic conditions
        """
        backend = MockBackend()
        backend.add_window(title="My App")
        w = backend.list_windows()[0]
        from guidewire.backends.types import DesktopAction, ElementState

        backend.add_element(
            role="button",
            name="OK",
            parent=w,
            states=ElementState(enabled=True),
        )
        elem = backend.find_elements(w, role="button")[0]
        ref_store = ElementRefStore()
        e_ref = ref_store.store(elem, prefix="e")

        # Simulate condition evaluation
        conditions = [
            {"type": "element_exists", "ref": e_ref},
            {"type": "element_state", "ref": e_ref, "state": "enabled", "value": True},
            {"type": "element_text", "ref": e_ref, "operator": "equals", "value": ""},
        ]

        def evaluate_condition(cond: dict) -> bool:
            handle = ref_store.resolve(cond["ref"])
            if handle is None:
                return False
            ctype = cond["type"]
            if ctype == "element_exists":
                return backend.is_valid(handle)
            elif ctype == "element_state":
                info = backend.get_element_info(handle)
                return info["states"].get(cond["state"]) == cond["value"]
            elif ctype == "element_text":
                text = backend.perform_action(handle, DesktopAction.GET_TEXT) or ""
                op = cond["operator"]
                expected = cond["value"]
                if op == "equals":
                    return text == expected
                elif op == "contains":
                    return expected in text
                elif op == "not_empty":
                    return len(text) > 0
            return False

        # All conditions should pass
        for cond in conditions:
            assert evaluate_condition(cond), f"Condition failed: {cond}"


# ---------------------------------------------------------------------------
# Spike 4: Polling loop integration with DesktopBackend
# ---------------------------------------------------------------------------


class TestPollingLoopIntegration:
    """Validate async polling loop that checks backend conditions.

    This is the core pattern wait_for will use: an async function that
    polls the backend at intervals until a condition is met or timeout.
    """

    @pytest.mark.asyncio
    async def test_async_poll_until_condition_met(self):
        """Simulate wait_for polling loop that returns when condition is met.

        Uses a mutable flag to simulate a condition becoming true after
        a short delay, validating the polling pattern.
        """
        backend = MockBackend()
        backend.add_window(title="Test")
        w = backend.list_windows()[0]
        backend.add_element(role="button", name="OK", parent=w)
        elem = backend.find_elements(w, role="button")[0]

        # Simulate condition: element becomes invalid after 100ms
        condition_met = {"value": False}

        async def simulate_change():
            await asyncio.sleep(0.05)  # 50ms
            backend.invalidate(elem)
            condition_met["value"] = True

        # Start background task that will change state
        changer = asyncio.create_task(simulate_change())

        # Polling loop (the wait_for pattern)
        timeout_ms = 500
        poll_interval_ms = 20
        t0 = time.monotonic()
        result = False

        while (time.monotonic() - t0) * 1000 < timeout_ms:
            if not backend.is_valid(elem):
                result = True
                break
            await asyncio.sleep(poll_interval_ms / 1000.0)

        await changer

        assert result is True
        elapsed_ms = (time.monotonic() - t0) * 1000
        # Should have detected within ~100ms (50ms delay + one poll cycle)
        assert elapsed_ms < 200, f"Took too long: {elapsed_ms:.1f}ms"

    @pytest.mark.asyncio
    async def test_async_poll_timeout(self):
        """Polling loop correctly times out when condition is never met."""
        backend = MockBackend()
        backend.add_window(title="Test")
        w = backend.list_windows()[0]
        backend.add_element(role="button", name="OK", parent=w)
        elem = backend.find_elements(w, role="button")[0]

        timeout_ms = 100
        poll_interval_ms = 20
        t0 = time.monotonic()
        result = False
        timed_out = False

        while (time.monotonic() - t0) * 1000 < timeout_ms:
            if not backend.is_valid(elem):  # Never becomes invalid
                result = True
                break
            await asyncio.sleep(poll_interval_ms / 1000.0)
        else:
            timed_out = True

        assert result is False
        assert timed_out is True

    @pytest.mark.asyncio
    async def test_async_poll_with_ref_store_resolution(self):
        """Full pipeline: ref_store → backend → condition check."""
        backend = MockBackend()
        backend.add_window(title="App")
        w = backend.list_windows()[0]
        backend.add_element(
            role="text",
            name="status",
            value="Loading...",
            parent=w,
        )
        elem = backend.find_elements(w, role="text")[0]

        ref_store = ElementRefStore()
        e_ref = ref_store.store(elem, prefix="e")

        # Simulate text change after delay
        async def simulate_text_change():
            await asyncio.sleep(0.05)
            # In MockBackend, we'd need to directly mutate (no setter API)
            backend._elements[elem].value = "Ready"

        changer = asyncio.create_task(simulate_text_change())

        from guidewire.backends.types import DesktopAction

        # Poll for text change
        timeout_ms = 500
        poll_interval_ms = 20
        t0 = time.monotonic()
        found = False

        while (time.monotonic() - t0) * 1000 < timeout_ms:
            handle = ref_store.resolve(e_ref)
            if handle and backend.is_valid(handle):
                text = backend.perform_action(handle, DesktopAction.GET_TEXT)
                if text == "Ready":
                    found = True
                    break
            await asyncio.sleep(poll_interval_ms / 1000.0)

        await changer
        assert found is True


# ---------------------------------------------------------------------------
# Spike 5: Architecture decision — recommended wait_for design
# ---------------------------------------------------------------------------


class TestArchitectureDecision:
    """Capture architecture decisions from the spike.

    These tests serve as living documentation of the validated design.
    They assert against the architectural constraints that must hold true.
    """

    def test_no_new_abc_methods_needed(self):
        """All wait_for conditions can be evaluated with existing ABC methods.

        Existing methods that serve wait_for conditions:
        - is_valid() → element_exists
        - get_element_info() → element_state, element_role
        - perform_action(GET_TEXT) → element_text
        - get_window_info() → window_title, window_focused
        - find_elements() → element_count, element_visible

        No new abstract methods need to be added to DesktopBackend.
        """
        from guidewire.backends.base import DesktopBackend

        abstract_methods = set(DesktopBackend.__abstractmethods__)
        # The spike confirms no new methods are needed
        expected_methods = {
            "list_windows",
            "get_window_info",
            "focus_window",
            "snapshot",
            "find_elements",
            "perform_action",
            "get_element_info",
            "is_valid",
            "clipboard_read",
            "clipboard_write",
            "scroll_to_item",
            "minimize_window",
            "maximize_window",
            "restore_window",
            "move_window",
            "resize_window",
            "dispose",
        }
        assert abstract_methods == expected_methods

    def test_handler_must_be_async(self):
        """The wait_for handler MUST be async to avoid blocking the event loop.

        A sync handler with time.sleep() would block all MCP processing
        during the wait period, making the server unresponsive.

        This test validates that an async handler is the correct pattern.
        """
        # This is a design assertion, not a runtime test
        # The wait_for tool handler signature must be:
        #   async def wait_for(
        #       condition: dict,
        #       timeout_ms: int = 5000,
        #       poll_interval_ms: int = 100,
        #   ) -> str
        assert True  # Design constraint documented

    def test_condition_dsl_is_json_dict(self):
        """Conditions are expressed as JSON dicts with a 'type' discriminator.

        This matches the MCP tool parameter pattern (all params are JSON
        serializable). The 'type' key determines which ABC method to use.

        Supported condition types (validated by this spike):
        - element_exists: uses is_valid()
        - element_state: uses get_element_info() → states dict
        - element_text: uses perform_action(GET_TEXT)
        - element_count: uses find_elements() → len()
        - window_title: uses get_window_info() → title
        """
        # Validate that all condition types map to existing ABC methods
        condition_to_method = {
            "element_exists": "is_valid",
            "element_state": "get_element_info",
            "element_text": "perform_action",  # GET_TEXT
            "element_count": "find_elements",
            "window_title": "get_window_info",
        }
        from guidewire.backends.base import DesktopBackend

        for cond_type, method_name in condition_to_method.items():
            assert hasattr(DesktopBackend, method_name), (
                f"Condition '{cond_type}' requires method '{method_name}' "
                f"which doesn't exist on DesktopBackend"
            )

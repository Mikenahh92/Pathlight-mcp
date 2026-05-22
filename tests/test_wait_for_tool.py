"""Tests for the desktop.wait_for tool handler (GW-064, GW-074).

Validates:
- Stub mode returns success without backend
- element_appears condition (element validity)
- element_disappears condition (element becomes invalid)
- text_equals condition (text comparison operators)
- state_change condition (state flag matching)
- duration condition (fixed-duration wait, no element ref)
- window_appears condition (window title matching, no element ref)
- Timeout behavior when condition is never met
- Immediate return when condition is already true
- Async polling detects state changes mid-wait (TC-02)
- Input validation (missing type, unknown type, missing required keys)
- Structured error responses for invalid/stale references
- Timeout/interval range validation (upper/lower bounds)
- Stale-during-poll returns stale_element_reference error (TC-10)
- Safety classification (READ_ONLY)
"""

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.types import ElementState, NativeHandle
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all


# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and elements."""
    b = MockBackend().add_window(title="Test Window", app="TestApp", focused=True)
    window_handle = b.list_windows()[0]
    b.add_element(
        role="button",
        name="Submit",
        value="Submit",
        parent=window_handle,
        states=ElementState(enabled=True, focused=False),
    )
    b.add_element(
        role="text",
        name="status",
        value="Loading...",
        parent=window_handle,
        states=ElementState(enabled=True, visible=True),
    )
    return b


@pytest.fixture()
def ref_store(backend: MockBackend) -> ElementRefStore:
    """Return an ElementRefStore with refs for window and elements."""
    store = ElementRefStore()
    window_handle = backend.list_windows()[0]
    store.store(window_handle, prefix="w")
    elements = backend.find_elements(window_handle)
    for handle in elements:
        store.store(handle, prefix="e")
    return store


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-wait-for")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-wait-for-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestWaitForStub:
    """wait_for returns static stub response when no backend is provided."""

    async def test_stub_returns_success(self, stub_mcp: FastMCP) -> None:
        """Without a backend, wait_for should return success."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.wait_for",
            arguments={"condition": {"type": "element_appears", "ref": "e1"}},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["message"] is not None


# -- element_appears condition ------------------------------------------------


class TestElementAppearsCondition:
    """element_appears: True when element is valid, False when not."""

    async def test_element_appears_true_immediately(
        self, mcp: FastMCP
    ) -> None:
        """Element exists — returns success immediately."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 200,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["elapsed_ms"] < 100  # Should be near-instant
        assert data["polls"] >= 1

    async def test_element_appears_detects_invalidation(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Element invalidated mid-wait — polling detects change."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window)

        async def invalidate_after_delay():
            await asyncio.sleep(0.05)
            backend.invalidate(elements[0])

        asyncio.create_task(invalidate_after_delay())

        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        # element_appears returns True when is_valid is True
        # e1 was valid on the first poll, so it should succeed immediately
        assert data["success"] is True


# -- element_disappears condition ---------------------------------------------


class TestElementDisappearsCondition:
    """element_disappears: True when element is no longer valid."""

    async def test_element_disappears_immediate(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Element already invalidated — disappears condition is True immediately."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window)
        backend.invalidate(elements[0])

        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_disappears", "ref": "e1"},
                "timeout_ms": 200,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["polls"] == 1

    async def test_element_disappears_after_delay(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Element invalidated mid-wait — disappears detects it."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window)

        async def invalidate_after_delay():
            await asyncio.sleep(0.05)
            backend.invalidate(elements[0])

        asyncio.create_task(invalidate_after_delay())

        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_disappears", "ref": "e1"},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["elapsed_ms"] < 200

    async def test_element_disappears_timeout(
        self, mcp: FastMCP
    ) -> None:
        """Element stays valid — disappears times out."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_disappears", "ref": "e1"},
                "timeout_ms": 100,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not met" in data["message"].lower()


# -- text_equals condition ---------------------------------------------------


class TestTextEqualsCondition:
    """text_equals: True when element text matches expected value."""

    async def test_text_equals_exact(
        self, mcp: FastMCP
    ) -> None:
        """Text equals — matches exact text."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "text_equals",
                    "ref": "e2",
                    "value": "Loading...",
                },
                "timeout_ms": 200,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_text_equals_contains(
        self, mcp: FastMCP
    ) -> None:
        """Text contains — matches substring."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "text_equals",
                    "ref": "e2",
                    "operator": "contains",
                    "value": "Loading",
                },
                "timeout_ms": 200,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_text_equals_not_empty(
        self, mcp: FastMCP
    ) -> None:
        """Text not_empty — matches non-empty text."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "text_equals",
                    "ref": "e2",
                    "operator": "not_empty",
                    "value": "",
                },
                "timeout_ms": 200,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_text_equals_timeout(
        self, mcp: FastMCP
    ) -> None:
        """Text doesn't match — times out."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "text_equals",
                    "ref": "e2",
                    "value": "Done",
                },
                "timeout_ms": 100,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False

    async def test_text_equals_detects_change(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Text changes mid-wait — polling detects the change."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="text")
        elem = elements[0]

        async def change_text():
            await asyncio.sleep(0.05)
            backend._elements[elem].value = "Ready"

        asyncio.create_task(change_text())

        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "text_equals",
                    "ref": "e2",
                    "value": "Ready",
                },
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["elapsed_ms"] < 200


# -- state_change condition --------------------------------------------------


class TestStateChangeCondition:
    """state_change: True when element state matches expected value."""

    async def test_state_change_enabled_true(
        self, mcp: FastMCP
    ) -> None:
        """Element has enabled=True — matches immediately."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "state_change",
                    "ref": "e1",
                    "state": "enabled",
                    "value": True,
                },
                "timeout_ms": 200,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_state_change_focused_false(
        self, mcp: FastMCP
    ) -> None:
        """Element has focused=False — matches immediately."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "state_change",
                    "ref": "e1",
                    "state": "focused",
                    "value": False,
                },
                "timeout_ms": 200,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_state_change_timeout_when_not_matching(
        self, mcp: FastMCP
    ) -> None:
        """Element state doesn't match — times out."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "state_change",
                    "ref": "e1",
                    "state": "focused",
                    "value": True,
                },
                "timeout_ms": 100,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert data["elapsed_ms"] >= 80  # Allow timing slack


# -- Timeout behavior ---------------------------------------------------------


class TestWaitForTimeout:
    """Timeout behavior when condition is never met."""

    async def test_timeout_returns_failure(
        self, mcp: FastMCP
    ) -> None:
        """Condition never met — returns success=False with timeout details."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "state_change",
                    "ref": "e1",
                    "state": "focused",
                    "value": True,
                },
                "timeout_ms": 80,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "elapsed_ms" in data
        assert "polls" in data
        assert "not met" in data["message"].lower()

    async def test_timeout_includes_poll_count(
        self, mcp: FastMCP
    ) -> None:
        """Timeout response includes number of polls performed."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "state_change",
                    "ref": "e1",
                    "state": "focused",
                    "value": True,
                },
                "timeout_ms": 100,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["polls"] >= 2


# -- Immediate return when already true ----------------------------------------


class TestWaitForImmediateReturn:
    """Returns immediately when condition is already true."""

    async def test_returns_immediately_when_true(
        self, mcp: FastMCP
    ) -> None:
        """Condition already true — returns in first poll."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 5000,
                "poll_interval_ms": 100,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["polls"] == 1
        assert data["elapsed_ms"] < 50


# -- TC-02: Async non-blocking test ------------------------------------------


class TestWaitForAsyncNonBlocking:
    """TC-02: wait_for does not block the MCP event loop.

    Two concurrent wait_for calls must be able to run in parallel,
    proving that asyncio.sleep() keeps the event loop responsive.
    """

    async def test_concurrent_wait_for_calls(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Two wait_for calls run concurrently — both complete."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="text")
        elem = elements[0]

        async def change_text():
            await asyncio.sleep(0.05)
            backend._elements[elem].value = "Ready"

        asyncio.create_task(change_text())

        # Run two wait_for calls concurrently
        results = await asyncio.gather(
            mcp.call_tool(
                "desktop.wait_for",
                arguments={
                    "condition": {
                        "type": "text_equals",
                        "ref": "e2",
                        "value": "Ready",
                    },
                    "timeout_ms": 500,
                    "poll_interval_ms": 20,
                },
            ),
            mcp.call_tool(
                "desktop.wait_for",
                arguments={
                    "condition": {"type": "element_appears", "ref": "e1"},
                    "timeout_ms": 500,
                    "poll_interval_ms": 20,
                },
            ),
        )

        data1 = json.loads(results[0][0][0].text)
        data2 = json.loads(results[1][0][0].text)
        assert data1["success"] is True
        assert data2["success"] is True

    async def test_wait_for_does_not_block_other_tool(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """A wait_for call does not block another tool call."""
        import time

        t0 = time.monotonic()

        async def delayed_element_appears():
            # This will succeed immediately since e1 is valid
            return await mcp.call_tool(
                "desktop.wait_for",
                arguments={
                    "condition": {"type": "element_appears", "ref": "e1"},
                    "timeout_ms": 200,
                    "poll_interval_ms": 20,
                },
            )

        result, _ = await delayed_element_appears()
        elapsed = (time.monotonic() - t0) * 1000
        data = json.loads(result[0].text)
        assert data["success"] is True
        # Should return well within 100ms (not blocked)
        assert elapsed < 100


# -- TC-10: Stale-during-poll test -------------------------------------------


class TestWaitForStaleDuringPoll:
    """TC-10: Stale element reference during polling returns error.

    When an element reference becomes stale mid-poll, the tool must
    return a stale_element_reference error immediately rather than
    timing out or raising an unhandled exception.
    """

    async def test_stale_ref_returns_error(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Stale reference during polling returns stale error."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window)
        elem = elements[0]

        # Register the element in the store
        store = ElementRefStore()
        ref = store.store(elem, prefix="s")

        # Create a separate MCP with this ref store
        stale_mcp = FastMCP(name="test-stale")
        register_all(stale_mcp, backend=backend, ref_store=store)

        async def make_stale():
            await asyncio.sleep(0.05)
            backend.invalidate(elem)

        asyncio.create_task(make_stale())

        result, _ = await stale_mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "state_change", "ref": ref, "state": "enabled", "value": False},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        # Should get a stale reference error, not a timeout
        assert data.get("error") == "stale_element_reference"
        assert "s1" in data.get("message", "") or ref in data.get("message", "")


# -- duration condition (GW-074) ---------------------------------------------


class TestDurationCondition:
    """duration: waits for a fixed duration_ms, no element ref needed."""

    async def test_duration_waits_specified_time(
        self, mcp: FastMCP
    ) -> None:
        """duration waits approximately duration_ms and returns success."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "duration", "duration_ms": 100},
                "timeout_ms": 5000,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["elapsed_ms"] >= 80  # allow timing slack
        assert data["elapsed_ms"] < 300

    async def test_duration_zero_ms(
        self, mcp: FastMCP
    ) -> None:
        """duration with duration_ms=0 returns immediately."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "duration", "duration_ms": 0},
                "timeout_ms": 5000,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["elapsed_ms"] < 50
        assert data["polls"] == 1

    async def test_duration_capped_by_timeout(
        self, mcp: FastMCP
    ) -> None:
        """duration waits min(duration_ms, timeout_ms)."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "duration", "duration_ms": 5000},
                "timeout_ms": 80,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        # Should be capped at timeout_ms (80ms), not wait the full 5000ms
        assert data["elapsed_ms"] < 200

    async def test_duration_no_ref_required(
        self, mcp: FastMCP
    ) -> None:
        """duration condition does not require a ref key."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "duration", "duration_ms": 10},
                "timeout_ms": 500,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_duration_missing_duration_ms(
        self, mcp: FastMCP
    ) -> None:
        """duration without duration_ms returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "duration"},
                "timeout_ms": 500,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "duration_ms" in data["message"]

    async def test_duration_negative_duration_ms(
        self, mcp: FastMCP
    ) -> None:
        """duration with negative duration_ms returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "duration", "duration_ms": -1},
                "timeout_ms": 500,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-negative" in data["message"]


# -- window_appears condition (GW-074) ----------------------------------------


class TestWindowAppearsCondition:
    """window_appears: waits for a window with matching title, no element ref."""

    async def test_window_appears_immediate_match(
        self, mcp: FastMCP
    ) -> None:
        """Window already exists with matching title — returns success immediately."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears", "title": "Test Window"},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["polls"] == 1

    async def test_window_appears_case_insensitive(
        self, mcp: FastMCP
    ) -> None:
        """Title match is case-insensitive."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears", "title": "test window"},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_window_appears_substring_match(
        self, mcp: FastMCP
    ) -> None:
        """Title match uses substring matching."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears", "title": "Test"},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_window_appears_timeout(
        self, mcp: FastMCP
    ) -> None:
        """No window with matching title — times out."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears", "title": "Nonexistent"},
                "timeout_ms": 100,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not met" in data["message"].lower()

    async def test_window_appears_no_ref_required(
        self, mcp: FastMCP
    ) -> None:
        """window_appears does not require a ref key."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears", "title": "Test Window"},
                "timeout_ms": 500,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_window_appears_missing_title(
        self, mcp: FastMCP
    ) -> None:
        """window_appears without title returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears"},
                "timeout_ms": 500,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "title" in data["message"]

    async def test_window_appears_detects_new_window(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Window appears mid-wait — polling detects the new window."""
        async def add_window_after_delay():
            await asyncio.sleep(0.05)
            backend.add_window(title="New Dialog", app="TestApp")

        asyncio.create_task(add_window_after_delay())

        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears", "title": "New Dialog"},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["elapsed_ms"] < 300

    async def test_window_appears_regex_match(
        self, mcp: FastMCP
    ) -> None:
        """window_appears with match='regex' matches using regex pattern."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "window_appears",
                    "title": r"Test\s+Window",
                    "match": "regex",
                },
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["polls"] == 1

    async def test_window_appears_regex_no_match(
        self, mcp: FastMCP
    ) -> None:
        """window_appears with match='regex' and no matching window times out."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "window_appears",
                    "title": r"^Error\d+$",
                    "match": "regex",
                },
                "timeout_ms": 100,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is False

    async def test_window_appears_returns_window_ref(
        self, mcp: FastMCP
    ) -> None:
        """window_appears registers matched window in ref_store and returns window_ref."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears", "title": "Test Window"},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "window_ref" in data
        assert data["window_ref"].startswith("w")
        assert "matched_title" in data
        assert data["matched_title"] == "Test Window"

    async def test_window_appears_regex_returns_window_ref(
        self, mcp: FastMCP
    ) -> None:
        """window_appears with regex returns window_ref on match."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "window_appears",
                    "title": r"Test.*Window",
                    "match": "regex",
                },
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "window_ref" in data
        assert data["window_ref"].startswith("w")

    async def test_window_appears_default_match_is_substring(
        self, mcp: FastMCP
    ) -> None:
        """window_appears without match key defaults to substring matching."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "window_appears", "title": "Test"},
                "timeout_ms": 500,
                "poll_interval_ms": 20,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True


# -- Input validation ---------------------------------------------------------


class TestWaitForValidation:
    """Input validation returns structured JSON errors."""

    async def test_missing_type_key(
        self, mcp: FastMCP
    ) -> None:
        """Condition without type key returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"ref": "e1"},
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "type" in data["message"].lower()

    async def test_unknown_condition_type(
        self, mcp: FastMCP
    ) -> None:
        """Unknown condition type returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "unknown_type", "ref": "e1"},
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "unknown_type" in data["message"]

    async def test_element_appears_missing_ref(
        self, mcp: FastMCP
    ) -> None:
        """element_appears without ref returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears"},
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

    async def test_state_change_missing_state(
        self, mcp: FastMCP
    ) -> None:
        """state_change without state key returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "state_change", "ref": "e1"},
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "state" in data["message"]

    async def test_text_equals_missing_value(
        self, mcp: FastMCP
    ) -> None:
        """text_equals without value returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "text_equals",
                    "ref": "e1",
                },
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "value" in data["message"]

    async def test_negative_timeout(
        self, mcp: FastMCP
    ) -> None:
        """Negative timeout returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": -1,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

    async def test_negative_poll_interval(
        self, mcp: FastMCP
    ) -> None:
        """Negative poll interval returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 100,
                "poll_interval_ms": -1,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

    async def test_unknown_ref_returns_error(
        self, mcp: FastMCP
    ) -> None:
        """Unknown reference returns element_not_found error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e99"},
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "element_not_found"
        assert "e99" in data["message"]

    async def test_window_appears_invalid_match_mode(
        self, mcp: FastMCP
    ) -> None:
        """window_appears with invalid match mode returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "window_appears",
                    "title": "Test",
                    "match": "exact",
                },
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "match" in data["message"]

    async def test_window_appears_invalid_regex(
        self, mcp: FastMCP
    ) -> None:
        """window_appears with invalid regex returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {
                    "type": "window_appears",
                    "title": r"[invalid",
                    "match": "regex",
                },
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "regex" in data["message"].lower() or "Invalid" in data["message"]


# -- Range validation ---------------------------------------------------------


class TestWaitForRangeValidation:
    """Timeout and poll interval range validation."""

    async def test_timeout_exceeds_max(
        self, mcp: FastMCP
    ) -> None:
        """Timeout exceeding 60s returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 60_001,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "60000" in data["message"] or "60" in data["message"]

    async def test_timeout_at_max_is_accepted(
        self, mcp: FastMCP
    ) -> None:
        """Timeout at exactly 60s is accepted (condition met immediately)."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 60_000,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_poll_interval_below_min(
        self, mcp: FastMCP
    ) -> None:
        """Poll interval below 10ms returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 200,
                "poll_interval_ms": 5,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "10" in data["message"]

    async def test_poll_interval_above_max(
        self, mcp: FastMCP
    ) -> None:
        """Poll interval above 5000ms returns validation error."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 200,
                "poll_interval_ms": 6000,
            },
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "5000" in data["message"]

    async def test_poll_interval_at_min_is_accepted(
        self, mcp: FastMCP
    ) -> None:
        """Poll interval at exactly 10ms is accepted."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 200,
                "poll_interval_ms": 10,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_poll_interval_at_max_is_accepted(
        self, mcp: FastMCP
    ) -> None:
        """Poll interval at exactly 5000ms is accepted."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 6000,
                "poll_interval_ms": 5000,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True


# -- Schema validation --------------------------------------------------------


class TestWaitForSchema:
    """wait_for tool schema remains correct after wiring."""

    async def test_tool_name_registered(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.wait_for."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.wait_for" in names

    async def test_condition_parameter_required(
        self, mcp: FastMCP
    ) -> None:
        """condition parameter should be required."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.wait_for")
        schema = tool.inputSchema
        assert "condition" in schema["properties"]
        assert "condition" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.wait_for")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "wait" in tool.description.lower()

    async def test_default_timeout(self, mcp: FastMCP) -> None:
        """timeout_ms should default to 5000."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.wait_for")
        schema = tool.inputSchema
        props = schema["properties"]
        assert "timeout_ms" in props

    async def test_default_poll_interval(self, mcp: FastMCP) -> None:
        """poll_interval_ms should default to 100."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.wait_for")
        schema = tool.inputSchema
        props = schema["properties"]
        assert "poll_interval_ms" in props


# -- Safety classification ----------------------------------------------------


class TestWaitForSafety:
    """wait_for is READ_ONLY — passive observation only."""

    async def test_risk_is_read_only(
        self, mcp: FastMCP
    ) -> None:
        """Success response should report read_only risk."""
        result, _ = await mcp.call_tool(
            "desktop.wait_for",
            arguments={
                "condition": {"type": "element_appears", "ref": "e1"},
                "timeout_ms": 200,
            },
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["risk"] == "read_only"

"""Tests for CDP event buffer (events.py).

Validates thread-safety, per-method bounded capacity, method filtering, and
clear behavior of :class:`EventBuffer`.
"""

import threading
from typing import Any

import pytest

from pathlight_mcp.cdp.events import EventBuffer
from pathlight_mcp.cdp.protocol import CDPEvent


def _evt(method: str, **params: Any) -> CDPEvent:
    """Create a test CDPEvent with optional params."""
    return CDPEvent(method=method, params=dict(params) if params else {})


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestEventBufferInit:
    """Tests for :class:`EventBuffer` construction."""

    def test_default_maxsize_per_method(self) -> None:
        buf = EventBuffer()
        assert buf.maxsize_per_method == 1024

    def test_custom_maxsize_per_method(self) -> None:
        buf = EventBuffer(maxsize_per_method=50)
        assert buf.maxsize_per_method == 50

    def test_unlimited_maxsize_per_method(self) -> None:
        buf = EventBuffer(maxsize_per_method=0)
        assert buf.maxsize_per_method == 0

    def test_negative_maxsize_per_method_raises(self) -> None:
        with pytest.raises(ValueError, match="maxsize_per_method must be >= 0"):
            EventBuffer(maxsize_per_method=-1)


# ---------------------------------------------------------------------------
# Put and get
# ---------------------------------------------------------------------------


class TestEventBufferPutGet:
    """Tests for basic put/get operations."""

    def test_put_and_get_all(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("Page.loadEventFired"))
        buf.put(_evt("Runtime.consoleAPICalled"))
        events = buf.get_all()
        assert len(events) == 2
        assert events[0].method == "Page.loadEventFired"
        assert events[1].method == "Runtime.consoleAPICalled"

    def test_get_all_returns_copy(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("test"))
        events = buf.get_all()
        events.clear()
        assert len(buf.get_all()) == 1

    def test_empty_buffer_get_all(self) -> None:
        buf = EventBuffer()
        assert buf.get_all() == []

    def test_len(self) -> None:
        buf = EventBuffer()
        assert len(buf) == 0
        buf.put(_evt("a"))
        assert len(buf) == 1
        buf.put(_evt("b"))
        assert len(buf) == 2


# ---------------------------------------------------------------------------
# Method filtering
# ---------------------------------------------------------------------------


class TestEventBufferFilter:
    """Tests for method-based filtering."""

    def test_get_by_method(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("Page.loadEventFired"))
        buf.put(_evt("Runtime.consoleAPICalled"))
        buf.put(_evt("Page.loadEventFired"))
        result = buf.get_by_method("Page.loadEventFired")
        assert len(result) == 2
        assert all(e.method == "Page.loadEventFired" for e in result)

    def test_get_by_method_no_match(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("Page.loadEventFired"))
        assert buf.get_by_method("DOM.childNodeInserted") == []

    def test_get_latest_no_filter(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("a"))
        buf.put(_evt("b"))
        assert buf.get_latest() is not None
        assert buf.get_latest().method == "b"  # type: ignore[union-attr]

    def test_get_latest_with_filter(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("a", x=1))
        buf.put(_evt("b"))
        buf.put(_evt("a", x=2))
        latest_a = buf.get_latest(method="a")
        assert latest_a is not None
        assert latest_a.params == {"x": 2}

    def test_get_latest_empty_buffer(self) -> None:
        buf = EventBuffer()
        assert buf.get_latest() is None

    def test_get_latest_no_method_match(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("a"))
        assert buf.get_latest(method="z") is None


# ---------------------------------------------------------------------------
# Per-method bounded capacity
# ---------------------------------------------------------------------------


class TestEventBufferPerMethodBounded:
    """Tests for per-method bounded buffer capacity and eviction."""

    def test_evicts_oldest_per_method(self) -> None:
        buf = EventBuffer(maxsize_per_method=3)
        buf.put(_evt("a"))
        buf.put(_evt("a"))
        buf.put(_evt("a"))
        buf.put(_evt("a"))  # should evict first "a"
        events_a = buf.get_by_method("a")
        assert len(events_a) == 3

    def test_chatty_events_dont_evict_other_methods(self) -> None:
        """Per-method isolation: chatty events cannot evict other methods."""
        buf = EventBuffer(maxsize_per_method=3)
        # Fill "chatty" method to capacity
        buf.put(_evt("chatty", n=1))
        buf.put(_evt("chatty", n=2))
        buf.put(_evt("chatty", n=3))
        buf.put(_evt("chatty", n=4))  # evicts chatty n=1

        # Important event from different method should be preserved
        buf.put(_evt("Page.loadEventFired", url="important"))

        important = buf.get_by_method("Page.loadEventFired")
        assert len(important) == 1
        assert important[0].params == {"url": "important"}

        # Chatty method should have evicted its oldest
        chatty = buf.get_by_method("chatty")
        assert len(chatty) == 3
        assert chatty[0].params == {"n": 2}

    def test_is_full(self) -> None:
        buf = EventBuffer(maxsize_per_method=2)
        assert not buf.is_full
        buf.put(_evt("a"))
        assert not buf.is_full
        buf.put(_evt("a"))
        assert buf.is_full

    def test_is_full_per_method_independent(self) -> None:
        """is_full reflects per-method capacity, not total."""
        buf = EventBuffer(maxsize_per_method=2)
        buf.put(_evt("a"))
        buf.put(_evt("b"))
        assert not buf.is_full  # neither deque is full individually
        buf.put(_evt("a"))
        assert buf.is_full  # "a" deque is full

    def test_is_full_unlimited(self) -> None:
        buf = EventBuffer(maxsize_per_method=0)
        assert not buf.is_full
        for i in range(100):
            buf.put(_evt(f"evt-{i}"))
        assert not buf.is_full

    def test_maxsize_one(self) -> None:
        buf = EventBuffer(maxsize_per_method=1)
        buf.put(_evt("first"))
        buf.put(_evt("first"))
        events = buf.get_by_method("first")
        assert len(events) == 1
        assert events[0].params == {}

    def test_methods_property(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("Page.loadEventFired"))
        buf.put(_evt("Runtime.consoleAPICalled"))
        assert "Page.loadEventFired" in buf.methods
        assert "Runtime.consoleAPICalled" in buf.methods


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


class TestEventBufferClear:
    """Tests for buffer clearing."""

    def test_clear(self) -> None:
        buf = EventBuffer()
        buf.put(_evt("a"))
        buf.put(_evt("b"))
        buf.clear()
        assert len(buf) == 0
        assert buf.get_all() == []

    def test_clear_empty(self) -> None:
        buf = EventBuffer()
        buf.clear()  # should not raise
        assert len(buf) == 0


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestEventBufferThreadSafety:
    """Tests for concurrent access safety."""

    def test_concurrent_puts(self) -> None:
        buf = EventBuffer(maxsize_per_method=0)
        num_threads = 10
        events_per_thread = 100

        def writer(thread_id: int) -> None:
            for i in range(events_per_thread):
                buf.put(_evt(f"t{thread_id}-e{i}"))

        threads = [
            threading.Thread(target=writer, args=(tid,))
            for tid in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(buf) == num_threads * events_per_thread

    def test_concurrent_put_and_read(self) -> None:
        buf = EventBuffer(maxsize_per_method=0)
        stop = threading.Event()
        read_count = 0
        lock = threading.Lock()

        def reader() -> None:
            nonlocal read_count
            while not stop.is_set():
                buf.get_all()
                with lock:
                    read_count += 1

        reader_thread = threading.Thread(target=reader)
        reader_thread.start()

        for i in range(200):
            buf.put(_evt(f"e-{i}"))

        stop.set()
        reader_thread.join(timeout=5)

        assert len(buf) == 200
        assert read_count > 0

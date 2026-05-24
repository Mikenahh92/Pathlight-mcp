"""Tests for CDPProtocol handler (_protocol.py).

Validates the CDPProtocol class: Future-based command correlation,
event dispatch, and pending cancellation.
"""

import threading
from typing import Any

import pytest

from guidewire.cdp._protocol import CDPProtocol
from guidewire.cdp.events import EventBuffer
from guidewire.cdp.protocol import CDPError, CDPResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CaptureSend:
    """Callable that captures sent data and optionally defers."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def __call__(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def _create_protocol(
    default_timeout: float = 5.0,
) -> tuple[CDPProtocol, _CaptureSend, EventBuffer]:
    """Create a CDPProtocol with a capture send function."""
    send_fn = _CaptureSend()
    buf = EventBuffer()
    proto = CDPProtocol(send_fn=send_fn, event_buffer=buf, default_timeout=default_timeout)
    return proto, send_fn, buf


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestCDPProtocolInit:
    """Tests for CDPProtocol construction."""

    def test_default_timeout(self) -> None:
        proto, _, _ = _create_protocol()
        assert proto._default_timeout == 5.0

    def test_custom_timeout(self) -> None:
        proto, _, _ = _create_protocol(default_timeout=10.0)
        assert proto._default_timeout == 10.0


# ---------------------------------------------------------------------------
# send_command
# ---------------------------------------------------------------------------


class TestCDPProtocolSendCommand:
    """Tests for send_command."""

    def test_send_command_success(self) -> None:
        proto, send_fn, _ = _create_protocol()

        # Simulate response arriving in a background thread
        def respond() -> None:
            import time
            time.sleep(0.05)
            proto._handle_response(CDPResponse(id=1, result={"value": True}))

        threading.Thread(target=respond, daemon=True).start()

        result = proto.send_command("Test.method", timeout=2.0)
        assert result == {"value": True}
        assert len(send_fn.sent) == 1
        assert send_fn.sent[0]["method"] == "Test.method"

    def test_send_command_error_response(self) -> None:
        proto, _, _ = _create_protocol()

        def respond() -> None:
            import time
            time.sleep(0.05)
            proto._handle_response(
                CDPResponse(id=1, error={"code": -32000, "message": "Not found"})
            )

        threading.Thread(target=respond, daemon=True).start()

        with pytest.raises(CDPError, match="CDP error -32000"):
            proto.send_command("Test.method", timeout=2.0)

    def test_send_command_timeout(self) -> None:
        proto, _, _ = _create_protocol(default_timeout=0.1)

        with pytest.raises(TimeoutError, match="timed out"):
            proto.send_command("Test.method")

    def test_send_command_increments_ids(self) -> None:
        proto, send_fn, _ = _create_protocol()

        def respond_to(cmd_id: int) -> None:
            proto._handle_response(CDPResponse(id=cmd_id, result={}))

        # Start response threads
        threading.Timer(0.05, lambda: respond_to(1)).start()
        threading.Timer(0.15, lambda: respond_to(2)).start()

        proto.send_command("Method1", timeout=2.0)
        proto.send_command("Method2", timeout=2.0)

        assert send_fn.sent[0]["id"] == 1
        assert send_fn.sent[1]["id"] == 2


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


class TestCDPProtocolDispatch:
    """Tests for dispatch."""

    def test_dispatches_response(self) -> None:
        proto, _, _ = _create_protocol()

        # Start a command to have a pending future
        result_holder: dict[str, Any] = {}

        def send_and_capture() -> None:
            try:
                result_holder["result"] = proto.send_command("Test", timeout=2.0)
            except Exception as exc:
                result_holder["error"] = exc

        t = threading.Thread(target=send_and_capture)
        t.start()

        import time
        time.sleep(0.05)

        proto.dispatch({"id": 1, "result": {"ok": True}})
        t.join(timeout=3)

        assert result_holder.get("result") == {"ok": True}

    def test_dispatches_event(self) -> None:
        proto, _, buf = _create_protocol()

        proto.dispatch({"method": "Page.loadEventFired", "params": {"ts": 1.0}})

        events = buf.get_by_method("Page.loadEventFired")
        assert len(events) == 1
        assert events[0].params == {"ts": 1.0}


# ---------------------------------------------------------------------------
# cancel_pending
# ---------------------------------------------------------------------------


class TestCDPProtocolCancelPending:
    """Tests for cancel_pending."""

    def test_cancel_pending_wakes_waiters(self) -> None:
        proto, _, _ = _create_protocol()

        error_holder: dict[str, Any] = {"error": None}

        def send_cmd() -> None:
            try:
                proto.send_command("Test", timeout=10)
            except Exception as exc:
                error_holder["error"] = exc

        t = threading.Thread(target=send_cmd)
        t.start()

        import time
        time.sleep(0.05)

        proto.cancel_pending()
        t.join(timeout=3)

        assert error_holder["error"] is not None

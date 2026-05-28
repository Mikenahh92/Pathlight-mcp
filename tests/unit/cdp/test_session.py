"""Tests for CDP session management (session.py).

Validates :class:`CDPSession` — attach/detach lifecycle, session-scoped
command sending, state transitions, and error handling.
"""

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pathlight_mcp.cdp._types import CDPTarget, SessionState
from pathlight_mcp.cdp.connection import CDPConnection
from pathlight_mcp.cdp.session import CDPSession
from pathlight_mcp.errors import PathlightMCPError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Minimal fake WebSocket for testing."""

    def __init__(self) -> None:
        self._queue: list[str] = []
        self._sent: list[str] = []
        self._closed = False
        self._lock = threading.Lock()
        self._has_data = threading.Condition(self._lock)

    def enqueue(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._queue.append(json.dumps(data))
            self._has_data.notify()

    def recv(self) -> str:
        with self._lock:
            while not self._queue and not self._closed:
                if not self._has_data.wait(timeout=0.5):
                    continue
            if self._closed and not self._queue:
                raise ConnectionError("WebSocket is closed")
            return self._queue.pop(0)

    def send(self, payload: str) -> None:
        if self._closed:
            raise ConnectionError("WebSocket is closed")
        self._sent.append(payload)

    def close(self) -> None:
        self._closed = True

    @property
    def sent_messages(self) -> list[dict[str, Any]]:
        return [json.loads(s) for s in self._sent]


def _fake_ws_module(fake_ws: FakeWebSocket) -> MagicMock:
    mod = MagicMock()
    mod.create_connection.return_value = fake_ws
    return mod


def _create_connected_connection() -> tuple[CDPConnection, FakeWebSocket]:
    """Create a connected CDPConnection with a FakeWebSocket."""
    fake_ws = FakeWebSocket()
    ws_mod = _fake_ws_module(fake_ws)

    with patch("pathlight_mcp.cdp.connection._import_websocket", return_value=ws_mod):
        conn = CDPConnection(url="ws://localhost:9222/devtools/browser")
        conn.connect()

    return conn, fake_ws


def _create_target(
    target_id: str = "target-1",
    target_type: str = "page",
) -> CDPTarget:
    """Create a test CDPTarget."""
    return CDPTarget(
        id=target_id,
        type=target_type,
        title="Test Page",
        url="https://example.com",
        web_socket_debugger_url=f"ws://localhost:9222/devtools/page/{target_id}",
    )


def _attach_session(
    session: CDPSession,
    fake_ws: FakeWebSocket,
    session_id: str = "session-abc",
) -> None:
    """Attach a session by enqueueing the attach response."""
    # Enqueue response for Target.attachToTarget
    threading.Timer(
        0.05,
        lambda: fake_ws.enqueue(
            {
                "id": 1,
                "result": {"sessionId": session_id},
            }
        ),
    ).start()
    session.attach()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestCDPSessionInit:
    """Tests for CDPSession construction."""

    def test_initial_state(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        assert session.state == SessionState.DETACHED
        assert session.session_id is None
        assert not session.is_attached

    def test_target_stored(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        assert session.target.id == "target-1"
        assert session.target.type == "page"


# ---------------------------------------------------------------------------
# Attach
# ---------------------------------------------------------------------------


class TestCDPSessionAttach:
    """Tests for attach."""

    def test_attach_success(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Queue the attach response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "result": {"sessionId": "sess-123"},
                }
            ),
        ).start()

        session_id = session.attach()
        assert session_id == "sess-123"
        assert session.state == SessionState.ATTACHED
        assert session.session_id == "sess-123"
        assert session.is_attached

        # Verify the command was sent
        sent = fake_ws.sent_messages
        assert len(sent) >= 1
        attach_cmd = sent[0]
        assert attach_cmd["method"] == "Target.attachToTarget"
        assert attach_cmd["params"]["targetId"] == "target-1"
        assert attach_cmd["params"]["flatten"] is True

    def test_attach_no_flatten(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "result": {"sessionId": "sess-456"},
                }
            ),
        ).start()

        session.attach(flatten=False)

        sent = fake_ws.sent_messages
        attach_cmd = sent[0]
        assert attach_cmd["params"]["flatten"] is False

    def test_attach_already_attached_raises(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        with pytest.raises(PathlightMCPError, match="already attached"):
            session.attach()

    def test_attach_failure_resets_state(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Queue an error response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "error": {"code": -32000, "message": "Target not found"},
                }
            ),
        ).start()

        with pytest.raises(PathlightMCPError):
            session.attach()

        assert session.state == SessionState.DETACHED
        assert session.session_id is None

    def test_attach_no_session_id_in_response_raises(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Response without sessionId
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 1, "result": {}}),
        ).start()

        with pytest.raises(PathlightMCPError, match="no sessionId"):
            session.attach()

        assert session.state == SessionState.DETACHED


# ---------------------------------------------------------------------------
# Detach
# ---------------------------------------------------------------------------


class TestCDPSessionDetach:
    """Tests for detach."""

    def test_detach_success(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        # Queue the detach response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {}}),
        ).start()

        session.detach()

        assert session.state == SessionState.DETACHED
        assert session.session_id is None
        assert not session.is_attached

        # Verify detach command was sent
        sent = fake_ws.sent_messages
        detach_cmd = sent[-1]
        assert detach_cmd["method"] == "Target.detachFromTarget"
        assert detach_cmd["params"]["sessionId"] == "session-abc"

    def test_detach_not_attached_raises(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        with pytest.raises(PathlightMCPError, match="not attached"):
            session.detach()

    def test_detach_failure_still_resets_state(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        # Queue an error for detach
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 2,
                    "error": {"code": -32000, "message": "Session not found"},
                }
            ),
        ).start()

        # detach should still reset state even on error
        session.detach()
        assert session.state == SessionState.DETACHED
        assert session.session_id is None


# ---------------------------------------------------------------------------
# Send command
# ---------------------------------------------------------------------------


class TestCDPSessionSendCommand:
    """Tests for session-scoped send_command."""

    def test_send_command_success(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-send")

        # Queue response for the scoped command
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 2,
                    "result": {"frameId": "main"},
                }
            ),
        ).start()

        result = session.send_command("Page.navigate", {"url": "https://example.com"})
        assert result == {"frameId": "main"}

        sent = fake_ws.sent_messages
        scoped_cmd = sent[-1]
        assert scoped_cmd["method"] == "Page.navigate"
        assert scoped_cmd["sessionId"] == "sess-send"
        assert scoped_cmd["params"]["url"] == "https://example.com"

    def test_send_command_no_params(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-send")

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {}}),
        ).start()

        result = session.send_command("Page.enable")
        assert result == {}

        sent = fake_ws.sent_messages
        scoped_cmd = sent[-1]
        assert scoped_cmd["sessionId"] == "sess-send"
        assert "params" not in scoped_cmd or scoped_cmd.get("params") == {}

    def test_send_command_not_attached_reattempts_and_fails(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Session was never attached — send_command will try to re-attach
        # but the attach itself will fail (no valid response queued)
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "error": {"code": -32000, "message": "Target not found"},
                }
            ),
        ).start()

        with pytest.raises(PathlightMCPError):
            session.send_command("Page.enable")

    def test_send_command_with_custom_timeout(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {"data": True}}),
        ).start()

        result = session.send_command("Test.method", timeout=5.0)
        assert result == {"data": True}


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


class TestCDPSessionClose:
    """Tests for close."""

    def test_close_detaches_if_attached(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {}}),
        ).start()

        session.close()
        assert session.state == SessionState.DETACHED

    def test_close_is_safe_when_detached(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        session.close()  # should not raise
        assert session.state == SessionState.DETACHED


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestCDPSessionContextManager:
    """Tests for context manager usage."""

    def test_context_manager_attach_detach(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Enqueue attach response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "result": {"sessionId": "sess-ctx"},
                }
            ),
        ).start()

        with session:
            assert session.is_attached
            assert session.session_id == "sess-ctx"

            # Enqueue detach response
            threading.Timer(
                0.05,
                lambda: fake_ws.enqueue({"id": 2, "result": {}}),
            ).start()

        assert session.state == SessionState.DETACHED


# ---------------------------------------------------------------------------
# Stale session handling (GW-117)
# ---------------------------------------------------------------------------


class TestMarkDetached:
    """Tests for mark_detached (proactive invalidation via Target.detachedFromTarget)."""

    def test_mark_detached_transitions_state(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-mark")
        assert session.is_attached
        assert session.session_id == "sess-mark"

        session.mark_detached()
        assert session.state == SessionState.DETACHED
        assert session.session_id is None
        assert not session.is_attached

    def test_mark_detached_idempotent(self) -> None:
        conn, _fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Mark detached on a never-attached session — should not raise
        session.mark_detached()
        assert session.state == SessionState.DETACHED

    def test_mark_detached_from_any_thread(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-thread")

        # Mark detached from a different thread
        t = threading.Thread(target=session.mark_detached)
        t.start()
        t.join(timeout=2)

        assert session.state == SessionState.DETACHED
        assert session.session_id is None


class TestAutoReattach:
    """Tests for automatic re-attach on stale session (GW-117)."""

    def test_send_command_reattaches_after_mark_detached(self) -> None:
        """After mark_detached, send_command should re-attach and succeed."""
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-old")
        session.mark_detached()

        # Queue re-attach response (new session ID)
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {"id": 2, "result": {"sessionId": "sess-new"}},
            ),
        ).start()

        # Queue response for the actual command
        threading.Timer(
            0.1,
            lambda: fake_ws.enqueue(
                {"id": 3, "result": {"data": "ok"}},
            ),
        ).start()

        result = session.send_command("Test.method")
        assert result == {"data": "ok"}
        assert session.is_attached
        assert session.session_id == "sess-new"

    def test_send_command_reattaches_on_stale_error(self) -> None:
        """When the browser returns a stale session error, send_command should re-attach."""
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-stale")

        # First command returns a stale session error
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 2,
                    "error": {"code": -32000, "message": "Not attached to target"},
                }
            ),
        ).start()

        # Re-attach response
        threading.Timer(
            0.1,
            lambda: fake_ws.enqueue(
                {"id": 3, "result": {"sessionId": "sess-fresh"}},
            ),
        ).start()

        # Retry command response
        threading.Timer(
            0.15,
            lambda: fake_ws.enqueue(
                {"id": 4, "result": {"retried": True}},
            ),
        ).start()

        result = session.send_command("Test.method")
        assert result == {"retried": True}
        assert session.session_id == "sess-fresh"

    def test_send_command_does_not_reattach_on_non_stale_error(self) -> None:
        """Non-stale errors should not trigger re-attach."""
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-ok")

        # Non-stale error
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 2,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            ),
        ).start()

        with pytest.raises(PathlightMCPError, match="Method not found"):
            session.send_command("Nonexistent.method")

    def test_send_command_reattach_exhausted_raises(self) -> None:
        """If re-attach fails, the error should propagate."""
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-exhaust")
        session.mark_detached()

        # Re-attach fails
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 2,
                    "error": {"code": -32000, "message": "Target not found"},
                }
            ),
        ).start()

        with pytest.raises(PathlightMCPError):
            session.send_command("Test.method")


class TestIsStaleSessionError:
    """Tests for _is_stale_session_error heuristic."""

    def test_not_attached_message(self) -> None:
        from pathlight_mcp.cdp.protocol import CDPError
        assert CDPSession._is_stale_session_error(
            CDPError(-32000, "Not attached to target")
        )

    def test_session_not_found_message(self) -> None:
        from pathlight_mcp.cdp.protocol import CDPError
        assert CDPSession._is_stale_session_error(
            CDPError(-32000, "Session not found")
        )

    def test_session_is_closing_message(self) -> None:
        from pathlight_mcp.cdp.protocol import CDPError
        assert CDPSession._is_stale_session_error(
            CDPError(-32000, "Session is closing")
        )

    def test_target_closed_message(self) -> None:
        from pathlight_mcp.cdp.protocol import CDPError
        assert CDPSession._is_stale_session_error(
            CDPError(-32000, "Target closed")
        )

    def test_cdp_error_code_32000(self) -> None:
        from pathlight_mcp.cdp.protocol import CDPError
        assert CDPSession._is_stale_session_error(
            CDPError(-32000, "Something went wrong")
        )

    def test_pathlight_mcp_error_not_attached(self) -> None:
        assert CDPSession._is_stale_session_error(
            PathlightMCPError("Session not attached to target")
        )

    def test_non_stale_error_returns_false(self) -> None:
        from pathlight_mcp.cdp.protocol import CDPError
        assert not CDPSession._is_stale_session_error(
            CDPError(-32601, "Method not found")
        )

    def test_generic_exception_not_stale(self) -> None:
        assert not CDPSession._is_stale_session_error(
            ValueError("some random error")
        )


# ---------------------------------------------------------------------------
# Exponential backoff re-attach (GW-128)
# ---------------------------------------------------------------------------


class TestExponentialBackoffReattach:
    """Tests for exponential backoff CDP session re-attach (GW-128)."""

    def test_max_reattach_attempts_is_three(self) -> None:
        """GW-128: _MAX_REATTACH_ATTEMPTS should be 3 (not 1)."""
        from pathlight_mcp.cdp.session import _MAX_REATTACH_ATTEMPTS

        assert _MAX_REATTACH_ATTEMPTS == 3

    def test_backoff_base_is_positive(self) -> None:
        """GW-128: _REATTACH_BACKOFF_BASE should be a positive value."""
        from pathlight_mcp.cdp.session import _REATTACH_BACKOFF_BASE

        assert _REATTACH_BACKOFF_BASE > 0

    def test_send_command_retries_up_to_three_times_on_stale(
        self,
    ) -> None:
        """After mark_detached, send_command should retry up to 3 re-attach attempts."""
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-backoff")
        session.mark_detached()

        # First re-attach fails (quickly)
        threading.Timer(
            0.1,
            lambda: fake_ws.enqueue(
                {"id": 2, "error": {"code": -32000, "message": "Target not found"}},
            ),
        ).start()

        # Second re-attach succeeds (after backoff delay)
        threading.Timer(
            0.8,
            lambda: fake_ws.enqueue(
                {"id": 3, "result": {"sessionId": "sess-retry-2"}},
            ),
        ).start()

        # Command response on the new session
        threading.Timer(
            1.2,
            lambda: fake_ws.enqueue(
                {"id": 4, "result": {"data": "recovered"}},
            ),
        ).start()

        result = session.send_command("Test.method", timeout=10.0)
        assert result == {"data": "recovered"}
        assert session.session_id == "sess-retry-2"

    def test_send_command_exhausts_all_three_retries(self) -> None:
        """All 3 re-attach attempts can fail before raising."""
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-exhaust-3")
        session.mark_detached()

        # Response queueing strategy: enqueue error responses with enough
        # delay that the command has already been sent (and its future
        # registered) before the response arrives.  Backoff schedule:
        #   attempt 0 -> no delay, command ~0s,   response at 0.1s
        #   attempt 1 -> 0.25s backoff, command ~0.3s, response at 0.5s
        #   attempt 2 -> 0.5s backoff, command ~0.9s, response at 1.2s
        delays = [0.1, 0.5, 1.2]
        for i, delay in enumerate(delays):
            cid = 2 + i
            threading.Timer(
                delay,
                lambda c=cid: fake_ws.enqueue(
                    {"id": c, "error": {"code": -32000, "message": "Target not found"}},
                ),
            ).start()

        with pytest.raises(PathlightMCPError):
            session.send_command("Test.method", timeout=10.0)

    def test_send_command_recovers_on_second_stale_error(self) -> None:
        """First stale error triggers re-attach, which succeeds, then command succeeds."""
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-stale-twice")

        # First command: stale session error
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 2,
                    "error": {"code": -32000, "message": "Not attached to target"},
                }
            ),
        ).start()

        # Re-attach response
        threading.Timer(
            0.15,
            lambda: fake_ws.enqueue(
                {"id": 3, "result": {"sessionId": "sess-fresh-2"}},
            ),
        ).start()

        # Retry command response
        threading.Timer(
            0.25,
            lambda: fake_ws.enqueue(
                {"id": 4, "result": {"recovered": True}},
            ),
        ).start()

        result = session.send_command("Test.method")
        assert result == {"recovered": True}
        assert session.session_id == "sess-fresh-2"

    def test_backoff_and_reattach_no_delay_on_first_attempt(self) -> None:
        """_backoff_and_reattach(attempt=0) should have no delay."""
        import time as _time

        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Enqueue attach response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {"id": 1, "result": {"sessionId": "sess-fast"}},
            ),
        ).start()

        t0 = _time.monotonic()
        session._backoff_and_reattach(attempt=0)
        elapsed = _time.monotonic() - t0

        # First attempt should have minimal delay (< 50ms)
        assert elapsed < 0.1
        assert session.is_attached

    def test_exhausted_error_propagates(self) -> None:
        """When retries are exhausted, the last error should propagate."""
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Never attached — all retries will fail.
        # Same strategy as test_send_command_exhausts_all_three_retries but
        # starting from ID 1 (no prior attach).
        delays = [0.1, 0.5, 1.2]
        for i, delay in enumerate(delays):
            cid = 1 + i
            threading.Timer(
                delay,
                lambda c=cid: fake_ws.enqueue(
                    {"id": c, "error": {"code": -32000, "message": "Target not found"}},
                ),
            ).start()

        with pytest.raises(PathlightMCPError):
            session.send_command("Test.method", timeout=10.0)

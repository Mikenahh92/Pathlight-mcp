"""Tests for CDP type definitions (_types.py).

Validates the ConnectionState, SessionState enums and CDPTarget dataclass.
"""

from typing import Any

import pytest

from guidewire.cdp._types import CDPTarget, ConnectionState, SessionState

# ---------------------------------------------------------------------------
# ConnectionState
# ---------------------------------------------------------------------------


class TestConnectionState:
    """Tests for :class:`ConnectionState`."""

    def test_values(self) -> None:
        assert ConnectionState.DISCONNECTED == "disconnected"
        assert ConnectionState.CONNECTING == "connecting"
        assert ConnectionState.CONNECTED == "connected"
        assert ConnectionState.CLOSING == "closing"
        assert ConnectionState.CLOSED == "closed"

    def test_is_str(self) -> None:
        for state in ConnectionState:
            assert isinstance(state, str)

    def test_all_states_present(self) -> None:
        assert len(ConnectionState) == 5


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------


class TestSessionState:
    """Tests for :class:`SessionState`."""

    def test_values(self) -> None:
        assert SessionState.DETACHED == "detached"
        assert SessionState.ATTACHING == "attaching"
        assert SessionState.ATTACHED == "attached"
        assert SessionState.DETACHING == "detaching"

    def test_is_str(self) -> None:
        for state in SessionState:
            assert isinstance(state, str)

    def test_all_states_present(self) -> None:
        assert len(SessionState) == 4


# ---------------------------------------------------------------------------
# CDPTarget
# ---------------------------------------------------------------------------


class TestCDPTarget:
    """Tests for :class:`CDPTarget`."""

    def test_construction(self) -> None:
        target = CDPTarget(id="ABC", type="page", title="Test", url="http://example.com")
        assert target.id == "ABC"
        assert target.type == "page"
        assert target.title == "Test"
        assert target.url == "http://example.com"
        assert target.web_socket_debugger_url == ""

    def test_from_dict(self) -> None:
        data = {
            "id": "DEF",
            "type": "page",
            "title": "My Page",
            "url": "https://example.com",
            "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/DEF",
        }
        target = CDPTarget.from_dict(data)
        assert target.id == "DEF"
        assert target.type == "page"
        assert target.title == "My Page"
        assert target.url == "https://example.com"
        assert target.web_socket_debugger_url == "ws://localhost:9222/devtools/page/DEF"

    def test_from_dict_missing_fields(self) -> None:
        target = CDPTarget.from_dict({})
        assert target.id == ""
        assert target.type == ""
        assert target.title == ""
        assert target.url == ""
        assert target.web_socket_debugger_url == ""

    def test_from_dict_partial(self) -> None:
        data: dict[str, Any] = {"id": "XYZ", "type": "service_worker"}
        target = CDPTarget.from_dict(data)
        assert target.id == "XYZ"
        assert target.type == "service_worker"
        assert target.title == ""
        assert target.url == ""

    def test_frozen(self) -> None:
        target = CDPTarget(id="ABC", type="page")
        with pytest.raises(AttributeError):
            target.id = "DEF"  # type: ignore[misc]

    def test_default_values(self) -> None:
        target = CDPTarget(id="ABC", type="page")
        assert target.title == ""
        assert target.url == ""
        assert target.web_socket_debugger_url == ""

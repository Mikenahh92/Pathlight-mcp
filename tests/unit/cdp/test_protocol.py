"""Tests for CDP message protocol (protocol.py).

Validates the data structures and helpers for constructing CDP commands,
parsing CDP responses and events, and validating the message protocol.
"""

import pytest

from pathlight_mcp.cdp.protocol import (
    CDPError,
    CDPEvent,
    CDPMessage,
    CDPResponse,
    parse_cdp_message,
)

# ---------------------------------------------------------------------------
# CDPMessage
# ---------------------------------------------------------------------------


class TestCDPMessage:
    """Tests for :class:`CDPMessage`."""

    def test_to_dict_with_params(self) -> None:
        msg = CDPMessage(id=1, method="Page.navigate", params={"url": "https://example.com"})
        d = msg.to_dict()
        assert d == {"id": 1, "method": "Page.navigate", "params": {"url": "https://example.com"}}

    def test_to_dict_without_params(self) -> None:
        msg = CDPMessage(id=2, method="Page.enable")
        d = msg.to_dict()
        assert d == {"id": 2, "method": "Page.enable"}

    def test_to_dict_empty_params_omitted(self) -> None:
        msg = CDPMessage(id=3, method="Runtime.enable", params={})
        d = msg.to_dict()
        assert "params" not in d

    def test_frozen(self) -> None:
        msg = CDPMessage(id=1, method="test")
        with pytest.raises(AttributeError):
            msg.id = 2  # type: ignore[misc]

    def test_default_params_is_empty_dict(self) -> None:
        msg = CDPMessage(id=1, method="test")
        assert msg.params == {}

    def test_to_dict_with_session_id(self) -> None:
        msg = CDPMessage(
            id=4, method="Page.navigate",
            params={"url": "https://example.com"},
            session_id="sess-abc",
        )
        d = msg.to_dict()
        assert d == {
            "id": 4,
            "method": "Page.navigate",
            "sessionId": "sess-abc",
            "params": {"url": "https://example.com"},
        }

    def test_to_dict_with_session_id_no_params(self) -> None:
        msg = CDPMessage(id=5, method="Page.enable", session_id="sess-xyz")
        d = msg.to_dict()
        assert d == {"id": 5, "method": "Page.enable", "sessionId": "sess-xyz"}
        assert "params" not in d

    def test_to_dict_without_session_id(self) -> None:
        msg = CDPMessage(id=6, method="Page.enable")
        d = msg.to_dict()
        assert "sessionId" not in d


# ---------------------------------------------------------------------------
# CDPResponse
# ---------------------------------------------------------------------------


class TestCDPResponse:
    """Tests for :class:`CDPResponse`."""

    def test_from_dict_success(self) -> None:
        data = {"id": 1, "result": {"frameId": "main"}}
        resp = CDPResponse.from_dict(data)
        assert resp.id == 1
        assert resp.result == {"frameId": "main"}
        assert resp.error is None
        assert not resp.is_error

    def test_from_dict_error(self) -> None:
        data = {"id": 2, "error": {"code": -32000, "message": "Not found"}}
        resp = CDPResponse.from_dict(data)
        assert resp.id == 2
        assert resp.result is None
        assert resp.error == {"code": -32000, "message": "Not found"}
        assert resp.is_error

    def test_from_dict_missing_id_raises(self) -> None:
        with pytest.raises(ValueError, match="missing 'id' field"):
            CDPResponse.from_dict({"result": {}})

    def test_raise_for_error_raises_cdp_error(self) -> None:
        resp = CDPResponse(id=1, error={"code": -32000, "message": "Not found"})
        with pytest.raises(CDPError, match="CDP error -32000: Not found") as exc_info:
            resp.raise_for_error()
        assert exc_info.value.code == -32000
        assert exc_info.value.message == "Not found"

    def test_raise_for_error_success_noop(self) -> None:
        resp = CDPResponse(id=1, result={})
        resp.raise_for_error()  # should not raise

    def test_frozen(self) -> None:
        resp = CDPResponse(id=1, result={})
        with pytest.raises(AttributeError):
            resp.id = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CDPEvent
# ---------------------------------------------------------------------------


class TestCDPEvent:
    """Tests for :class:`CDPEvent`."""

    def test_from_dict(self) -> None:
        data = {"method": "Page.loadEventFired", "params": {"timestamp": 12345.0}}
        event = CDPEvent.from_dict(data)
        assert event.method == "Page.loadEventFired"
        assert event.params == {"timestamp": 12345.0}

    def test_from_dict_no_params(self) -> None:
        data = {"method": "Runtime.executionContextCreated"}
        event = CDPEvent.from_dict(data)
        assert event.method == "Runtime.executionContextCreated"
        assert event.params == {}

    def test_from_dict_empty_method(self) -> None:
        event = CDPEvent.from_dict({})
        assert event.method == ""
        assert event.params == {}

    def test_frozen(self) -> None:
        event = CDPEvent(method="test", params={})
        with pytest.raises(AttributeError):
            event.method = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# parse_cdp_message
# ---------------------------------------------------------------------------


class TestParseCDPMessage:
    """Tests for :func:`parse_cdp_message`."""

    def test_parses_response_when_id_present(self) -> None:
        data = {"id": 1, "result": {"value": True}}
        msg = parse_cdp_message(data)
        assert isinstance(msg, CDPResponse)
        assert msg.id == 1
        assert msg.result == {"value": True}

    def test_parses_event_when_id_absent(self) -> None:
        data = {"method": "Page.loadEventFired", "params": {}}
        msg = parse_cdp_message(data)
        assert isinstance(msg, CDPEvent)
        assert msg.method == "Page.loadEventFired"

    def test_parses_error_response(self) -> None:
        data = {"id": 3, "error": {"code": -32600, "message": "Invalid Request"}}
        msg = parse_cdp_message(data)
        assert isinstance(msg, CDPResponse)
        assert msg.is_error


# ---------------------------------------------------------------------------
# CDPError
# ---------------------------------------------------------------------------


class TestCDPError:
    """Tests for :class:`CDPError`."""

    def test_attributes(self) -> None:
        err = CDPError(code=-32000, message="Not found")
        assert err.code == -32000
        assert err.message == "Not found"
        assert "CDP error -32000: Not found" in str(err)

    def test_is_exception(self) -> None:
        with pytest.raises(CDPError):
            raise CDPError(code=-1, message="test")

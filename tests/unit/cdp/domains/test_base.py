"""Tests for CDP domain base class (_base.py).

Validates :class:`CDPDomain` — error mapping, method name construction,
and command sending.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from pathlight_mcp.cdp._errors import map_cdp_error
from pathlight_mcp.cdp.domains._base import CDPDomain
from pathlight_mcp.cdp.protocol import CDPError
from pathlight_mcp.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
    ElementNotFoundError,
    PathlightMCPError,
    StaleElementReferenceError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubSession:
    """Minimal stub for CDPSession.send_command."""

    def __init__(self) -> None:
        self.responses: list[dict[str, Any]] = [{}]
        self._call_index = 0

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._call_index < len(self.responses):
            resp = self.responses[self._call_index]
            self._call_index += 1
            return resp
        return {}


class _FailingSession:
    """Session that raises CDPError."""

    def __init__(self, error: CDPError) -> None:
        self._error = error

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        raise self._error


class _ConnectionFailSession:
    """Session that raises ConnectionError."""

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        raise ConnectionError("Connection lost")


# ---------------------------------------------------------------------------
# Concrete test domain (CDPDomain is abstract-ish)
# ---------------------------------------------------------------------------


class _SampleDomain(CDPDomain):
    """Sample domain for unit testing."""

    domain = "Test"

    def __init__(self, session: Any) -> None:
        super().__init__(session)

    def do_something(self) -> dict[str, Any]:
        return self._send(self._method("doSomething"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCDPDomainMethod:
    """Tests for _method name construction."""

    def test_method_builds_fully_qualified_name(self) -> None:
        session = _StubSession()
        domain = _SampleDomain(session)
        assert domain._method("doSomething") == "Test.doSomething"

    def test_method_with_empty_domain(self) -> None:
        session = _StubSession()
        domain = CDPDomain(session)
        assert domain._method("test") == ".test"


class TestCDPDomainSend:
    """Tests for _send command sending."""

    def test_send_returns_result(self) -> None:
        session = _StubSession()
        session.responses = [{"data": "ok"}]
        domain = _SampleDomain(session)
        result = domain._send("Test.doSomething")
        assert result == {"data": "ok"}

    def test_send_passes_params_and_timeout(self) -> None:
        mock_session = MagicMock()
        mock_session.send_command.return_value = {"ok": True}
        domain = _SampleDomain(mock_session)
        result = domain._send("Test.cmd", {"key": "val"}, timeout=5.0)
        mock_session.send_command.assert_called_once_with("Test.cmd", {"key": "val"}, timeout=5.0)
        assert result == {"ok": True}


class TestCDPDomainErrorMapping:
    """Tests for error mapping from CDP errors to Pathlight MCP errors."""

    def test_not_found_maps_to_element_not_found(self) -> None:
        error = CDPError(code=-32000, message="Node not found")
        session = _FailingSession(error)
        domain = _SampleDomain(session)

        with pytest.raises(ElementNotFoundError):
            domain.do_something()

    def test_unsupported_maps_to_action_not_supported(self) -> None:
        error = CDPError(code=-32000, message="Action not supported")
        session = _FailingSession(error)
        domain = _SampleDomain(session)

        with pytest.raises(ActionNotSupportedError):
            domain.do_something()

    def test_generic_error_maps_to_pathlight_mcp_error(self) -> None:
        error = CDPError(code=-32600, message="Invalid request")
        session = _FailingSession(error)
        domain = _SampleDomain(session)

        with pytest.raises(PathlightMCPError, match="CDP error"):
            domain.do_something()

    def test_connection_error_maps_to_backend_unavailable(self) -> None:
        session = _ConnectionFailSession()
        domain = _SampleDomain(session)

        with pytest.raises(BackendUnavailableError):
            domain.do_something()


class TestMapCdpError:
    """Tests for map_cdp_error function."""

    def test_not_found_case_insensitive(self) -> None:
        error = CDPError(code=-32000, message="Target NOT FOUND")
        result = map_cdp_error(error)
        assert isinstance(result, ElementNotFoundError)

    def test_not_attached_maps_to_stale(self) -> None:
        error = CDPError(code=-32000, message="Node is not attached")
        result = map_cdp_error(error)
        assert isinstance(result, StaleElementReferenceError)

    def test_unsupported(self) -> None:
        error = CDPError(code=-32000, message="Not supported in this context")
        result = map_cdp_error(error)
        assert isinstance(result, ActionNotSupportedError)

    def test_method_not_found_maps_to_backend_unavailable(self) -> None:
        error = CDPError(code=-32601, message="Method not found")
        result = map_cdp_error(error)
        assert isinstance(result, BackendUnavailableError)

    def test_invalid_params_maps_to_pathlight_mcp_error(self) -> None:
        error = CDPError(code=-32602, message="Invalid params")
        result = map_cdp_error(error)
        assert isinstance(result, PathlightMCPError)
        assert not isinstance(result, ElementNotFoundError)

    def test_generic_code(self) -> None:
        error = CDPError(code=-99999, message="Unknown")
        result = map_cdp_error(error)
        assert isinstance(result, PathlightMCPError)

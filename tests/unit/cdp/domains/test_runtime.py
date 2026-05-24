"""Tests for CDP Runtime domain wrapper.

Validates :class:`RuntimeDomain` — evaluate, call_function_on,
get_properties, enable/disable.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from guidewire.cdp.domains.runtime import RuntimeDomain
from guidewire.errors import GuidewireError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(responses: list[dict[str, Any]] | None = None) -> MagicMock:
    session = MagicMock()
    responses = responses or [{}]
    session.send_command.side_effect = responses
    return session


# ---------------------------------------------------------------------------
# Evaluate tests
# ---------------------------------------------------------------------------


class TestRuntimeEvaluate:
    """Tests for evaluate."""

    def test_returns_value(self) -> None:
        response = {"result": {"type": "number", "value": 42}}
        session = _make_session([response])
        domain = RuntimeDomain(session)

        result = domain.evaluate("1 + 1")
        assert result == 42

    def test_returns_remote_object_when_not_by_value(self) -> None:
        response = {"result": {"type": "object", "objectId": "obj-1"}}
        session = _make_session([response])
        domain = RuntimeDomain(session)

        result = domain.evaluate("document", return_by_value=False)
        assert result["objectId"] == "obj-1"

    def test_raises_on_exception(self) -> None:
        response = {
            "result": {"type": "object"},
            "exceptionDetails": {
                "text": "SyntaxError",
                "exception": {"description": "Unexpected token"},
            },
        }
        session = _make_session([response])
        domain = RuntimeDomain(session)

        with pytest.raises(GuidewireError, match="SyntaxError"):
            domain.evaluate("invalid js!")

    def test_sends_correct_params(self) -> None:
        session = _make_session([{"result": {"type": "undefined"}}])
        domain = RuntimeDomain(session)

        domain.evaluate("test", await_promise=True, timeout=10.0)

        call_args = session.send_command.call_args
        assert call_args[0][0] == "Runtime.evaluate"
        params = call_args[0][1]
        assert params["expression"] == "test"
        assert params["awaitPromise"] is True
        assert params["returnByValue"] is True

    def test_evaluate_none_value(self) -> None:
        response = {"result": {"type": "undefined"}}
        session = _make_session([response])
        domain = RuntimeDomain(session)

        result = domain.evaluate("undefined")
        assert result is None


# ---------------------------------------------------------------------------
# Call function on tests
# ---------------------------------------------------------------------------


class TestRuntimeCallFunctionOn:
    """Tests for call_function_on."""

    def test_calls_function_with_object_id(self) -> None:
        response = {"result": {"type": "string", "value": "hello"}}
        session = _make_session([response])
        domain = RuntimeDomain(session)

        result = domain.call_function_on(
            "() => this.textContent",
            object_id="obj-1",
        )
        assert result == "hello"

    def test_sends_arguments(self) -> None:
        session = _make_session([{"result": {"type": "undefined"}}])
        domain = RuntimeDomain(session)

        domain.call_function_on(
            "function(x) { return x; }",
            arguments=[{"value": 42}],
        )

        call_args = session.send_command.call_args
        params = call_args[0][1]
        assert params["arguments"] == [{"value": 42}]

    def test_raises_on_exception(self) -> None:
        response = {
            "result": {"type": "object"},
            "exceptionDetails": {
                "text": "TypeError",
                "exception": {"description": "Cannot read prop"},
            },
        }
        session = _make_session([response])
        domain = RuntimeDomain(session)

        with pytest.raises(GuidewireError, match="TypeError"):
            domain.call_function_on("bad()", object_id="obj-1")


# ---------------------------------------------------------------------------
# Get properties tests
# ---------------------------------------------------------------------------


class TestRuntimeGetProperties:
    """Tests for get_properties."""

    def test_returns_property_dict(self) -> None:
        response = {
            "result": [
                {"name": "foo", "value": {"type": "number", "value": 1}},
                {"name": "bar", "value": {"type": "string", "value": "baz"}},
            ]
        }
        session = _make_session([response])
        domain = RuntimeDomain(session)

        props = domain.get_properties("obj-1")
        assert "foo" in props
        assert "bar" in props

    def test_filters_dunder_properties(self) -> None:
        response = {
            "result": [
                {"name": "proto", "value": {}},
                {"name": "__proto__", "value": {}},
            ]
        }
        session = _make_session([response])
        domain = RuntimeDomain(session)

        props = domain.get_properties("obj-1")
        assert "proto" in props
        assert "__proto__" not in props

    def test_sends_own_properties(self) -> None:
        session = _make_session([{"result": []}])
        domain = RuntimeDomain(session)

        domain.get_properties("obj-1", own_properties=True)

        call_args = session.send_command.call_args
        params = call_args[0][1]
        assert params["ownProperties"] is True
        assert params["objectId"] == "obj-1"


# ---------------------------------------------------------------------------
# Enable/Disable tests
# ---------------------------------------------------------------------------


class TestRuntimeEnableDisable:
    """Tests for enable/disable."""

    def test_enable(self) -> None:
        session = _make_session([{}])
        domain = RuntimeDomain(session)
        domain.enable()
        session.send_command.assert_called_with(
            "Runtime.enable", None, timeout=None
        )

    def test_disable(self) -> None:
        session = _make_session([{}])
        domain = RuntimeDomain(session)
        domain.disable()
        session.send_command.assert_called_with(
            "Runtime.disable", None, timeout=None
        )

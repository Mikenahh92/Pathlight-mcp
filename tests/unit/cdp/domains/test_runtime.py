"""Tests for CDP Runtime domain wrapper.

Validates :class:`RuntimeDomain` — evaluate, call_function_on,
get_properties, enable/disable, and auto-enable before evaluate (GW-115).
"""

from typing import Any
from unittest.mock import MagicMock, call

import pytest

from pathlight_mcp.cdp.domains.runtime import RuntimeDomain
from pathlight_mcp.errors import PathlightMCPError

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
        # First call = Runtime.enable, second call = Runtime.evaluate
        session = _make_session([{}, {"result": {"type": "number", "value": 42}}])
        domain = RuntimeDomain(session)

        result = domain.evaluate("1 + 1")
        assert result == 42

    def test_returns_remote_object_when_not_by_value(self) -> None:
        session = _make_session([{}, {"result": {"type": "object", "objectId": "obj-1"}}])
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
        session = _make_session([{}, response])
        domain = RuntimeDomain(session)

        with pytest.raises(PathlightMCPError, match="SyntaxError"):
            domain.evaluate("invalid js!")

    def test_sends_correct_params(self) -> None:
        session = _make_session([{}, {"result": {"type": "undefined"}}])
        domain = RuntimeDomain(session)

        domain.evaluate("test", await_promise=True, timeout=10.0)

        # Second call should be the evaluate
        evaluate_call = session.send_command.call_args_list[1]
        assert evaluate_call[0][0] == "Runtime.evaluate"
        params = evaluate_call[0][1]
        assert params["expression"] == "test"
        assert params["awaitPromise"] is True
        assert params["returnByValue"] is True

    def test_evaluate_none_value(self) -> None:
        session = _make_session([{}, {"result": {"type": "undefined"}}])
        domain = RuntimeDomain(session)

        result = domain.evaluate("undefined")
        assert result is None

    def test_auto_enables_before_first_evaluate(self) -> None:
        """evaluate() calls Runtime.enable before the first evaluate (GW-115)."""
        session = _make_session([{}, {"result": {"type": "number", "value": 1}}])
        domain = RuntimeDomain(session)

        domain.evaluate("1")

        assert session.send_command.call_count == 2
        calls = session.send_command.call_args_list
        assert calls[0][0][0] == "Runtime.enable"
        assert calls[1][0][0] == "Runtime.evaluate"

    def test_does_not_re_enable_on_subsequent_calls(self) -> None:
        """evaluate() does not call Runtime.enable again after first call (GW-115)."""
        session = _make_session([
            {},  # Runtime.enable
            {"result": {"type": "number", "value": 1}},  # evaluate 1
            {"result": {"type": "number", "value": 2}},  # evaluate 2
        ])
        domain = RuntimeDomain(session)

        domain.evaluate("1")
        domain.evaluate("2")

        assert session.send_command.call_count == 3
        calls = session.send_command.call_args_list
        assert calls[0][0][0] == "Runtime.enable"
        assert calls[1][0][0] == "Runtime.evaluate"
        assert calls[2][0][0] == "Runtime.evaluate"

    def test_explicit_enable_prevents_auto_enable(self) -> None:
        """If enable() is called explicitly, evaluate() skips the auto-enable."""
        session = _make_session([
            {},  # explicit enable()
            {"result": {"type": "number", "value": 42}},  # evaluate
        ])
        domain = RuntimeDomain(session)

        domain.enable()
        domain.evaluate("42")

        assert session.send_command.call_count == 2
        calls = session.send_command.call_args_list
        assert calls[0][0][0] == "Runtime.enable"
        assert calls[1][0][0] == "Runtime.evaluate"


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

        with pytest.raises(PathlightMCPError, match="TypeError"):
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

    def test_enable_sets_enabled_flag(self) -> None:
        """enable() sets _enabled flag to True (GW-115)."""
        session = _make_session([{}])
        domain = RuntimeDomain(session)
        assert domain._enabled is False
        domain.enable()
        assert domain._enabled is True

    def test_disable_resets_enabled_flag(self) -> None:
        """disable() resets _enabled flag to False (GW-115)."""
        session = _make_session([{}, {}])
        domain = RuntimeDomain(session)
        domain.enable()
        assert domain._enabled is True
        domain.disable()
        assert domain._enabled is False

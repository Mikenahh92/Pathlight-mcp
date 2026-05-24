"""Tests for CDP Target domain wrapper.

Validates :class:`TargetDomain` — discover targets, get targets,
activate target, create/close targets, auto-attach, etc.
"""

from typing import Any
from unittest.mock import MagicMock

from guidewire.cdp._types import CDPTarget
from guidewire.cdp.domains.target import TargetDomain

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(responses: list[dict[str, Any]] | None = None) -> MagicMock:
    session = MagicMock()
    responses = responses or [{}]
    session.send_command.side_effect = responses
    return session


# ---------------------------------------------------------------------------
# Set discover targets
# ---------------------------------------------------------------------------


class TestTargetSetDiscoverTargets:
    """Tests for set_discover_targets."""

    def test_enable(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.set_discover_targets(True)

        session.send_command.assert_called_with(
            "Target.setDiscoverTargets",
            {"discover": True},
            timeout=None,
        )

    def test_disable(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.set_discover_targets(False)

        session.send_command.assert_called_with(
            "Target.setDiscoverTargets",
            {"discover": False},
            timeout=None,
        )

    def test_default_is_true(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.set_discover_targets()

        params = session.send_command.call_args[0][1]
        assert params["discover"] is True


# ---------------------------------------------------------------------------
# Get targets
# ---------------------------------------------------------------------------


class TestTargetGetTargets:
    """Tests for get_targets."""

    def test_returns_cdp_targets(self) -> None:
        response = {
            "targetInfos": [
                {
                    "targetId": "target-1",
                    "type": "page",
                    "title": "Test Page",
                    "url": "https://example.com",
                    "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/target-1",
                },
                {
                    "targetId": "target-2",
                    "type": "service_worker",
                    "title": "SW",
                    "url": "https://example.com/sw.js",
                    "webSocketDebuggerUrl": "",
                },
            ]
        }
        session = _make_session([response])
        domain = TargetDomain(session)

        targets = domain.get_targets()

        assert len(targets) == 2
        assert isinstance(targets[0], CDPTarget)
        assert targets[0].id == "target-1"
        assert targets[0].type == "page"
        assert targets[1].id == "target-2"

    def test_empty_targets(self) -> None:
        session = _make_session([{"targetInfos": []}])
        domain = TargetDomain(session)

        targets = domain.get_targets()
        assert targets == []


# ---------------------------------------------------------------------------
# Activate target
# ---------------------------------------------------------------------------


class TestTargetActivateTarget:
    """Tests for activate_target."""

    def test_activates(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.activate_target("target-1")

        session.send_command.assert_called_with(
            "Target.activateTarget",
            {"targetId": "target-1"},
            timeout=None,
        )


# ---------------------------------------------------------------------------
# Create target
# ---------------------------------------------------------------------------


class TestTargetCreateTarget:
    """Tests for create_target."""

    def test_returns_target_id(self) -> None:
        session = _make_session([{"targetId": "new-target"}])
        domain = TargetDomain(session)

        target_id = domain.create_target("https://example.com")

        assert target_id == "new-target"
        call_args = session.send_command.call_args
        assert call_args[0][1] == {"url": "https://example.com"}


# ---------------------------------------------------------------------------
# Close target
# ---------------------------------------------------------------------------


class TestTargetCloseTarget:
    """Tests for close_target."""

    def test_returns_success(self) -> None:
        session = _make_session([{"success": True}])
        domain = TargetDomain(session)

        result = domain.close_target("target-1")
        assert result is True

    def test_returns_false_on_failure(self) -> None:
        session = _make_session([{"success": False}])
        domain = TargetDomain(session)

        result = domain.close_target("target-1")
        assert result is False


# ---------------------------------------------------------------------------
# Get target info
# ---------------------------------------------------------------------------


class TestTargetGetTargetInfo:
    """Tests for get_target_info."""

    def test_returns_target_info(self) -> None:
        response = {
            "targetInfo": {
                "targetId": "target-1",
                "type": "page",
                "title": "Test",
                "url": "https://example.com",
            }
        }
        session = _make_session([response])
        domain = TargetDomain(session)

        target = domain.get_target_info("target-1")

        assert isinstance(target, CDPTarget)
        assert target.id == "target-1"

    def test_returns_none_when_no_info(self) -> None:
        session = _make_session([{}])
        domain = TargetDomain(session)

        target = domain.get_target_info("nonexistent")
        assert target is None


# ---------------------------------------------------------------------------
# Auto-attach
# ---------------------------------------------------------------------------


class TestTargetSetAutoAttach:
    """Tests for set_auto_attach."""

    def test_default_params(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.set_auto_attach()

        params = session.send_command.call_args[0][1]
        assert params["autoAttach"] is True
        assert params["waitForDebuggerOnStart"] is False
        assert params["flatten"] is True

    def test_custom_params(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.set_auto_attach(
            auto_attach=False,
            wait_for_debugger_on_start=True,
            flatten=False,
        )

        params = session.send_command.call_args[0][1]
        assert params["autoAttach"] is False
        assert params["waitForDebuggerOnStart"] is True
        assert params["flatten"] is False


# ---------------------------------------------------------------------------
# Attach/Detach
# ---------------------------------------------------------------------------


class TestTargetAttachDetach:
    """Tests for attach_to_target and detach_from_target."""

    def test_attach_returns_session_id(self) -> None:
        session = _make_session([{"sessionId": "sess-123"}])
        domain = TargetDomain(session)

        session_id = domain.attach_to_target("target-1")

        assert session_id == "sess-123"
        params = session.send_command.call_args[0][1]
        assert params["targetId"] == "target-1"
        assert params["flatten"] is True

    def test_attach_no_flatten(self) -> None:
        session = _make_session([{"sessionId": "sess-456"}])
        domain = TargetDomain(session)

        domain.attach_to_target("target-1", flatten=False)

        params = session.send_command.call_args[0][1]
        assert params["flatten"] is False

    def test_detach_with_session_id(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.detach_from_target(session_id="sess-123")

        params = session.send_command.call_args[0][1]
        assert params["sessionId"] == "sess-123"

    def test_detach_without_session_id(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.detach_from_target()

        params = session.send_command.call_args[0][1]
        assert params is None


# ---------------------------------------------------------------------------
# Expose DevTools protocol
# ---------------------------------------------------------------------------


class TestTargetExposeDevToolsProtocol:
    """Tests for expose_dev_tools_protocol."""

    def test_expose_with_default_binding(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.expose_dev_tools_protocol("target-1")

        params = session.send_command.call_args[0][1]
        assert params["targetId"] == "target-1"
        assert params["bindingName"] == "cdp"

    def test_expose_with_custom_binding(self) -> None:
        session = _make_session()
        domain = TargetDomain(session)

        domain.expose_dev_tools_protocol("target-1", binding_name="custom")

        params = session.send_command.call_args[0][1]
        assert params["bindingName"] == "custom"

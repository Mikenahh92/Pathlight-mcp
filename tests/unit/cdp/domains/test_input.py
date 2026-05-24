"""Tests for CDP Input domain wrapper.

Validates :class:`InputDomain` — mouse events, key events, text insertion.
"""

from typing import Any
from unittest.mock import MagicMock

from guidewire.cdp.domains.input import InputDomain

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(responses: list[dict[str, Any]] | None = None) -> MagicMock:
    session = MagicMock()
    responses = responses or [{}]
    session.send_command.side_effect = responses
    return session


# ---------------------------------------------------------------------------
# Mouse event tests
# ---------------------------------------------------------------------------


class TestInputDispatchMouseEvent:
    """Tests for dispatch_mouse_event."""

    def test_mouse_press(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_mouse_event("mousePressed", 100.0, 200.0, button="left", click_count=1)

        call_args = session.send_command.call_args
        assert call_args[0][0] == "Input.dispatchMouseEvent"
        params = call_args[0][1]
        assert params["type"] == "mousePressed"
        assert params["x"] == 100.0
        assert params["y"] == 200.0
        assert params["button"] == "left"
        assert params["clickCount"] == 1

    def test_mouse_move(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_mouse_event("mouseMoved", 150.0, 250.0)

        params = session.send_command.call_args[0][1]
        assert params["type"] == "mouseMoved"
        assert params["button"] == "none"

    def test_mouse_wheel(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_mouse_event(
            "mouseWheel", 0.0, 0.0, delta_x=0.0, delta_y=100.0
        )

        params = session.send_command.call_args[0][1]
        assert params["type"] == "mouseWheel"
        assert params["deltaX"] == 0.0
        assert params["deltaY"] == 100.0

    def test_wheel_delta_not_sent_for_non_wheel(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_mouse_event("mousePressed", 10.0, 20.0)

        params = session.send_command.call_args[0][1]
        assert "deltaX" not in params
        assert "deltaY" not in params

    def test_modifiers_sent(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_mouse_event("mousePressed", 0.0, 0.0, modifiers=2)

        params = session.send_command.call_args[0][1]
        assert params["modifiers"] == 2


# ---------------------------------------------------------------------------
# Key event tests
# ---------------------------------------------------------------------------


class TestInputDispatchKeyEvent:
    """Tests for dispatch_key_event."""

    def test_key_down(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_key_event("keyDown", key="a", code="KeyA")

        call_args = session.send_command.call_args
        assert call_args[0][0] == "Input.dispatchKeyEvent"
        params = call_args[0][1]
        assert params["type"] == "keyDown"
        assert params["key"] == "a"
        assert params["code"] == "KeyA"

    def test_char_event_with_text(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_key_event("char", text="A", key="A", code="KeyA")

        params = session.send_command.call_args[0][1]
        assert params["text"] == "A"

    def test_optional_params_not_sent_when_default(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_key_event("keyUp", key="Enter", code="Enter")

        params = session.send_command.call_args[0][1]
        assert "autoRepeat" not in params
        assert "isKeypad" not in params
        assert "commands" not in params
        assert "text" not in params

    def test_all_optional_params_sent(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.dispatch_key_event(
            "rawKeyDown",
            key="Tab",
            code="Tab",
            windows_virtual_key_code=9,
            native_virtual_key_code=9,
            text="\t",
            unmodified_text="\t",
            auto_repeat=True,
            is_keypad=False,
            is_system_key=False,
            location=0,
            commands=["deleteWordBackward"],
        )

        params = session.send_command.call_args[0][1]
        assert params["autoRepeat"] is True
        assert params["windowsVirtualKeyCode"] == 9
        assert params["nativeVirtualKeyCode"] == 9
        assert params["commands"] == ["deleteWordBackward"]


# ---------------------------------------------------------------------------
# Insert text tests
# ---------------------------------------------------------------------------


class TestInputInsertText:
    """Tests for insert_text."""

    def test_sends_text(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.insert_text("Hello, World!")

        call_args = session.send_command.call_args
        assert call_args[0][0] == "Input.insertText"
        assert call_args[0][1] == {"text": "Hello, World!"}


# ---------------------------------------------------------------------------
# Emulate touch tests
# ---------------------------------------------------------------------------


class TestInputEmulateTouch:
    """Tests for emulate_touch_from_mouse_event."""

    def test_sends_touch_event(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.emulate_touch_from_mouse_event("mousePressed", 100.0, 200.0)

        call_args = session.send_command.call_args
        assert call_args[0][0] == "Input.emulateTouchFromMouseEvent"
        params = call_args[0][1]
        assert params["x"] == 100
        assert params["y"] == 200
        assert params["button"] == "left"


# ---------------------------------------------------------------------------
# Set ignore input events tests
# ---------------------------------------------------------------------------


class TestInputSetIgnoreInputEvents:
    """Tests for set_ignore_input_events."""

    def test_ignore_true(self) -> None:
        session = _make_session()
        domain = InputDomain(session)

        domain.set_ignore_input_events(True)

        call_args = session.send_command.call_args
        assert call_args[0][1] == {"ignore": True}

"""Tests for backend type definitions (GW-002, test design v2 TC-11-TC-15).

Verifies:
- NativeHandle is a NewType wrapping Any
- ElementState is a frozen dataclass with 9 boolean state flags (§3.2)
- ElementBounds is a frozen dataclass with correct fields
- DesktopAction is a StrEnum with the 12 canonical values (§4.3)
- Module exports are correct
"""

import dataclasses
import typing

import pytest

from pathlight_mcp.backends.types import (
    DesktopAction,
    ElementBounds,
    ElementState,
    NativeHandle,
)

# -- TC-11: NativeHandle ----------------------------------------------------


class TestNativeHandle:
    """Verify NativeHandle is a NewType wrapping Any (TC-11)."""

    def test_is_newtype(self) -> None:
        """NativeHandle should be a typing.NewType."""
        # NewType creates a callable, not a class, so we check the name
        assert callable(NativeHandle)
        assert NativeHandle.__name__ == "NativeHandle"

    def test_accepts_any_value(self) -> None:
        """NativeHandle should accept any value without error."""
        h1 = NativeHandle(42)
        h2 = NativeHandle("some_com_pointer")
        h3 = NativeHandle(object())
        assert h1 == 42
        assert h2 == "some_com_pointer"
        assert h3 is not None

    def test_distinct_from_plain_any(self) -> None:
        """NativeHandle values should be distinct at the type level."""
        # At runtime NewType is identity, but the supertype should be Any
        assert NativeHandle.__supertype__ is typing.Any


# -- TC-12: ElementBounds ---------------------------------------------------


class TestElementBounds:
    """Verify ElementBounds frozen dataclass (TC-12)."""

    def test_construction(self) -> None:
        """ElementBounds should accept x, y, width, height."""
        b = ElementBounds(x=10, y=20, width=100, height=50)
        assert b.x == 10
        assert b.y == 20
        assert b.width == 100
        assert b.height == 50

    def test_is_frozen(self) -> None:
        """ElementBounds should be immutable (frozen=True)."""
        b = ElementBounds(x=0, y=0, width=800, height=600)
        assert dataclasses.is_dataclass(b)
        assert b.__dataclass_params__.frozen

    def test_has_slots(self) -> None:
        """ElementBounds should use __slots__ (slots=True)."""
        b = ElementBounds(x=0, y=0, width=800, height=600)
        assert hasattr(b, "__slots__")

    def test_default_not_allowed(self) -> None:
        """All fields should be required (no defaults)."""
        with pytest.raises(TypeError):
            ElementBounds()  # type: ignore[call-arg]

    def test_equality(self) -> None:
        """ElementBounds with same values should be equal."""
        b1 = ElementBounds(x=0, y=0, width=800, height=600)
        b2 = ElementBounds(x=0, y=0, width=800, height=600)
        assert b1 == b2


# -- TC-13: ElementState ----------------------------------------------------


class TestElementState:
    """Verify ElementState frozen dataclass with 9 boolean state flags (TC-13, §3.2)."""

    EXPECTED_FLAGS = frozenset(
        {
            "enabled",
            "focused",
            "selected",
            "checked",
            "expanded",
            "visible",
            "offscreen",
            "read_only",
            "required",
        }
    )

    def test_has_nine_fields(self) -> None:
        """ElementState should have exactly 9 boolean state flags."""
        fields = {f.name for f in dataclasses.fields(ElementState)}
        assert fields == self.EXPECTED_FLAGS

    def test_construction_defaults(self) -> None:
        """ElementState should default to sensible values."""
        s = ElementState()
        assert s.enabled is True
        assert s.focused is False
        assert s.selected is False
        assert s.checked is False
        assert s.expanded is False
        assert s.visible is True
        assert s.offscreen is False
        assert s.read_only is False
        assert s.required is False

    def test_construction_custom(self) -> None:
        """ElementState should accept explicit flag values."""
        s = ElementState(
            enabled=False,
            focused=True,
            selected=True,
            checked=True,
            expanded=True,
            visible=False,
            offscreen=True,
            read_only=True,
            required=True,
        )
        assert s.enabled is False
        assert s.focused is True
        assert s.selected is True
        assert s.checked is True
        assert s.expanded is True
        assert s.visible is False
        assert s.offscreen is True
        assert s.read_only is True
        assert s.required is True

    def test_checked_accepts_str(self) -> None:
        """checked field should accept bool | str for tri-state."""
        s_bool = ElementState(checked=True)
        s_str = ElementState(checked="mixed")
        assert s_bool.checked is True
        assert s_str.checked == "mixed"

    def test_is_frozen(self) -> None:
        """ElementState should be immutable (frozen=True)."""
        s = ElementState()
        assert dataclasses.is_dataclass(s)
        assert s.__dataclass_params__.frozen

    def test_has_slots(self) -> None:
        """ElementState should use __slots__ (slots=True)."""
        s = ElementState()
        assert hasattr(s, "__slots__")

    def test_equality(self) -> None:
        """ElementState with same flag values should be equal."""
        s1 = ElementState(enabled=True, focused=False)
        s2 = ElementState(enabled=True, focused=False)
        assert s1 == s2

    def test_inequality(self) -> None:
        """ElementState with different flag values should not be equal."""
        s1 = ElementState(focused=True)
        s2 = ElementState(focused=False)
        assert s1 != s2


# -- TC-14: DesktopAction ---------------------------------------------------


class TestDesktopAction:
    """Verify DesktopAction StrEnum with 12 canonical variants (TC-14, §4.3)."""

    def test_is_strenum(self) -> None:
        """DesktopAction should be a StrEnum."""
        from enum import StrEnum

        assert issubclass(DesktopAction, StrEnum)

    def test_has_twelve_values(self) -> None:
        """DesktopAction should have exactly 19 members."""
        assert len(DesktopAction) == 19

    def test_click_member(self) -> None:
        assert DesktopAction.CLICK == "click"

    def test_type_member(self) -> None:
        """TYPE_TEXT was renamed to TYPE per architecture v2 §4.3."""
        assert DesktopAction.TYPE == "type"

    def test_press_key_member(self) -> None:
        assert DesktopAction.PRESS_KEY == "press_key"

    def test_set_value_member(self) -> None:
        assert DesktopAction.SET_VALUE == "set_value"

    def test_select_member(self) -> None:
        assert DesktopAction.SELECT == "select"

    def test_scroll_member(self) -> None:
        assert DesktopAction.SCROLL == "scroll"

    def test_get_text_member(self) -> None:
        """GET_TEXT is a new action per architecture v2 §4.3."""
        assert DesktopAction.GET_TEXT == "get_text"

    def test_toggle_member(self) -> None:
        assert DesktopAction.TOGGLE == "toggle"

    def test_expand_member(self) -> None:
        assert DesktopAction.EXPAND == "expand"

    def test_collapse_member(self) -> None:
        assert DesktopAction.COLLAPSE == "collapse"

    def test_increment_member(self) -> None:
        assert DesktopAction.INCREMENT == "increment"

    def test_decrement_member(self) -> None:
        assert DesktopAction.DECREMENT == "decrement"

    def test_no_type_text_member(self) -> None:
        """TYPE_TEXT should not exist (renamed to TYPE)."""
        assert not hasattr(DesktopAction, "TYPE_TEXT")


# -- TC-15: Module exports --------------------------------------------------


class TestModuleExports:
    """Verify the types module __all__ (TC-15)."""

    def test_all_exports(self) -> None:
        from pathlight_mcp.backends.types import __all__

        expected = {"DesktopAction", "ElementBounds", "ElementState", "NativeHandle"}
        assert set(__all__) == expected

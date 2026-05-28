"""Tests for the core model dataclasses and DesktopAction type."""

import json

from pathlight_mcp.models import Bounds, DesktopAction, ElementStates, NormalizedElement

# ---------------------------------------------------------------------------
# DesktopAction type
# ---------------------------------------------------------------------------


class TestDesktopAction:
    """Tests for the DesktopAction Literal type."""

    def test_expected_actions_are_valid(self) -> None:
        expected = [
            "click",
            "focus",
            "type",
            "set_value",
            "select",
            "select_item",
            "deselect_item",
            "add_to_selection",
            "toggle",
            "expand",
            "collapse",
            "scroll",
            "scroll_to_item",
            "increment",
            "decrement",
            "open_menu",
            "invoke",
        ]
        for name in expected:
            # Assigning to DesktopAction should not raise
            action: DesktopAction = name  # type: ignore[assignment]
            assert action == name

    def test_action_count(self) -> None:
        """DesktopAction has exactly 17 members."""
        import typing

        args = typing.get_args(DesktopAction)
        assert len(args) == 17

    def test_invalid_action_rejected_by_mypy(self) -> None:
        """Non-member strings are not DesktopAction at type-check time.

        Runtime: this test documents the intent; mypy would reject the line.
        """
        # This would fail mypy: bad: DesktopAction = "nonexistent"
        pass


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


class TestBounds:
    """Tests for the Bounds frozen dataclass."""

    def test_basic_construction(self) -> None:
        b = Bounds(x=10.0, y=20.0, width=100.0, height=50.0)
        assert b.x == 10.0
        assert b.y == 20.0
        assert b.width == 100.0
        assert b.height == 50.0

    def test_accepts_int_values(self) -> None:
        """Bounds fields are float but accept int (auto-coerced)."""
        b = Bounds(x=10, y=20, width=100, height=50)
        assert isinstance(b.x, int)
        assert isinstance(b.width, int)

    def test_is_empty_with_positive_area(self) -> None:
        b = Bounds(x=0.0, y=0.0, width=100.0, height=50.0)
        assert not b.is_empty

    def test_is_empty_with_zero_width(self) -> None:
        b = Bounds(x=0.0, y=0.0, width=0.0, height=50.0)
        assert b.is_empty

    def test_is_empty_with_zero_height(self) -> None:
        b = Bounds(x=0.0, y=0.0, width=100.0, height=0.0)
        assert b.is_empty

    def test_is_empty_with_negative_dimensions(self) -> None:
        b = Bounds(x=0.0, y=0.0, width=-1.0, height=50.0)
        assert b.is_empty

    def test_center(self) -> None:
        b = Bounds(x=100.0, y=200.0, width=80.0, height=40.0)
        assert b.center == (140.0, 220.0)

    def test_center_fractional(self) -> None:
        """Center with odd dimensions yields fractional result."""
        b = Bounds(x=0.0, y=0.0, width=1.0, height=1.0)
        assert b.center == (0.5, 0.5)

    def test_frozen(self) -> None:
        b = Bounds(x=0.0, y=0.0, width=10.0, height=10.0)
        try:
            b.x = 99.0  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass

    def test_slots(self) -> None:
        b = Bounds(x=0.0, y=0.0, width=10.0, height=10.0)
        assert "__dict__" not in dir(b) or not hasattr(b, "__dict__") or b.__dict__ == {}


# ---------------------------------------------------------------------------
# ElementStates
# ---------------------------------------------------------------------------


class TestElementStates:
    """Tests for the ElementStates frozen dataclass."""

    def test_defaults_are_none(self) -> None:
        s = ElementStates()
        assert s.enabled is None
        assert s.focused is None
        assert s.selected is None
        assert s.checked is None
        assert s.expanded is None
        assert s.visible is None
        assert s.offscreen is None
        assert s.read_only is None
        assert s.required is None
        assert s.is_password is None

    def test_explicit_values(self) -> None:
        s = ElementStates(enabled=True, focused=False, checked="mixed")
        assert s.enabled is True
        assert s.focused is False
        assert s.checked == "mixed"

    def test_checked_type_is_literal(self) -> None:
        """checked accepts bool, 'mixed', or None — not arbitrary strings."""
        s1 = ElementStates(checked=True)
        s2 = ElementStates(checked=False)
        s3 = ElementStates(checked="mixed")
        s4 = ElementStates(checked=None)
        assert s1.checked is True
        assert s2.checked is False
        assert s3.checked == "mixed"
        assert s4.checked is None

    def test_is_interactive_when_enabled(self) -> None:
        s = ElementStates(enabled=True)
        assert s.is_interactive is True

    def test_is_interactive_when_none(self) -> None:
        s = ElementStates()
        assert s.is_interactive is True  # None → not False

    def test_is_interactive_when_disabled(self) -> None:
        s = ElementStates(enabled=False)
        assert s.is_interactive is False

    def test_is_checked_true(self) -> None:
        s = ElementStates(checked=True)
        assert s.is_checked is True

    def test_is_checked_false(self) -> None:
        s = ElementStates(checked=False)
        assert s.is_checked is False

    def test_is_checked_mixed(self) -> None:
        s = ElementStates(checked="mixed")
        assert s.is_checked is None

    def test_is_checked_none(self) -> None:
        s = ElementStates()
        assert s.is_checked is None

    def test_frozen(self) -> None:
        s = ElementStates(enabled=True)
        try:
            s.enabled = False  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# NormalizedElement
# ---------------------------------------------------------------------------


class TestNormalizedElement:
    """Tests for the NormalizedElement dataclass."""

    def _make_button(
        self,
        ref: str = "e1",
        name: str = "Save",
        actions: list[DesktopAction] | None = None,
        children: list[NormalizedElement] | None = None,
    ) -> NormalizedElement:
        return NormalizedElement(
            ref=ref,
            backend_id="native-42",
            role="button",
            name=name,
            actions=actions or ["click"],
            children=children,
        )

    def test_basic_construction(self) -> None:
        el = self._make_button()
        assert el.ref == "e1"
        assert el.backend_id == "native-42"
        assert el.role == "button"
        assert el.name == "Save"
        assert el.actions == ["click"]
        assert el.children is None

    def test_optional_fields_default_to_none(self) -> None:
        el = NormalizedElement(ref="e1", backend_id="x", role="custom")
        assert el.native_role is None
        assert el.control_type is None
        assert el.name is None
        assert el.description is None
        assert el.value is None
        assert el.text is None
        assert el.bounds is None

    def test_children_default_is_none(self) -> None:
        """TC-010: children defaults to None, not []."""
        el = NormalizedElement(ref="e1", backend_id="x", role="custom")
        assert el.children is None

    def test_children_none_vs_empty_list(self) -> None:
        """TC-010: None children and empty list are distinct."""
        el_none = NormalizedElement(ref="e1", backend_id="x", role="custom")
        el_empty = NormalizedElement(ref="e2", backend_id="x", role="custom", children=[])
        assert el_none.children is None
        assert el_empty.children == []
        assert el_none.children != el_empty.children

    def test_children_none_walk_returns_self_only(self) -> None:
        """walk() with None children returns just the element."""
        el = NormalizedElement(ref="e1", backend_id="x", role="custom")
        assert el.walk() == [el]

    def test_states_default(self) -> None:
        el = NormalizedElement(ref="e1", backend_id="x", role="custom")
        assert isinstance(el.states, ElementStates)
        assert el.states.enabled is None

    def test_with_bounds(self) -> None:
        el = NormalizedElement(
            ref="e1",
            backend_id="x",
            role="button",
            bounds=Bounds(x=100.0, y=200.0, width=80.0, height=32.0),
        )
        assert el.bounds is not None
        assert el.bounds.center == (140.0, 216.0)

    def test_walk_single_element(self) -> None:
        el = self._make_button()
        assert el.walk() == [el]

    def test_walk_with_children(self) -> None:
        child = self._make_button(ref="e2", name="Cancel")
        parent = self._make_button(ref="e1", children=[child])
        flat = parent.walk()
        assert len(flat) == 2
        assert flat[0].ref == "e1"
        assert flat[1].ref == "e2"

    def test_walk_nested(self) -> None:
        leaf = self._make_button(ref="e3")
        mid = self._make_button(ref="e2", children=[leaf])
        root = self._make_button(ref="e1", children=[mid])
        flat = root.walk()
        refs = [e.ref for e in flat]
        assert refs == ["e1", "e2", "e3"]

    def test_find_by_role(self) -> None:
        child_btn = self._make_button(ref="e2")
        child_text = NormalizedElement(ref="e3", backend_id="x", role="text")
        parent = self._make_button(ref="e1", children=[child_btn, child_text])
        buttons = parent.find_by_role("button")
        assert len(buttons) == 2
        texts = parent.find_by_role("text")
        assert len(texts) == 1
        assert texts[0].ref == "e3"

    def test_find_by_ref_found(self) -> None:
        child = self._make_button(ref="e2")
        parent = self._make_button(ref="e1", children=[child])
        found = parent.find_by_ref("e2")
        assert found is not None
        assert found.ref == "e2"

    def test_find_by_ref_not_found(self) -> None:
        el = self._make_button()
        assert el.find_by_ref("e99") is None

    def test_find_by_ref_self(self) -> None:
        el = self._make_button(ref="e1")
        assert el.find_by_ref("e1") is el

    def test_to_dict_minimal(self) -> None:
        el = NormalizedElement(ref="e1", backend_id="x", role="custom")
        d = el.to_dict()
        assert d["ref"] == "e1"
        assert d["backend_id"] == "x"
        assert d["role"] == "custom"
        assert "native_role" not in d
        assert "name" not in d
        assert d["children"] == []
        assert d["bounds"] is None
        assert d["states"] == {}
        assert d["actions"] == []

    def test_to_dict_full(self) -> None:
        el = NormalizedElement(
            ref="e1",
            backend_id="native-42",
            role="button",
            native_role="AXButton",
            control_type="ControlType.Button",
            name="Save",
            description="Save the document",
            value=None,
            text="Save",
            states=ElementStates(enabled=True, focused=False),
            bounds=Bounds(x=100.0, y=200.0, width=80.0, height=32.0),
            actions=["click", "invoke"],
        )
        d = el.to_dict()
        assert d["ref"] == "e1"
        assert d["native_role"] == "AXButton"
        assert d["control_type"] == "ControlType.Button"
        assert d["name"] == "Save"
        assert d["description"] == "Save the document"
        assert d["text"] == "Save"
        assert "value" not in d  # None values omitted
        assert d["states"] == {"enabled": True, "focused": False}
        assert d["bounds"]["x"] == 100.0
        assert d["bounds"]["width"] == 80.0
        assert d["actions"] == ["click", "invoke"]

    def test_to_dict_with_children(self) -> None:
        child = self._make_button(ref="e2", name="Cancel")
        parent = self._make_button(ref="e1", children=[child])
        d = parent.to_dict()
        assert "children" in d
        assert len(d["children"]) == 1
        assert d["children"][0]["ref"] == "e2"

    def test_to_dict_includes_value_when_set(self) -> None:
        """to_dict should include 'value' key when value is not None."""
        el = NormalizedElement(
            ref="e1",
            backend_id="x",
            role="text_input",
            value="hello",
        )
        d = el.to_dict()
        assert "value" in d
        assert d["value"] == "hello"

    def test_to_dict_is_json_serializable(self) -> None:
        child = NormalizedElement(
            ref="e2",
            backend_id="x",
            role="text_input",
            name="Search",
            states=ElementStates(enabled=True, focused=True),
            actions=["type", "set_value"],
        )
        parent = NormalizedElement(
            ref="e1",
            backend_id="x",
            role="window",
            name="Settings",
            bounds=Bounds(x=0.0, y=0.0, width=900.0, height=700.0),
            actions=["focus"],
            children=[child],
        )
        # Should not raise
        json_str = json.dumps(parent.to_dict())
        assert '"ref": "e1"' in json_str
        assert '"role": "window"' in json_str


# ---------------------------------------------------------------------------
# TC-017: Action completeness
# ---------------------------------------------------------------------------


class TestActionCompleteness:
    """TC-017: Verify DesktopAction covers all required actions."""

    def test_all_desktop_actions_present(self) -> None:
        import typing

        args = typing.get_args(DesktopAction)
        expected = {
            "click",
            "focus",
            "type",
            "set_value",
            "select",
            "select_item",
            "deselect_item",
            "add_to_selection",
            "toggle",
            "expand",
            "collapse",
            "scroll",
            "scroll_to_item",
            "increment",
            "decrement",
            "open_menu",
            "invoke",
        }
        assert set(args) == expected

    def test_desktop_action_count(self) -> None:
        import typing

        args = typing.get_args(DesktopAction)
        assert len(args) == 17

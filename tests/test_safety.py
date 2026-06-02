"""Tests for pathlight_mcp.safety — PRD R12 three-tier element and system-action
risk classification."""

from __future__ import annotations

from typing import ClassVar

import pytest

from pathlight_mcp.models import DesktopAction, ElementStates, NormalizedElement
from pathlight_mcp.safety import (
    CDP_METHOD_ALLOWLIST,
    DESTRUCTIVE_NAME_PATTERNS,
    ROLE_RISK_MAP,
    SENSITIVE_ROLES,
    SYSTEM_ACTION_RISK_MAP,
    EvaluateRateLimiter,
    RiskAssessment,
    RiskLevel,
    SystemAction,
    classify,
    classify_system_action,
    is_cdp_method_allowed,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _element(
    role: str = "text_input",
    name: str | None = None,
    enabled: bool | None = None,
    actions: list[DesktopAction] | None = None,
) -> NormalizedElement:
    """Build a minimal NormalizedElement for classification tests."""
    states = ElementStates()
    if enabled is not None:
        states = ElementStates(enabled=enabled)
    return NormalizedElement(
        ref="e1",
        backend_id="native-1",
        role=role,
        name=name,
        states=states,
        actions=actions or [],
    )


# ---------------------------------------------------------------------------
# TC-4.1: RiskLevel type
# ---------------------------------------------------------------------------


class TestRiskLevelType:
    """RiskLevel is a Literal type with exactly three string values."""

    def test_is_literal(self) -> None:
        import typing

        origin = typing.get_origin(RiskLevel)
        assert origin is typing.Literal

    def test_valid_values(self) -> None:
        import typing

        args = typing.get_args(RiskLevel)
        assert set(args) == {"READ_ONLY", "INTERACTION", "SENSITIVE"}

    def test_accepts_read_only(self) -> None:
        _: RiskLevel = "READ_ONLY"

    def test_accepts_interaction(self) -> None:
        _: RiskLevel = "INTERACTION"

    def test_accepts_sensitive(self) -> None:
        _: RiskLevel = "SENSITIVE"


# ---------------------------------------------------------------------------
# TC-4.2: RiskAssessment dataclass
# ---------------------------------------------------------------------------


class TestRiskAssessmentDataclass:
    """RiskAssessment is a frozen dataclass with four fields."""

    def test_is_frozen(self) -> None:
        ra = RiskAssessment(
            risk_level="READ_ONLY",
            confirmation_required=False,
            reason="test",
            confidence=1.0,
        )
        with pytest.raises(AttributeError):
            ra.risk_level = "SENSITIVE"  # type: ignore[misc]

    def test_has_slots(self) -> None:
        ra = RiskAssessment(
            risk_level="READ_ONLY",
            confirmation_required=False,
            reason="test",
            confidence=1.0,
        )
        assert "__slots__" in type(ra).__dict__

    def test_field_risk_level(self) -> None:
        ra = RiskAssessment(
            risk_level="SENSITIVE",
            confirmation_required=True,
            reason="test",
            confidence=0.9,
        )
        assert ra.risk_level == "SENSITIVE"

    def test_field_confirmation_required(self) -> None:
        ra = RiskAssessment(
            risk_level="SENSITIVE",
            confirmation_required=True,
            reason="test",
            confidence=0.9,
        )
        assert ra.confirmation_required is True

    def test_field_reason(self) -> None:
        ra = RiskAssessment(
            risk_level="READ_ONLY",
            confirmation_required=False,
            reason="Element is disabled",
            confidence=1.0,
        )
        assert ra.reason == "Element is disabled"

    def test_field_confidence(self) -> None:
        ra = RiskAssessment(
            risk_level="INTERACTION",
            confirmation_required=False,
            reason="default",
            confidence=0.8,
        )
        assert ra.confidence == pytest.approx(0.8)

    def test_equality(self) -> None:
        ra1 = RiskAssessment("READ_ONLY", False, "test", 1.0)
        ra2 = RiskAssessment("READ_ONLY", False, "test", 1.0)
        assert ra1 == ra2

    def test_inequality(self) -> None:
        ra1 = RiskAssessment("READ_ONLY", False, "test", 1.0)
        ra2 = RiskAssessment("SENSITIVE", True, "test", 0.9)
        assert ra1 != ra2


# ---------------------------------------------------------------------------
# TC-4.3: classify() signature
# ---------------------------------------------------------------------------


class TestClassifySignature:
    """classify accepts NormalizedElement and DesktopAction, returns RiskAssessment."""

    def test_returns_risk_assessment(self) -> None:
        elem = _element(role="label")
        result = classify(elem, "click")
        assert isinstance(result, RiskAssessment)

    def test_two_arg_signature(self) -> None:
        """classify must be called with (element, action) — not role string."""
        elem = _element(role="text_input")
        result = classify(elem, "type")
        assert isinstance(result, RiskAssessment)


# ---------------------------------------------------------------------------
# TC-4.4: Disabled element handling
# ---------------------------------------------------------------------------


class TestDisabledElement:
    """Disabled elements (enabled=False) always return READ_ONLY."""

    def test_disabled_button_is_read_only(self) -> None:
        elem = _element(role="button", enabled=False)
        result = classify(elem, "click")
        assert result.risk_level == "READ_ONLY"
        assert result.confirmation_required is False

    def test_disabled_delete_button_is_read_only(self) -> None:
        elem = _element(role="delete_button", enabled=False)
        result = classify(elem, "click")
        assert result.risk_level == "READ_ONLY"

    def test_disabled_with_destructive_name_is_read_only(self) -> None:
        elem = _element(role="button", name="Delete All", enabled=False)
        result = classify(elem, "click")
        assert result.risk_level == "READ_ONLY"

    def test_disabled_element_confidence(self) -> None:
        elem = _element(role="button", enabled=False)
        result = classify(elem, "click")
        assert result.confidence == pytest.approx(1.0)

    def test_enabled_none_is_not_disabled(self) -> None:
        """enabled=None (unreported) should NOT trigger the disabled rule."""
        elem = _element(role="delete_button", enabled=None)
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"

    def test_enabled_true_is_not_disabled(self) -> None:
        elem = _element(role="delete_button", enabled=True)
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"


# ---------------------------------------------------------------------------
# TC-4.5: Focus action always returns READ_ONLY
# ---------------------------------------------------------------------------


class TestFocusAction:
    """Focus action on any element returns READ_ONLY regardless of role."""

    @pytest.mark.parametrize(
        "role",
        [
            "button",
            "delete_button",
            "text_input",
            "link",
            "label",
            "menu_item",
        ],
    )
    def test_focus_is_read_only(self, role: str) -> None:
        elem = _element(role=role)
        result = classify(elem, "focus")
        assert result.risk_level == "READ_ONLY"
        assert result.confirmation_required is False

    def test_focus_on_destructive_name(self) -> None:
        elem = _element(role="button", name="Delete Everything")
        result = classify(elem, "focus")
        assert result.risk_level == "READ_ONLY"

    def test_focus_on_enabled_element(self) -> None:
        elem = _element(role="button", enabled=True)
        result = classify(elem, "focus")
        assert result.risk_level == "READ_ONLY"

    def test_non_focus_action_not_read_only_by_default(self) -> None:
        """Click on a button is not READ_ONLY (it's INTERACTION or SENSITIVE)."""
        elem = _element(role="button")
        result = classify(elem, "click")
        assert result.risk_level != "READ_ONLY"


# ---------------------------------------------------------------------------
# TC-4.6: Sensitive roles
# ---------------------------------------------------------------------------


class TestSensitiveRoles:
    """delete_button, remove_button, clear_button are SENSITIVE."""

    @pytest.mark.parametrize("role", list(SENSITIVE_ROLES))
    def test_sensitive_roles_are_sensitive(self, role: str) -> None:
        elem = _element(role=role)
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True

    def test_sensitive_roles_exact_set(self) -> None:
        """SENSITIVE_ROLES contains exactly the five sensitive roles."""
        assert {
            "delete_button",
            "remove_button",
            "clear_button",
            "password_field",
            "credential_field",
        } == SENSITIVE_ROLES

    def test_sensitive_role_confidence(self) -> None:
        elem = _element(role="delete_button")
        result = classify(elem, "click")
        assert result.confidence == pytest.approx(1.0)

    def test_sensitive_role_reason(self) -> None:
        elem = _element(role="remove_button")
        result = classify(elem, "click")
        assert "remove_button" in result.reason


# ---------------------------------------------------------------------------
# TC-4.6b: Password / credential escalation
# ---------------------------------------------------------------------------


class TestPasswordCredentialEscalation:
    """password_field and credential_field roles are SENSITIVE (AC-8)."""

    def test_password_field_is_sensitive(self) -> None:
        elem = _element(role="password_field")
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True

    def test_credential_field_is_sensitive(self) -> None:
        elem = _element(role="credential_field")
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True

    def test_password_field_confidence(self) -> None:
        elem = _element(role="password_field")
        result = classify(elem, "click")
        assert result.confidence == pytest.approx(1.0)

    def test_credential_field_confidence(self) -> None:
        elem = _element(role="credential_field")
        result = classify(elem, "click")
        assert result.confidence == pytest.approx(1.0)

    def test_password_field_reason(self) -> None:
        elem = _element(role="password_field")
        result = classify(elem, "click")
        assert "password_field" in result.reason

    def test_credential_field_reason(self) -> None:
        elem = _element(role="credential_field")
        result = classify(elem, "click")
        assert "credential_field" in result.reason

    def test_disabled_password_field_is_read_only(self) -> None:
        """Disabled password_field is still READ_ONLY (disabled rule wins)."""
        elem = _element(role="password_field", enabled=False)
        result = classify(elem, "click")
        assert result.risk_level == "READ_ONLY"

    def test_focus_on_password_field_is_read_only(self) -> None:
        """Focus on password_field is READ_ONLY (focus rule wins)."""
        elem = _element(role="password_field")
        result = classify(elem, "focus")
        assert result.risk_level == "READ_ONLY"

    def test_password_field_in_sensitive_roles(self) -> None:
        assert "password_field" in SENSITIVE_ROLES

    def test_credential_field_in_sensitive_roles(self) -> None:
        assert "credential_field" in SENSITIVE_ROLES

    def test_password_field_in_role_risk_map(self) -> None:
        assert ROLE_RISK_MAP.get("password_field") == "SENSITIVE"

    def test_credential_field_in_role_risk_map(self) -> None:
        assert ROLE_RISK_MAP.get("credential_field") == "SENSITIVE"


# ---------------------------------------------------------------------------
# TC-4.7: Generic button is NOT sensitive
# ---------------------------------------------------------------------------


class TestGenericButtonNotSensitive:
    """A generic 'button' role is INTERACTION, not SENSITIVE."""

    def test_button_is_interaction(self) -> None:
        elem = _element(role="button")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_button_confirmation_not_required(self) -> None:
        elem = _element(role="button")
        result = classify(elem, "click")
        assert result.confirmation_required is False

    def test_submit_button_is_interaction(self) -> None:
        elem = _element(role="submit_button")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_cancel_button_is_interaction(self) -> None:
        elem = _element(role="cancel_button")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_toggle_button_is_interaction(self) -> None:
        elem = _element(role="toggle_button")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"


# ---------------------------------------------------------------------------
# TC-4.8: Destructive name heuristics
# ---------------------------------------------------------------------------


class TestDestructiveNameHeuristics:
    """Elements whose name contains destructive substrings are SENSITIVE."""

    @pytest.mark.parametrize(
        ("name", "expected_pattern"),
        [
            ("Delete", "delete"),
            ("Remove Item", "remove"),
            ("Clear All", "clear"),
            ("Destroy Data", "destroy"),
            ("Erase History", "erase"),
            ("Purge Cache", "purge"),
            ("Drop Table", "drop"),
            ("Discard Changes", "discard"),
            ("Nuke Settings", "nuke"),
            ("Obliterate File", "obliterate"),
            ("Wipe Disk", "wipe"),
            ("Format Drive", "format"),
            ("Reset Settings", "reset"),
            ("Uninstall App", "uninstall"),
        ],
    )
    def test_destructive_names_are_sensitive(self, name: str, expected_pattern: str) -> None:
        elem = _element(role="button", name=name)
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True
        assert expected_pattern in result.reason

    def test_destructive_name_confidence(self) -> None:
        elem = _element(role="button", name="Delete")
        result = classify(elem, "click")
        assert result.confidence == pytest.approx(0.9)

    def test_case_insensitive_matching(self) -> None:
        elem = _element(role="button", name="DELETE ALL")
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"

    def test_mixed_case_matching(self) -> None:
        elem = _element(role="button", name="DeLeTe")
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"

    def test_substring_matching(self) -> None:
        """Pattern 'delete' matches 'delete_user_account'."""
        elem = _element(role="button", name="delete_user_account")
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"

    def test_no_false_positive_safe_name(self) -> None:
        """A name without destructive patterns is not SENSITIVE by name."""
        elem = _element(role="button", name="Save Changes")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_null_name_no_match(self) -> None:
        """None name should not trigger destructive name heuristic."""
        elem = _element(role="button", name=None)
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_empty_name_no_match(self) -> None:
        """Empty string name should not trigger destructive name heuristic."""
        elem = _element(role="button", name="")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"


# ---------------------------------------------------------------------------
# TC-4.9: DESTRUCTIVE_NAME_PATTERNS public constant
# ---------------------------------------------------------------------------


class TestDestructiveNamePatternsConstant:
    """DESTRUCTIVE_NAME_PATTERNS is a public tuple of 14 patterns."""

    def test_is_tuple(self) -> None:
        assert isinstance(DESTRUCTIVE_NAME_PATTERNS, tuple)

    def test_pattern_count(self) -> None:
        assert len(DESTRUCTIVE_NAME_PATTERNS) == 14

    def test_contains_core_patterns(self) -> None:
        patterns = set(DESTRUCTIVE_NAME_PATTERNS)
        for expected in ("delete", "remove", "clear", "destroy", "erase"):
            assert expected in patterns

    def test_contains_all_patterns(self) -> None:
        expected = {
            "delete",
            "remove",
            "clear",
            "destroy",
            "erase",
            "purge",
            "drop",
            "discard",
            "nuke",
            "obliterate",
            "wipe",
            "format",
            "reset",
            "uninstall",
        }
        assert set(DESTRUCTIVE_NAME_PATTERNS) == expected


# ---------------------------------------------------------------------------
# TC-4.10: READ_ONLY roles
# ---------------------------------------------------------------------------


class TestReadOnlyClassification:
    """Informational / container roles are READ_ONLY."""

    READ_ONLY_ROLES: ClassVar[list[str]] = [
        "label",
        "text",
        "heading",
        "link",
        "image",
        "icon",
        "list",
        "list_item",
        "table",
        "table_row",
        "table_column_header",
        "table_header",
        "progress_bar",
        "separator",
        "group",
        "tab_bar",
        "tooltip",
        "status_bar",
        "title_bar",
        "chart",
        "dialog",
        "window",
        "pane",
        "document",
        "page_tab_list",
    ]

    @pytest.mark.parametrize("role", READ_ONLY_ROLES)
    def test_read_only_roles(self, role: str) -> None:
        elem = _element(role=role)
        result = classify(elem, "click")
        assert result.risk_level == "READ_ONLY"
        assert result.confirmation_required is False

    @pytest.mark.parametrize("role", READ_ONLY_ROLES)
    def test_read_only_roles_confidence(self, role: str) -> None:
        elem = _element(role=role)
        result = classify(elem, "click")
        assert result.confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TC-4.11: INTERACTION roles (default)
# ---------------------------------------------------------------------------


class TestInteractionClassification:
    """Input elements with limited blast radius are INTERACTION (default)."""

    INTERACTION_ROLES: ClassVar[list[str]] = [
        "text_input",
        "combo_box",
        "slider",
        "check_box",
        "radio_button",
        "spin_button",
        "search_input",
        "list_box",
        "drop_down",
        "switch",
        "date_picker",
        "scroll_bar",
        "color_picker",
        "text_area",
        "button",
        "submit_button",
        "cancel_button",
        "toggle_button",
        "menu_item",
        "tree_item",
        "tab",
        "password_input",
        "close_button",
        "minimize_button",
        "maximize_button",
    ]

    @pytest.mark.parametrize("role", INTERACTION_ROLES)
    def test_interaction_roles(self, role: str) -> None:
        elem = _element(role=role)
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"
        assert result.confirmation_required is False

    def test_unknown_role_defaults_to_interaction(self) -> None:
        elem = _element(role="completely_unknown_role")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_interaction_confidence(self) -> None:
        elem = _element(role="text_input")
        result = classify(elem, "type")
        assert result.confidence == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# TC-4.12: Priority rules — disabled before sensitive role
# ---------------------------------------------------------------------------


class TestPriorityRules:
    """Classification rules apply in the correct priority order."""

    def test_disabled_before_sensitive_role(self) -> None:
        """Disabled delete_button is READ_ONLY, not SENSITIVE."""
        elem = _element(role="delete_button", enabled=False)
        result = classify(elem, "click")
        assert result.risk_level == "READ_ONLY"

    def test_focus_before_sensitive_role(self) -> None:
        """Focus on delete_button is READ_ONLY, not SENSITIVE."""
        elem = _element(role="delete_button")
        result = classify(elem, "focus")
        assert result.risk_level == "READ_ONLY"

    def test_focus_before_disabled(self) -> None:
        """Focus on disabled element is still READ_ONLY (both rules agree)."""
        elem = _element(role="button", enabled=False)
        result = classify(elem, "focus")
        assert result.risk_level == "READ_ONLY"

    def test_sensitive_role_before_name_heuristic(self) -> None:
        """Sensitive role triggers before name heuristic is checked."""
        elem = _element(role="delete_button", name="Save")
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"
        assert "Sensitive role" in result.reason

    def test_name_heuristic_before_read_only_role(self) -> None:
        """A read-only role with destructive name is still SENSITIVE."""
        elem = _element(role="button", name="Delete Everything")
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"

    def test_read_only_role_before_interaction_default(self) -> None:
        """Known read-only role is READ_ONLY, not INTERACTION."""
        elem = _element(role="label")
        result = classify(elem, "click")
        assert result.risk_level == "READ_ONLY"


# ---------------------------------------------------------------------------
# TC-4.13: NormalizedElement integration
# ---------------------------------------------------------------------------


class TestNormalizedElementIntegration:
    """classify works with real NormalizedElement instances."""

    def test_full_element(self) -> None:
        elem = NormalizedElement(
            ref="e42",
            backend_id="win-42",
            role="text_input",
            name="Username",
            states=ElementStates(enabled=True, focused=True),
            actions=["click", "focus", "type"],
        )
        result = classify(elem, "type")
        assert result.risk_level == "INTERACTION"

    def test_element_with_children(self) -> None:
        parent = NormalizedElement(
            ref="e1",
            backend_id="win-1",
            role="dialog",
            name="Confirm Action",
            children=[
                NormalizedElement(
                    ref="e2",
                    backend_id="win-2",
                    role="delete_button",
                    name="Delete",
                ),
            ],
        )
        result = classify(parent, "click")
        # dialog is a READ_ONLY role
        assert result.risk_level == "READ_ONLY"

    def test_element_with_bounds(self) -> None:
        from pathlight_mcp.models import Bounds

        elem = NormalizedElement(
            ref="e10",
            backend_id="win-10",
            role="button",
            name="Submit",
            bounds=Bounds(x=100.0, y=200.0, width=80.0, height=30.0),
        )
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_element_with_all_states_none(self) -> None:
        """Default ElementStates (all None) should not trigger disabled rule."""
        elem = NormalizedElement(
            ref="e5",
            backend_id="win-5",
            role="delete_button",
        )
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"


# ---------------------------------------------------------------------------
# TC-4.14: Multiple actions on same element
# ---------------------------------------------------------------------------


class TestMultipleActions:
    """Different actions on the same element can yield different risk levels."""

    def test_focus_vs_click_on_button(self) -> None:
        elem = _element(role="button")
        focus_result = classify(elem, "focus")
        click_result = classify(elem, "click")
        assert focus_result.risk_level == "READ_ONLY"
        assert click_result.risk_level == "INTERACTION"

    def test_focus_vs_click_on_delete_button(self) -> None:
        elem = _element(role="delete_button")
        focus_result = classify(elem, "focus")
        click_result = classify(elem, "click")
        assert focus_result.risk_level == "READ_ONLY"
        assert click_result.risk_level == "SENSITIVE"

    @pytest.mark.parametrize(
        "action",
        [
            "click",
            "type",
            "set_value",
            "select",
            "toggle",
            "expand",
            "collapse",
            "scroll",
            "increment",
            "decrement",
            "open_menu",
            "invoke",
        ],
    )
    def test_non_focus_actions_not_auto_read_only(self, action: str) -> None:
        """Only 'focus' is auto-READ_ONLY; all other actions follow normal rules."""
        elem = _element(role="label")
        result = classify(elem, action)
        # label is a READ_ONLY role so it's still READ_ONLY, but via Rule 5
        # not Rule 2 — the point is it's not short-circuited by the focus rule
        assert result.risk_level == "READ_ONLY"


# ---------------------------------------------------------------------------
# TC-4.15: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_role_defaults_to_interaction(self) -> None:
        elem = _element(role="")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_role_with_spaces(self) -> None:
        elem = _element(role="  ")
        result = classify(elem, "click")
        assert result.risk_level == "INTERACTION"

    def test_name_with_special_characters(self) -> None:
        """Destructive pattern in a name with special chars still matches."""
        elem = _element(role="button", name="Delete?!@#$%")
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"

    def test_name_unicode(self) -> None:
        """Unicode in name should still allow pattern matching."""
        elem = _element(role="button", name="Delete\u200bAll")  # zero-width space
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"

    def test_very_long_name(self) -> None:
        elem = _element(role="button", name="a" * 10000 + "delete" + "b" * 10000)
        result = classify(elem, "click")
        assert result.risk_level == "SENSITIVE"

    def test_confidence_range(self) -> None:
        """All confidence values should be in [0.0, 1.0]."""
        scenarios = [
            _element(role="label"),
            _element(role="text_input"),
            _element(role="delete_button"),
            _element(role="button", name="Delete"),
            _element(role="button", enabled=False),
        ]
        actions = ["click", "focus", "type"]
        for elem in scenarios:
            for action in actions:
                result = classify(elem, action)
                assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# TC-4.16: Module exports (__all__)
# ---------------------------------------------------------------------------


class TestExports:
    """Public API is correctly exported."""

    def test_all_entries(self) -> None:
        from pathlight_mcp import safety

        assert set(safety.__all__) == {
            "RiskAssessment",
            "RiskLevel",
            "SENSITIVE_ROLES",
            "DESTRUCTIVE_NAME_PATTERNS",
            "ROLE_RISK_MAP",
            "SYSTEM_ACTION_RISK_MAP",
            "SystemAction",
            "classify",
            "classify_system_action",
            "CDP_METHOD_ALLOWLIST",
            "EvaluateRateLimiter",
            "is_cdp_method_allowed",
        }

    def test_all_count(self) -> None:
        from pathlight_mcp import safety

        assert len(safety.__all__) == 12

    def test_import_risk_assessment(self) -> None:
        from pathlight_mcp.safety import RiskAssessment  # noqa: F401

    def test_import_risk_level(self) -> None:
        from pathlight_mcp.safety import RiskLevel  # noqa: F401

    def test_import_sensitive_roles(self) -> None:
        from pathlight_mcp.safety import SENSITIVE_ROLES  # noqa: F401

    def test_import_destructive_name_patterns(self) -> None:
        from pathlight_mcp.safety import DESTRUCTIVE_NAME_PATTERNS  # noqa: F401

    def test_import_classify(self) -> None:
        from pathlight_mcp.safety import classify  # noqa: F401

    def test_import_role_risk_map(self) -> None:
        from pathlight_mcp.safety import ROLE_RISK_MAP  # noqa: F401

    def test_import_system_action_risk_map(self) -> None:
        from pathlight_mcp.safety import SYSTEM_ACTION_RISK_MAP  # noqa: F401

    def test_import_system_action(self) -> None:
        from pathlight_mcp.safety import SystemAction  # noqa: F401

    def test_import_classify_system_action(self) -> None:
        from pathlight_mcp.safety import classify_system_action  # noqa: F401


# ---------------------------------------------------------------------------
# TC-4.17: ROLE_RISK_MAP public constant (AC-4)
# ---------------------------------------------------------------------------


class TestRoleRiskMap:
    """ROLE_RISK_MAP is a public dict mapping roles to RiskLevel values."""

    def test_is_dict(self) -> None:
        assert isinstance(ROLE_RISK_MAP, dict)

    def test_maps_read_only_roles(self) -> None:
        for role in ("label", "text", "heading", "image", "dialog", "window"):
            assert ROLE_RISK_MAP.get(role) == "READ_ONLY"

    def test_maps_sensitive_roles(self) -> None:
        for role in (
            "delete_button",
            "remove_button",
            "clear_button",
            "password_field",
            "credential_field",
        ):
            assert ROLE_RISK_MAP.get(role) == "SENSITIVE"

    def test_unknown_role_not_in_map(self) -> None:
        assert "button" not in ROLE_RISK_MAP
        assert "text_input" not in ROLE_RISK_MAP

    def test_values_are_valid_risk_levels(self) -> None:
        for value in ROLE_RISK_MAP.values():
            assert value in ("READ_ONLY", "INTERACTION", "SENSITIVE")


# ---------------------------------------------------------------------------
# TC-030: ROLE_RISK_MAP entry count and completeness
# ---------------------------------------------------------------------------


class TestRoleRiskMapCompleteness:
    """TC-030: ROLE_RISK_MAP has expected entry count and covers all roles."""

    def test_entry_count(self) -> None:
        """ROLE_RISK_MAP should have 34 entries (29 READ_ONLY + 5 SENSITIVE)."""
        assert len(ROLE_RISK_MAP) == 34

    def test_read_only_role_count(self) -> None:
        ro = [r for r, v in ROLE_RISK_MAP.items() if v == "READ_ONLY"]
        assert len(ro) == 29

    def test_sensitive_role_count(self) -> None:
        se = [r for r, v in ROLE_RISK_MAP.items() if v == "SENSITIVE"]
        assert len(se) == 5

    def test_no_interaction_in_map(self) -> None:
        """INTERACTION roles are not in ROLE_RISK_MAP (they are the default)."""
        assert "INTERACTION" not in ROLE_RISK_MAP.values()


# ---------------------------------------------------------------------------
# TC-031: SENSITIVE_ROLES and ROLE_RISK_MAP consistency
# ---------------------------------------------------------------------------


class TestSensitiveRolesConsistency:
    """TC-031: SENSITIVE_ROLES set matches SENSITIVE entries in ROLE_RISK_MAP."""

    def test_sensitive_roles_match_map(self) -> None:
        map_sensitive = {r for r, v in ROLE_RISK_MAP.items() if v == "SENSITIVE"}
        assert map_sensitive == SENSITIVE_ROLES

    def test_sensitive_roles_is_frozenset(self) -> None:
        assert isinstance(SENSITIVE_ROLES, frozenset)

    def test_all_sensitive_roles_in_map(self) -> None:
        for role in SENSITIVE_ROLES:
            assert role in ROLE_RISK_MAP


# ---------------------------------------------------------------------------
# TC-032: DESTRUCTIVE_NAME_PATTERNS type and immutability
# ---------------------------------------------------------------------------


class TestDestructivePatternsImmutability:
    """TC-032: DESTRUCTIVE_NAME_PATTERNS is a tuple (immutable)."""

    def test_is_tuple(self) -> None:
        assert isinstance(DESTRUCTIVE_NAME_PATTERNS, tuple)

    def test_not_list(self) -> None:
        assert not isinstance(DESTRUCTIVE_NAME_PATTERNS, list)

    def test_cannot_mutate(self) -> None:
        """Tuple does not support item assignment."""
        with pytest.raises(TypeError):
            DESTRUCTIVE_NAME_PATTERNS[0] = "harmless"  # type: ignore[index]


# ---------------------------------------------------------------------------
# TC-033: classify() reason string format
# ---------------------------------------------------------------------------


class TestClassifyReasonFormat:
    """TC-033: classify() reason strings follow expected format."""

    def test_disabled_reason(self) -> None:
        elem = _element(role="button", enabled=False)
        result = classify(elem, "click")
        assert "disabled" in result.reason.lower()

    def test_focus_reason(self) -> None:
        elem = _element(role="button")
        result = classify(elem, "focus")
        assert "focus" in result.reason.lower()

    def test_sensitive_role_reason(self) -> None:
        elem = _element(role="delete_button")
        result = classify(elem, "click")
        assert "sensitive" in result.reason.lower()
        assert "delete_button" in result.reason

    def test_destructive_name_reason(self) -> None:
        elem = _element(role="button", name="Delete All")
        result = classify(elem, "click")
        assert "destructive" in result.reason.lower()

    def test_read_only_role_reason(self) -> None:
        elem = _element(role="label")
        result = classify(elem, "click")
        assert "read-only" in result.reason.lower()

    def test_default_reason(self) -> None:
        elem = _element(role="button")
        result = classify(elem, "click")
        assert "default" in result.reason.lower()


# ---------------------------------------------------------------------------
# TC-034: classify() with all DesktopAction values
# ---------------------------------------------------------------------------


class TestClassifyAllActions:
    """TC-034: classify() handles all 13 DesktopAction values."""

    @pytest.mark.parametrize(
        "action",
        [
            "click",
            "focus",
            "type",
            "set_value",
            "select",
            "toggle",
            "expand",
            "collapse",
            "scroll",
            "increment",
            "decrement",
            "open_menu",
            "invoke",
        ],
    )
    def test_all_actions_return_risk_assessment(self, action: str) -> None:
        """Every valid DesktopAction returns a RiskAssessment."""
        elem = _element(role="button")
        result = classify(elem, action)
        assert isinstance(result, RiskAssessment)
        assert result.risk_level in ("READ_ONLY", "INTERACTION", "SENSITIVE")
        assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# TC-035: RiskAssessment repr and string representation
# ---------------------------------------------------------------------------


class TestRiskAssessmentRepr:
    """TC-035: RiskAssessment has useful repr/str."""

    def test_repr_contains_risk_level(self) -> None:
        ra = RiskAssessment(
            risk_level="SENSITIVE",
            confirmation_required=True,
            reason="test",
            confidence=1.0,
        )
        r = repr(ra)
        assert "SENSITIVE" in r

    def test_repr_contains_confirmation(self) -> None:
        ra = RiskAssessment(
            risk_level="READ_ONLY",
            confirmation_required=False,
            reason="test",
            confidence=1.0,
        )
        r = repr(ra)
        assert "READ_ONLY" in r

    def test_frozen_prevents_mutation(self) -> None:
        ra = RiskAssessment("INTERACTION", False, "test", 0.8)
        with pytest.raises(AttributeError):
            ra.risk_level = "SENSITIVE"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            ra.confidence = 0.0  # type: ignore[misc]


# ===========================================================================
# classify_system_action tests
# ===========================================================================


# ---------------------------------------------------------------------------
# TC-SA.1: SystemAction type
# ---------------------------------------------------------------------------


class TestSystemActionType:
    """SystemAction is a Literal type with the expected action values."""

    def test_is_literal(self) -> None:
        import typing

        origin = typing.get_origin(SystemAction)
        assert origin is typing.Literal

    def test_valid_values(self) -> None:
        import typing

        args = set(typing.get_args(SystemAction))
        expected = {
            "app_launch",
            "app_close",
            "clipboard_read",
            "clipboard_write",
            "screenshot",
            "window_list",
            "window_focus",
            "window_close",
            "window_manage",
            "system_info",
            "desktop_click_xy",
            "desktop_mouse_move",
            "web_connect",
            "web_navigate",
            "web_evaluate",
            "web_inspect",
            "web_click",
            "web_type",
            "web_hover",
            "web_select_option",
            "web_upload_files",
            "web_list_tabs",
            "web_tab_action",
            "web_frame_tree",
            "web_wait_for",
            "web_screenshot",
        }
        assert args == expected

    def test_action_count(self) -> None:
        import typing

        args = typing.get_args(SystemAction)
        assert len(args) == 26


# ---------------------------------------------------------------------------
# TC-SA.2: classify_system_action signature
# ---------------------------------------------------------------------------


class TestClassifySystemActionSignature:
    """classify_system_action returns RiskAssessment for known actions."""

    def test_returns_risk_assessment(self) -> None:
        result = classify_system_action("app_launch")
        assert isinstance(result, RiskAssessment)

    @pytest.mark.parametrize(
        "action",
        [
            "app_launch",
            "app_close",
            "clipboard_read",
            "clipboard_write",
            "screenshot",
            "window_list",
            "window_focus",
            "window_close",
            "system_info",
            "web_connect",
            "web_navigate",
            "web_evaluate",
            "web_inspect",
        ],
    )
    def test_all_actions_return_risk_assessment(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert isinstance(result, RiskAssessment)


# ---------------------------------------------------------------------------
# TC-SA.3: SENSITIVE system actions
# ---------------------------------------------------------------------------


class TestSensitiveSystemActions:
    """System actions that require confirmation (SENSITIVE)."""

    SENSITIVE_ACTIONS: ClassVar[list[SystemAction]] = [
        "app_launch",
        "app_close",
        "clipboard_write",
        "window_close",
        "web_connect",
        "web_navigate",
        "web_evaluate",
        "web_inspect",
    ]

    @pytest.mark.parametrize("action", SENSITIVE_ACTIONS)
    def test_sensitive_risk_level(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.risk_level == "SENSITIVE"

    @pytest.mark.parametrize("action", SENSITIVE_ACTIONS)
    def test_confirmation_required(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.confirmation_required is True

    @pytest.mark.parametrize("action", SENSITIVE_ACTIONS)
    def test_sensitive_confidence(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TC-SA.4: INTERACTION system actions
# ---------------------------------------------------------------------------


class TestInteractionSystemActions:
    """System actions that are interactions but not destructive."""

    INTERACTION_ACTIONS: ClassVar[list[SystemAction]] = [
        "clipboard_read",
        "screenshot",
        "window_focus",
    ]

    @pytest.mark.parametrize("action", INTERACTION_ACTIONS)
    def test_interaction_risk_level(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.risk_level == "INTERACTION"

    @pytest.mark.parametrize("action", INTERACTION_ACTIONS)
    def test_no_confirmation_required(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.confirmation_required is False


# ---------------------------------------------------------------------------
# TC-SA.5: READ_ONLY system actions
# ---------------------------------------------------------------------------


class TestReadOnlySystemActions:
    """System actions that are read-only."""

    READ_ONLY_ACTIONS: ClassVar[list[SystemAction]] = [
        "window_list",
        "system_info",
    ]

    @pytest.mark.parametrize("action", READ_ONLY_ACTIONS)
    def test_read_only_risk_level(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.risk_level == "READ_ONLY"

    @pytest.mark.parametrize("action", READ_ONLY_ACTIONS)
    def test_no_confirmation_required(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.confirmation_required is False


# ---------------------------------------------------------------------------
# TC-SA.6: Unknown system action defaults to SENSITIVE
# ---------------------------------------------------------------------------


class TestUnknownSystemAction:
    """Unknown/unrecognised system actions default to SENSITIVE (safe fallback)."""

    def test_unknown_action_defaults_to_sensitive(self) -> None:
        result = classify_system_action("unknown_action")  # type: ignore[arg-type]
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True
        assert "defaults to SENSITIVE" in result.reason
        assert result.confidence == 0.8

    def test_empty_action_defaults_to_sensitive(self) -> None:
        result = classify_system_action("")  # type: ignore[arg-type]
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True
        assert "defaults to SENSITIVE" in result.reason

    def test_typo_action_defaults_to_sensitive(self) -> None:
        result = classify_system_action("app_launc")  # type: ignore[arg-type]
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True
        assert "defaults to SENSITIVE" in result.reason


# ---------------------------------------------------------------------------
# TC-SA.7: Target parameter enriches reason
# ---------------------------------------------------------------------------


class TestSystemActionTarget:
    """The optional target parameter enriches reason strings."""

    def test_target_in_reason_app_launch(self) -> None:
        result = classify_system_action("app_launch", target="notepad.exe")
        assert "notepad.exe" in result.reason

    def test_target_in_reason_clipboard_write(self) -> None:
        result = classify_system_action("clipboard_write", target="sensitive data")
        assert "sensitive data" in result.reason

    def test_no_target_no_clause(self) -> None:
        result = classify_system_action("app_launch")
        assert " on '" not in result.reason

    def test_target_does_not_change_risk_level(self) -> None:
        result_with = classify_system_action("app_launch", target="test")
        result_without = classify_system_action("app_launch")
        assert result_with.risk_level == result_without.risk_level

    def test_target_does_not_change_confirmation(self) -> None:
        result_with = classify_system_action("clipboard_read", target="text")
        result_without = classify_system_action("clipboard_read")
        assert result_with.confirmation_required == result_without.confirmation_required


# ---------------------------------------------------------------------------
# TC-SA.8: Reason string format
# ---------------------------------------------------------------------------


class TestSystemActionReasonFormat:
    """Reason strings follow expected patterns for each risk level."""

    def test_sensitive_reason_mentions_confirmation(self) -> None:
        result = classify_system_action("app_launch")
        assert "requires confirmation" in result.reason

    def test_read_only_reason_mentions_read_only(self) -> None:
        result = classify_system_action("system_info")
        assert "read-only" in result.reason.lower()

    def test_interaction_reason_mentions_interaction(self) -> None:
        result = classify_system_action("clipboard_read")
        assert "interaction" in result.reason.lower()

    def test_reason_contains_action_name(self) -> None:
        result = classify_system_action("app_launch")
        assert "app_launch" in result.reason


# ---------------------------------------------------------------------------
# TC-SA.9: SYSTEM_ACTION_RISK_MAP completeness
# ---------------------------------------------------------------------------


class TestSystemActionRiskMapCompleteness:
    """SYSTEM_ACTION_RISK_MAP covers all SystemAction values."""

    def test_map_is_dict(self) -> None:
        assert isinstance(SYSTEM_ACTION_RISK_MAP, dict)

    def test_covers_all_actions(self) -> None:
        import typing

        for action in typing.get_args(SystemAction):
            assert action in SYSTEM_ACTION_RISK_MAP, f"{action} missing from SYSTEM_ACTION_RISK_MAP"

    def test_entry_count(self) -> None:
        assert len(SYSTEM_ACTION_RISK_MAP) == 26

    def test_values_are_valid_risk_levels(self) -> None:
        for value in SYSTEM_ACTION_RISK_MAP.values():
            assert value in ("READ_ONLY", "INTERACTION", "SENSITIVE")

    def test_sensitive_count(self) -> None:
        sensitive = [a for a, v in SYSTEM_ACTION_RISK_MAP.items() if v == "SENSITIVE"]
        assert len(sensitive) == 11

    def test_interaction_count(self) -> None:
        interaction = [a for a, v in SYSTEM_ACTION_RISK_MAP.items() if v == "INTERACTION"]
        assert len(interaction) == 9

    def test_read_only_count(self) -> None:
        read_only = [a for a, v in SYSTEM_ACTION_RISK_MAP.items() if v == "READ_ONLY"]
        assert len(read_only) == 6


# ---------------------------------------------------------------------------
# TC-SA.10: Confidence values
# ---------------------------------------------------------------------------


class TestSystemActionConfidence:
    """All classify_system_action results have confidence 1.0."""

    @pytest.mark.parametrize(
        "action",
        [
            "app_launch",
            "app_close",
            "clipboard_read",
            "clipboard_write",
            "screenshot",
            "window_list",
            "window_focus",
            "window_close",
            "system_info",
            "web_connect",
            "web_navigate",
            "web_evaluate",
            "web_inspect",
        ],
    )
    def test_confidence_is_1(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TC-SA.11: Returned RiskAssessment is frozen
# ---------------------------------------------------------------------------


class TestSystemActionRiskAssessmentFrozen:
    """classify_system_action returns the same frozen RiskAssessment dataclass."""

    def test_result_is_frozen(self) -> None:
        result = classify_system_action("app_launch")
        with pytest.raises(AttributeError):
            result.risk_level = "READ_ONLY"  # type: ignore[misc]

    def test_result_has_slots(self) -> None:
        result = classify_system_action("app_launch")
        assert "__slots__" in type(result).__dict__


# ---------------------------------------------------------------------------
# TC-SA.12: Specific action risk levels
# ---------------------------------------------------------------------------


class TestSpecificSystemActionRiskLevels:
    """Each system action maps to the correct risk level."""

    @pytest.mark.parametrize(
        ("action", "expected_level"),
        [
            ("app_launch", "SENSITIVE"),
            ("app_close", "SENSITIVE"),
            ("clipboard_read", "INTERACTION"),
            ("clipboard_write", "SENSITIVE"),
            ("screenshot", "INTERACTION"),
            ("window_list", "READ_ONLY"),
            ("window_focus", "INTERACTION"),
            ("window_close", "SENSITIVE"),
            ("system_info", "READ_ONLY"),
            ("web_connect", "SENSITIVE"),
            ("web_navigate", "SENSITIVE"),
            ("web_evaluate", "SENSITIVE"),
            ("web_inspect", "SENSITIVE"),
        ],
    )
    def test_risk_level(self, action: SystemAction, expected_level: RiskLevel) -> None:
        result = classify_system_action(action)
        assert result.risk_level == expected_level


# ===========================================================================
# GW-100: CDP Method Allowlist tests
# ===========================================================================


class TestCDPMethodAllowlist:
    """CDP_METHOD_ALLOWLIST restricts allowed CDP methods."""

    def test_allowlist_is_frozenset(self) -> None:
        assert isinstance(CDP_METHOD_ALLOWLIST, frozenset)

    def test_allowed_accessibility_methods(self) -> None:
        assert "Accessibility.getFullAXTree" in CDP_METHOD_ALLOWLIST
        assert "Accessibility.queryAXTree" in CDP_METHOD_ALLOWLIST

    def test_allowed_dom_methods(self) -> None:
        assert "DOM.getDocument" in CDP_METHOD_ALLOWLIST
        assert "DOM.getBoxModel" in CDP_METHOD_ALLOWLIST
        assert "DOM.querySelector" in CDP_METHOD_ALLOWLIST

    def test_allowed_input_methods(self) -> None:
        assert "Input.dispatchMouseEvent" in CDP_METHOD_ALLOWLIST
        assert "Input.dispatchKeyEvent" in CDP_METHOD_ALLOWLIST

    def test_allowed_page_methods(self) -> None:
        assert "Page.getFrameTree" in CDP_METHOD_ALLOWLIST
        assert "Page.captureScreenshot" in CDP_METHOD_ALLOWLIST

    def test_allowed_runtime_methods(self) -> None:
        assert "Runtime.evaluate" in CDP_METHOD_ALLOWLIST
        assert "Runtime.getProperties" in CDP_METHOD_ALLOWLIST

    def test_allowed_target_methods(self) -> None:
        assert "Target.getTargets" in CDP_METHOD_ALLOWLIST
        assert "Target.attachToTarget" in CDP_METHOD_ALLOWLIST

    def test_dangerous_methods_blocked(self) -> None:
        """Methods not in allowlist are blocked."""
        assert "Network.enable" not in CDP_METHOD_ALLOWLIST
        assert "Browser.close" not in CDP_METHOD_ALLOWLIST
        assert "Fetch.enable" not in CDP_METHOD_ALLOWLIST
        assert "Storage.clearDataForOrigin" not in CDP_METHOD_ALLOWLIST
        assert "Emulation.setDeviceMetricsOverride" not in CDP_METHOD_ALLOWLIST

    def test_minimum_entry_count(self) -> None:
        """Allowlist has at least 20 entries covering core domains."""
        assert len(CDP_METHOD_ALLOWLIST) >= 20


class TestIsCDPMethodAllowed:
    """is_cdp_method_allowed checks membership."""

    def test_allowed_method(self) -> None:
        assert is_cdp_method_allowed("Runtime.evaluate") is True

    def test_blocked_method(self) -> None:
        assert is_cdp_method_allowed("Network.enable") is False

    def test_empty_string(self) -> None:
        assert is_cdp_method_allowed("") is False

    def test_case_sensitive(self) -> None:
        """CDP method names are case-sensitive."""
        assert is_cdp_method_allowed("runtime.evaluate") is False
        assert is_cdp_method_allowed("RUNTIME.EVALUATE") is False

    @pytest.mark.parametrize("method", list(CDP_METHOD_ALLOWLIST))
    def test_all_allowed_methods_pass(self, method: str) -> None:
        assert is_cdp_method_allowed(method) is True


# ===========================================================================
# GW-100: EvaluateRateLimiter tests
# ===========================================================================


class TestEvaluateRateLimiter:
    """EvaluateRateLimiter enforces sliding-window rate limiting."""

    def test_default_max_calls(self) -> None:
        limiter = EvaluateRateLimiter()
        assert limiter.max_calls == 10

    def test_default_window_seconds(self) -> None:
        limiter = EvaluateRateLimiter()
        assert limiter.window_seconds == 60.0

    def test_allows_up_to_max_calls(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=3, window_seconds=60.0)
        assert limiter.is_allowed() is True

    def test_blocks_after_max_calls(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=2, window_seconds=60.0)
        limiter.is_allowed()
        limiter.is_allowed()
        assert limiter.is_allowed() is False

    def test_remaining_decrements(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=5, window_seconds=60.0)
        assert limiter.remaining == 5
        limiter.is_allowed()
        assert limiter.remaining == 4
        limiter.is_allowed()
        assert limiter.remaining == 3

    def test_remaining_cannot_go_negative(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=1, window_seconds=60.0)
        limiter.is_allowed()
        assert limiter.is_allowed() is False
        assert limiter.remaining == 0

    def test_reset_clears_state(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=1, window_seconds=60.0)
        limiter.is_allowed()
        assert limiter.is_allowed() is False
        limiter.reset()
        assert limiter.is_allowed() is True

    def test_remaining_after_reset(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=5, window_seconds=60.0)
        limiter.is_allowed()
        limiter.reset()
        assert limiter.remaining == 5

    def test_custom_max_calls(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=100, window_seconds=60.0)
        assert limiter.max_calls == 100

    def test_custom_window_seconds(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=10, window_seconds=30.0)
        assert limiter.window_seconds == 30.0

    def test_sliding_window_expiry(self) -> None:
        """Timestamps outside the window are pruned, allowing new calls."""
        limiter = EvaluateRateLimiter(max_calls=1, window_seconds=0.01)
        assert limiter.is_allowed() is True
        assert limiter.is_allowed() is False
        # Wait for the window to expire
        import time

        time.sleep(0.02)
        assert limiter.is_allowed() is True

    def test_zero_max_calls_blocks_everything(self) -> None:
        limiter = EvaluateRateLimiter(max_calls=0, window_seconds=60.0)
        assert limiter.is_allowed() is False
        assert limiter.remaining == 0


# ===========================================================================
# GW-100: Web SystemAction classification tests
# ===========================================================================


class TestWebSystemActions:
    """Web system actions (web_connect, web_navigate, web_evaluate, web_inspect)
    are all SENSITIVE."""

    @pytest.mark.parametrize(
        "action",
        ["web_connect", "web_navigate", "web_evaluate", "web_inspect"],
    )
    def test_web_action_is_sensitive(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.risk_level == "SENSITIVE"

    @pytest.mark.parametrize(
        "action",
        ["web_connect", "web_navigate", "web_evaluate", "web_inspect"],
    )
    def test_web_action_requires_confirmation(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.confirmation_required is True

    @pytest.mark.parametrize(
        "action",
        ["web_connect", "web_navigate", "web_evaluate", "web_inspect"],
    )
    def test_web_action_confidence(self, action: SystemAction) -> None:
        result = classify_system_action(action)
        assert result.confidence == pytest.approx(1.0)

    def test_web_connect_in_risk_map(self) -> None:
        assert SYSTEM_ACTION_RISK_MAP["web_connect"] == "SENSITIVE"

    def test_web_navigate_in_risk_map(self) -> None:
        assert SYSTEM_ACTION_RISK_MAP["web_navigate"] == "SENSITIVE"

    def test_web_evaluate_in_risk_map(self) -> None:
        assert SYSTEM_ACTION_RISK_MAP["web_evaluate"] == "SENSITIVE"

    def test_web_inspect_in_risk_map(self) -> None:
        assert SYSTEM_ACTION_RISK_MAP["web_inspect"] == "SENSITIVE"

    def test_web_connect_target_in_reason(self) -> None:
        result = classify_system_action("web_connect", target="https://example.com")
        assert "example.com" in result.reason

    def test_web_navigate_target_in_reason(self) -> None:
        result = classify_system_action("web_navigate", target="https://bank.com")
        assert "bank.com" in result.reason

    def test_web_evaluate_target_in_reason(self) -> None:
        result = classify_system_action("web_evaluate", target="document.cookie")
        assert "document.cookie" in result.reason

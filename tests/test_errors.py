"""Tests for guidewire.errors — PRD R14 structured error codes + hint infrastructure."""

import pytest

from guidewire.errors import (
    ActionNotSupportedError,
    AmbiguousSelectorError,
    BackendUnavailableError,
    ElementNotFoundError,
    GuidewireError,
    PermissionRequiredError,
    StaleElementReferenceError,
    WindowNotFoundError,
    hints_for,
    register_hints,
)
from guidewire.hints import _HINT_REGISTRY

# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


class TestInheritance:
    """Every concrete error must inherit from GuidewireError."""

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            PermissionRequiredError,
            AmbiguousSelectorError,
            WindowNotFoundError,
        ],
    )
    def test_concrete_errors_inherit_base(self, cls: type) -> None:
        assert issubclass(cls, GuidewireError)

    def test_base_is_exception(self) -> None:
        assert issubclass(GuidewireError, Exception)


# ---------------------------------------------------------------------------
# Machine-readable error codes
# ---------------------------------------------------------------------------


class TestCodes:
    """Each error class carries a unique, snake_case error_code string."""

    @pytest.mark.parametrize(
        ("cls", "expected"),
        [
            (BackendUnavailableError, "backend_unavailable"),
            (ElementNotFoundError, "element_not_found"),
            (StaleElementReferenceError, "stale_element_reference"),
            (ActionNotSupportedError, "action_not_supported"),
            (PermissionRequiredError, "permission_required"),
            (AmbiguousSelectorError, "ambiguous_selector"),
            (WindowNotFoundError, "window_not_found"),
        ],
    )
    def test_error_code_value(self, cls: type[GuidewireError], expected: str) -> None:
        assert cls.error_code == expected

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            PermissionRequiredError,
            AmbiguousSelectorError,
            WindowNotFoundError,
        ],
    )
    def test_error_code_on_instance(self, cls: type[GuidewireError]) -> None:
        instance = cls()
        assert instance.error_code == cls.error_code

    def test_error_codes_are_unique(self) -> None:
        codes = [
            BackendUnavailableError.error_code,
            ElementNotFoundError.error_code,
            StaleElementReferenceError.error_code,
            ActionNotSupportedError.error_code,
            PermissionRequiredError.error_code,
            AmbiguousSelectorError.error_code,
            WindowNotFoundError.error_code,
        ]
        assert len(codes) == len(set(codes))

    def test_base_code_is_not_reused(self) -> None:
        concrete_codes = [
            BackendUnavailableError.error_code,
            ElementNotFoundError.error_code,
            StaleElementReferenceError.error_code,
            ActionNotSupportedError.error_code,
            PermissionRequiredError.error_code,
            AmbiguousSelectorError.error_code,
            WindowNotFoundError.error_code,
        ]
        assert GuidewireError.error_code not in concrete_codes


# ---------------------------------------------------------------------------
# Constructibility & message handling
# ---------------------------------------------------------------------------


class TestConstruction:
    """Errors can be raised with an optional human-readable message."""

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            PermissionRequiredError,
            AmbiguousSelectorError,
            WindowNotFoundError,
        ],
    )
    def test_default_message(self, cls: type[GuidewireError]) -> None:
        err = cls()
        assert isinstance(err.message, str)
        assert len(err.message) > 0

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            PermissionRequiredError,
            AmbiguousSelectorError,
            WindowNotFoundError,
        ],
    )
    def test_custom_message(self, cls: type[GuidewireError]) -> None:
        err = cls("custom detail")
        assert err.message == "custom detail"

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            PermissionRequiredError,
            AmbiguousSelectorError,
            WindowNotFoundError,
        ],
    )
    def test_str_representation(self, cls: type[GuidewireError]) -> None:
        err = cls("something went wrong")
        assert str(err) == "something went wrong"

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            PermissionRequiredError,
            AmbiguousSelectorError,
            WindowNotFoundError,
        ],
    )
    def test_catchability(self, cls: type[GuidewireError]) -> None:
        with pytest.raises(GuidewireError):
            raise cls("boom")

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            PermissionRequiredError,
            AmbiguousSelectorError,
            WindowNotFoundError,
        ],
    )
    def test_catch_specific(self, cls: type[GuidewireError]) -> None:
        with pytest.raises(cls):
            raise cls("boom")

    def test_catch_by_base_does_not_match_unrelated(self) -> None:
        """GuidewireError should not catch standard Python exceptions."""
        with pytest.raises(ValueError):
            try:
                raise ValueError("not a guidewire error")
            except GuidewireError:
                pytest.fail("GuidewireError caught a ValueError")


# ---------------------------------------------------------------------------
# Error hints — instance attribute
# ---------------------------------------------------------------------------


class TestHintsAttribute:
    """Every error instance carries a hints list auto-populated from the registry."""

    def test_base_class_hints_empty(self) -> None:
        """GuidewireError base has no registry entry so defaults to empty."""
        err = GuidewireError()
        assert err.hints == []

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            PermissionRequiredError,
            AmbiguousSelectorError,
            WindowNotFoundError,
        ],
    )
    def test_concrete_errors_auto_populate_hints(self, cls: type[GuidewireError]) -> None:
        err = cls()
        assert len(err.hints) > 0, f"{cls.__name__} should have auto-populated hints"
        assert all(isinstance(h, str) for h in err.hints)

    @pytest.mark.parametrize(
        "cls",
        [
            BackendUnavailableError,
            ElementNotFoundError,
        ],
    )
    def test_with_hints_adds_to_auto_populated(self, cls: type[GuidewireError]) -> None:
        err = cls("msg").with_hints("extra hint")
        assert "extra hint" in err.hints
        # Auto-populated hints should still be present
        assert len(err.hints) > 1

    def test_hints_are_independent_copies(self) -> None:
        """Each instance gets its own list, not a shared reference."""
        a = ElementNotFoundError()
        b = ElementNotFoundError()
        a.hints.append("extra")
        assert "extra" not in b.hints


# ---------------------------------------------------------------------------
# with_hints builder
# ---------------------------------------------------------------------------


class TestWithHints:
    """with_hints appends hints and returns self for chaining."""

    def test_returns_self(self) -> None:
        err = ElementNotFoundError("button")
        result = err.with_hints("try find")
        assert result is err

    def test_appends_single_hint(self) -> None:
        err = ElementNotFoundError("button").with_hints("try find")
        assert "try find" in err.hints

    def test_appends_multiple_hints(self) -> None:
        err = ElementNotFoundError("button").with_hints("hint a", "hint b")
        assert "hint a" in err.hints
        assert "hint b" in err.hints

    def test_chaining_with_auto_populated_hints(self) -> None:
        err = ElementNotFoundError("button").with_hints("added")
        assert "added" in err.hints
        # Auto-populated hints should also be present
        assert len(err.hints) > 1

    def test_chained_calls(self) -> None:
        err = ElementNotFoundError("button").with_hints("a").with_hints("b")
        assert "a" in err.hints
        assert "b" in err.hints

    def test_works_in_raise_expression(self) -> None:
        with pytest.raises(ElementNotFoundError) as exc_info:
            raise ElementNotFoundError("button").with_hints("use snapshot")
        assert "use snapshot" in exc_info.value.hints


# ---------------------------------------------------------------------------
# Hint registry
# ---------------------------------------------------------------------------


class TestHintRegistry:
    """hints_for and register_hints manage the global hint registry."""

    def test_known_error_code_returns_hints(self) -> None:
        hints = hints_for("element_not_found")
        assert len(hints) > 0
        assert all(isinstance(h, str) for h in hints)

    def test_all_concrete_codes_have_registered_hints(self) -> None:
        codes = [
            "backend_unavailable",
            "element_not_found",
            "stale_element_reference",
            "action_not_supported",
            "permission_required",
            "ambiguous_selector",
            "window_not_found",
        ]
        for code in codes:
            hints = hints_for(code)
            assert len(hints) > 0, f"{code} has no registered hints"

    def test_unknown_code_returns_empty(self) -> None:
        assert hints_for("totally_unknown_code") == []

    def test_returns_copy(self) -> None:
        """Mutating the returned list should not affect the registry."""
        hints = hints_for("element_not_found")
        hints.append("mutated")
        assert "mutated" not in hints_for("element_not_found")

    def test_register_overwrites_existing(self) -> None:
        original = hints_for("element_not_found")
        register_hints("element_not_found", ["new hint"])
        assert hints_for("element_not_found") == ["new hint"]
        # Restore original
        register_hints("element_not_found", original)

    def test_register_new_code(self) -> None:
        register_hints("custom_error", ["custom hint"])
        assert hints_for("custom_error") == ["custom hint"]
        # Cleanup
        del _HINT_REGISTRY["custom_error"]

    def test_register_makes_copy(self) -> None:
        """register_hints should store a copy, not the original reference."""
        original = ["mutable"]
        register_hints("test_copy_code", original)
        original.append("mutated")
        assert hints_for("test_copy_code") == ["mutable"]
        # Cleanup
        del _HINT_REGISTRY["test_copy_code"]


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------


class TestExports:
    """All error classes, base, and registry functions must be importable."""

    def test_all_contains_ten_entries(self) -> None:
        from guidewire import errors

        assert len(errors.__all__) == 10

    def test_all_entries_importable(self) -> None:
        from guidewire import errors

        for name in errors.__all__:
            obj = getattr(errors, name)
            assert obj is not None, f"{name} is None"

    def test_all_error_classes_are_guidewire_subclasses(self) -> None:
        from guidewire import errors

        error_names = [
            n for n in errors.__all__
            if n not in ("hints_for", "register_hints")
        ]
        for name in error_names:
            obj = getattr(errors, name)
            assert issubclass(obj, GuidewireError), f"{name} is not a GuidewireError subclass"

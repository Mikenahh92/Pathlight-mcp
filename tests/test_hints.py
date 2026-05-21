"""Tests for guidewire.hints — standalone hint registry module."""

import pytest

from guidewire.hints import _HINT_REGISTRY, hints_for, register_hints


# ---------------------------------------------------------------------------
# Module-level registry content
# ---------------------------------------------------------------------------


class TestRegistryDefaults:
    """All PRD-mandated + launch_app error codes have default hints registered."""

    @pytest.mark.parametrize(
        "error_code",
        [
            "backend_unavailable",
            "element_not_found",
            "stale_element_reference",
            "action_not_supported",
            "permission_required",
            "ambiguous_selector",
            "window_not_found",
            "launch_error",
            "app_not_found",
        ],
    )
    def test_default_hints_exist(self, error_code: str) -> None:
        hints = hints_for(error_code)
        assert len(hints) > 0, f"{error_code} has no registered hints"
        assert all(isinstance(h, str) for h in hints)

    def test_all_hints_are_non_empty_strings(self) -> None:
        for code, hints in _HINT_REGISTRY.items():
            for hint in hints:
                assert len(hint.strip()) > 0, f"{code} has an empty hint"


# ---------------------------------------------------------------------------
# hints_for lookups
# ---------------------------------------------------------------------------


class TestHintsFor:
    """hints_for returns copies and handles missing codes."""

    def test_unknown_code_returns_empty(self) -> None:
        assert hints_for("nonexistent_error_code") == []

    def test_returns_copy(self) -> None:
        hints = hints_for("element_not_found")
        hints.append("mutated")
        assert "mutated" not in hints_for("element_not_found")

    def test_returns_list_not_none(self) -> None:
        result = hints_for("anything")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# register_hints mutations
# ---------------------------------------------------------------------------


class TestRegisterHints:
    """register_hints adds or overwrites entries in the registry."""

    def test_register_new_code(self) -> None:
        register_hints("test_new_code", ["hint 1", "hint 2"])
        assert hints_for("test_new_code") == ["hint 1", "hint 2"]
        del _HINT_REGISTRY["test_new_code"]

    def test_overwrite_existing(self) -> None:
        original = hints_for("element_not_found")
        register_hints("element_not_found", ["replacement hint"])
        assert hints_for("element_not_found") == ["replacement hint"]
        # Restore
        register_hints("element_not_found", original)

    def test_stores_copy(self) -> None:
        mutable = ["original"]
        register_hints("test_copy", mutable)
        mutable.append("mutated")
        assert hints_for("test_copy") == ["original"]
        del _HINT_REGISTRY["test_copy"]


# ---------------------------------------------------------------------------
# Integration: errors auto-populate from registry
# ---------------------------------------------------------------------------


class TestAutoPopulation:
    """Error instances auto-populate hints from the registry at construction."""

    def test_concrete_error_gets_registry_hints(self) -> None:
        from guidewire.errors import ElementNotFoundError

        err = ElementNotFoundError("button")
        expected = hints_for("element_not_found")
        assert err.hints == expected

    def test_base_error_has_no_hints(self) -> None:
        from guidewire.errors import GuidewireError

        err = GuidewireError("generic")
        assert err.hints == []

    def test_with_hints_appends_to_registry_hints(self) -> None:
        from guidewire.errors import StaleElementReferenceError

        err = StaleElementReferenceError("ref").with_hints("extra")
        assert "extra" in err.hints
        assert len(err.hints) > 1  # registry hints + extra

    def test_each_instance_gets_own_copy(self) -> None:
        from guidewire.errors import WindowNotFoundError

        a = WindowNotFoundError()
        b = WindowNotFoundError()
        a.hints.append("unique to a")
        assert "unique to a" not in b.hints


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------


class TestHintsExports:
    """Module exports the public API."""

    def test_all_contains_public_functions(self) -> None:
        from guidewire import hints

        assert "hints_for" in hints.__all__
        assert "register_hints" in hints.__all__

    def test_all_entries_importable(self) -> None:
        from guidewire import hints

        for name in hints.__all__:
            obj = getattr(hints, name)
            assert callable(obj), f"{name} is not callable"

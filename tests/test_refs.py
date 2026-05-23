"""Tests for guidewire.refs — Element Reference Store."""

from guidewire.backends.types import NativeHandle
from guidewire.refs import ElementRefStore

# -- Helpers ------------------------------------------------------------------


def _h(suffix: str = "") -> NativeHandle:
    """Create a NativeHandle for testing."""
    return NativeHandle(f"handle-{suffix}")


# -- Constructor & defaults ---------------------------------------------------


class TestConstructor:
    def test_no_prefix_parameter(self) -> None:
        store = ElementRefStore()
        assert not hasattr(store, "prefix")

    def test_starts_empty(self) -> None:
        store = ElementRefStore()
        assert store.size == 0
        assert len(store) == 0


# -- store() ------------------------------------------------------------------


class TestStore:
    def test_first_ref_is_e1(self) -> None:
        store = ElementRefStore()
        ref = store.store(_h("a"))
        assert ref == "e1"

    def test_sequential_refs(self) -> None:
        store = ElementRefStore()
        assert store.store(_h("a")) == "e1"
        assert store.store(_h("b")) == "e2"
        assert store.store(_h("c")) == "e3"

    def test_custom_prefix(self) -> None:
        store = ElementRefStore()
        assert store.store(_h("win1"), prefix="w") == "w1"
        assert store.store(_h("win2"), prefix="w") == "w2"

    def test_mixed_prefixes(self) -> None:
        store = ElementRefStore()
        assert store.store(_h("a")) == "e1"
        assert store.store(_h("win1"), prefix="w") == "w1"
        assert store.store(_h("b")) == "e2"
        assert store.store(_h("win2"), prefix="w") == "w2"

    def test_duplicate_handles_produce_different_refs(self) -> None:
        """TC-012: duplicate handles should produce different refs."""
        store = ElementRefStore()
        handle = _h("dup")
        ref1 = store.store(handle)
        ref2 = store.store(handle)
        assert ref1 != ref2
        assert ref1 == "e1"
        assert ref2 == "e2"
        assert store.size == 2


# -- resolve() ----------------------------------------------------------------


class TestResolve:
    def test_resolve_returns_handle(self) -> None:
        store = ElementRefStore()
        handle = _h("target")
        ref = store.store(handle)
        assert store.resolve(ref) == handle

    def test_resolve_cross_prefix(self) -> None:
        store = ElementRefStore()
        handle = _h("win")
        ref = store.store(handle, prefix="w")
        assert store.resolve(ref) == handle

    def test_resolve_unknown_returns_none(self) -> None:
        store = ElementRefStore()
        store.store(_h("a"))
        assert store.resolve("e99") is None

    def test_resolve_empty_store_returns_none(self) -> None:
        store = ElementRefStore()
        assert store.resolve("e1") is None

    def test_resolve_after_clear_returns_none(self) -> None:
        store = ElementRefStore()
        ref = store.store(_h("a"))
        store.clear()
        assert store.resolve(ref) is None


# -- is_valid() ---------------------------------------------------------------


class TestIsValid:
    def test_valid_ref(self) -> None:
        store = ElementRefStore()
        ref = store.store(_h("a"))
        assert store.is_valid(ref) is True

    def test_invalid_ref(self) -> None:
        store = ElementRefStore()
        assert store.is_valid("e99") is False

    def test_is_valid_after_clear(self) -> None:
        store = ElementRefStore()
        ref = store.store(_h("a"))
        store.clear()
        assert store.is_valid(ref) is False


# -- next_ref() ---------------------------------------------------------------


class TestNextRef:
    def test_next_ref_returns_unused_ref(self) -> None:
        store = ElementRefStore()
        ref = store.next_ref()
        assert ref == "e1"
        # ref should not be in the store since nothing was stored
        assert store.is_valid(ref) is False
        assert store.size == 0

    def test_next_ref_advances_counter(self) -> None:
        store = ElementRefStore()
        assert store.next_ref() == "e1"
        assert store.next_ref() == "e2"
        assert store.next_ref() == "e3"
        assert store.size == 0

    def test_next_ref_with_custom_prefix(self) -> None:
        store = ElementRefStore()
        assert store.next_ref(prefix="w") == "w1"
        assert store.next_ref(prefix="w") == "w2"

    def test_next_ref_and_store_share_counter(self) -> None:
        """next_ref() and store() share the same counter for a prefix."""
        store = ElementRefStore()
        assert store.next_ref() == "e1"
        assert store.store(_h("a")) == "e2"
        assert store.next_ref() == "e3"


# -- clear() ------------------------------------------------------------------


class TestClear:
    def test_clear_empties_store(self) -> None:
        store = ElementRefStore()
        store.store(_h("a"))
        store.store(_h("b"))
        store.clear()
        assert store.size == 0

    def test_clear_resets_counters(self) -> None:
        """After clear, new refs should start from 1 again."""
        store = ElementRefStore()
        store.store(_h("a"))
        store.store(_h("b"))
        store.clear()
        assert store.store(_h("c")) == "e1"
        assert store.store(_h("d")) == "e2"

    def test_clear_resets_custom_prefix_counters(self) -> None:
        store = ElementRefStore()
        store.store(_h("a"), prefix="w")
        store.clear()
        assert store.store(_h("b"), prefix="w") == "w1"


# -- clear_prefix() ----------------------------------------------------------


class TestClearPrefix:
    def test_clears_only_target_prefix(self) -> None:
        """clear_prefix('e') removes e-refs but preserves w-refs."""
        store = ElementRefStore()
        store.store(_h("a"), prefix="w")
        store.store(_h("b"))
        store.store(_h("c"), prefix="w")
        store.store(_h("d"))
        store.clear_prefix("e")
        assert store.is_valid("w1") is True
        assert store.is_valid("w2") is True
        assert store.is_valid("e1") is False
        assert store.is_valid("e2") is False

    def test_resets_counter_for_prefix_only(self) -> None:
        """Counter for the cleared prefix restarts at 1; others continue."""
        store = ElementRefStore()
        store.store(_h("a"), prefix="w")
        store.store(_h("b"))
        store.clear_prefix("e")
        assert store.store(_h("c"), prefix="w") == "w2"  # counter continues
        assert store.store(_h("d")) == "e1"  # counter reset

    def test_clear_prefix_window(self) -> None:
        """clear_prefix('w') removes w-refs but preserves e-refs."""
        store = ElementRefStore()
        store.store(_h("a"), prefix="w")
        store.store(_h("b"))
        store.clear_prefix("w")
        assert store.is_valid("w1") is False
        assert store.is_valid("e1") is True

    def test_clear_prefix_on_empty_store(self) -> None:
        """clear_prefix on an empty store is a no-op."""
        store = ElementRefStore()
        store.clear_prefix("e")
        assert store.size == 0

    def test_clear_prefix_unknown_prefix(self) -> None:
        """clear_prefix for a prefix with no refs is safe."""
        store = ElementRefStore()
        store.store(_h("a"))
        store.clear_prefix("z")
        assert store.is_valid("e1") is True
        assert store.size == 1


# -- __len__ and __repr__ -----------------------------------------------------


class TestDunderMethods:
    def test_len_matches_size(self) -> None:
        store = ElementRefStore()
        store.store(_h("a"))
        store.store(_h("b"))
        assert len(store) == 2

    def test_repr(self) -> None:
        store = ElementRefStore()
        store.store(_h("a"))
        assert repr(store) == "ElementRefStore(size=1)"

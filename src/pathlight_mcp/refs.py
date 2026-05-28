"""Element Reference Store — maps short string references to native handles.

The reference store assigns compact, human-readable identifiers (e.g. ``"e1"``,
``"w3"``) to opaque :data:`~pathlight_mcp.backends.types.NativeHandle` values so that
MCP clients can address elements across tool calls without passing full native
handles (PRD R10).

Typical usage inside a snapshot or find-elements call::

    store = ElementRefStore()
    ref = store.store(native_handle, "e")   # → "e1"
    handle = store.resolve("e1")            # → native_handle

A single :class:`ElementRefStore` instance is valid for one snapshot generation.
Call :meth:`clear` to start a new generation (all previous refs become stale).
"""

from pathlight_mcp.backends.types import NativeHandle

__all__ = [
    "ElementRefStore",
]


class ElementRefStore:
    """Map between short string references and native handles.

    Maintains a per-prefix auto-incrementing counter so that the first element
    registered under prefix ``"e"`` becomes ``"e1"``, the second ``"e2"``, etc.
    """

    def __init__(self) -> None:
        self._ref_to_handle: dict[str, NativeHandle] = {}
        self._counters: dict[str, int] = {}

    # -- Public API -----------------------------------------------------------

    def store(
        self,
        handle: NativeHandle,
        prefix: str = "e",
    ) -> str:
        """Register a native handle and return a short string reference.

        Each call generates a new unique reference, even for duplicate handles.

        Args:
            handle: Opaque native element or window handle.
            prefix: Reference prefix (e.g. ``"e"`` or ``"w"``).
                Defaults to ``"e"``.

        Returns:
            A short reference string like ``"e1"``.
        """
        counter = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = counter
        ref = f"{prefix}{counter}"

        self._ref_to_handle[ref] = handle
        return ref

    def resolve(self, ref: str) -> NativeHandle | None:
        """Resolve a short reference back to its native handle.

        Args:
            ref: Short reference string (e.g. ``"e1"``).

        Returns:
            The opaque native handle, or ``None`` if *ref* is not in the store.
        """
        return self._ref_to_handle.get(ref)

    def is_valid(self, ref: str) -> bool:
        """Check whether a reference exists in the store.

        Args:
            ref: Short reference string (e.g. ``"e1"``).

        Returns:
            ``True`` if the reference is in the store, ``False`` otherwise.
        """
        return ref in self._ref_to_handle

    def next_ref(self, prefix: str = "e") -> str:
        """Generate the next reference string for a prefix without storing it.

        Args:
            prefix: Reference prefix (e.g. ``"e"`` or ``"w"``).
                Defaults to ``"e"``.

        Returns:
            A short reference string like ``"e1"`` that is not yet in the store.
        """
        counter = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = counter
        return f"{prefix}{counter}"

    def clear(self) -> None:
        """Remove all mappings, starting a fresh reference generation.

        Counters are also reset so the next :meth:`store` call for a given
        prefix restarts at ``1``.
        """
        self._ref_to_handle.clear()
        self._counters.clear()

    def clear_prefix(self, prefix: str) -> None:
        """Remove all mappings and reset the counter for a single prefix.

        Refs belonging to other prefixes are preserved.  The counter for
        *prefix* is reset so the next :meth:`store` call restarts at ``1``.

        Args:
            prefix: The single-character prefix to clear (e.g. ``"e"``).
        """
        keys_to_remove = [k for k in self._ref_to_handle if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._ref_to_handle[k]
        self._counters.pop(prefix, None)

    # -- Introspection --------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of registered references."""
        return len(self._ref_to_handle)

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return f"ElementRefStore(size={self.size})"

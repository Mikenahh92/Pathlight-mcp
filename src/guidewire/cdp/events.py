"""Thread-safe event buffer for CDP events.

Provides :class:`EventBuffer` — a bounded, thread-safe event store that
maintains **per-method deques** so that chatty events from one CDP domain
cannot evict events from another.  Each unique CDP method name gets its
own :class:`~collections.deque` with a capacity of ``maxsize_per_method``.

Multiple consumers can read from the buffer without removing events.
"""

import threading
from collections import deque

from guidewire.cdp.protocol import CDPEvent

__all__ = ["EventBuffer"]


class EventBuffer:
    """Thread-safe bounded event buffer with per-method deques.

    Each unique CDP method (e.g. ``"Page.loadEventFired"``) gets its own
    :class:`~collections.deque` bounded to ``maxsize_per_method``.  This
    prevents high-frequency events from one domain from evicting events
    from another domain.

    Args:
        maxsize_per_method: Maximum number of events to retain per method.
            ``0`` means unlimited per method.

    Attributes:
        maxsize_per_method: Configured per-method buffer capacity.
    """

    def __init__(self, maxsize_per_method: int = 1024) -> None:
        if maxsize_per_method < 0:
            raise ValueError("maxsize_per_method must be >= 0")
        self.maxsize_per_method = maxsize_per_method
        self._buffers: dict[str, deque[CDPEvent]] = {}
        self._lock = threading.Lock()

    def put(self, event: CDPEvent) -> None:
        """Add an event to the per-method buffer.

        If the per-method buffer is at capacity, the oldest event for that
        method is evicted.

        Args:
            event: The CDP event to buffer.
        """
        with self._lock:
            if event.method not in self._buffers:
                self._buffers[event.method] = deque(
                    maxlen=self.maxsize_per_method or None
                )
            self._buffers[event.method].append(event)

    def get_all(self) -> list[CDPEvent]:
        """Return a snapshot of all buffered events across all methods.

        Events are returned grouped by method (in insertion order of the
        method's first event), with each group ordered oldest-first.

        Returns:
            List of all events currently in the buffer.
        """
        with self._lock:
            result: list[CDPEvent] = []
            for dq in self._buffers.values():
                result.extend(dq)
            return result

    def get_by_method(self, method: str) -> list[CDPEvent]:
        """Return buffered events matching a specific method name.

        Args:
            method: The CDP event method to filter by
                (e.g. ``"Page.loadEventFired"``).

        Returns:
            List of matching events (oldest first).
        """
        with self._lock:
            dq = self._buffers.get(method)
            return list(dq) if dq is not None else []

    def get_latest(self, method: str | None = None) -> CDPEvent | None:
        """Return the most recent event, optionally filtered by method.

        Args:
            method: If provided, return the latest event matching this method.

        Returns:
            The most recent matching event, or ``None`` if no match.
        """
        with self._lock:
            if method is not None:
                dq = self._buffers.get(method)
                return dq[-1] if dq else None
            # No method filter — scan all deques for the overall latest
            all_events: list[CDPEvent] = []
            for dq in self._buffers.values():
                all_events.extend(dq)
            return all_events[-1] if all_events else None

    def clear(self) -> None:
        """Remove all events from all per-method buffers."""
        with self._lock:
            self._buffers.clear()

    def __len__(self) -> int:
        """Return the current total number of buffered events."""
        with self._lock:
            return sum(len(dq) for dq in self._buffers.values())

    @property
    def is_full(self) -> bool:
        """Return ``True`` if any per-method buffer is at capacity."""
        with self._lock:
            if self.maxsize_per_method == 0:
                return False
            return any(
                len(dq) >= self.maxsize_per_method for dq in self._buffers.values()
            )

    @property
    def methods(self) -> list[str]:
        """Return the unique method names with buffered events."""
        with self._lock:
            return list(self._buffers.keys())

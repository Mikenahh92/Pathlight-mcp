"""WebSessionRegistry — centralized CDP session lifecycle management (GW-098, Architecture §2.1).

Extracts session creation, caching, and teardown from
:class:`~guidewire.backends.web.WebBackend` into a dedicated registry so
that session management is reusable across tools (e.g. ``web_navigate``,
``web_connect``) without coupling to the full backend.

The registry:
    - Maintains a ``target_id → CDPSession`` cache.
    - Lazily attaches sessions on first access via
      :meth:`~guidewire.cdp.browser.CDPBrowser.attach`.
    - Caches domain wrappers (Accessibility, DOM, Input, Page, Target)
      per session to avoid repeated construction.
    - Supports bulk disposal of all sessions on backend teardown.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from guidewire.cdp.domains.accessibility import AccessibilityDomain
from guidewire.cdp.domains.dom import DOMDomain
from guidewire.cdp.domains.input import InputDomain
from guidewire.cdp.domains.page import PageDomain
from guidewire.cdp.domains.target import TargetDomain
from guidewire.cdp.session import CDPSession
from guidewire.errors import BackendUnavailableError, WindowNotFoundError

if TYPE_CHECKING:
    from guidewire.cdp.browser import CDPBrowser

logger = logging.getLogger(__name__)

__all__ = ["WebSessionRegistry"]

# Type alias for the cached domain tuple.
_DomainTuple = tuple[
    AccessibilityDomain, DOMDomain, InputDomain, PageDomain, TargetDomain
]


class WebSessionRegistry:
    """Manages CDP session lifecycle for a :class:`~guidewire.cdp.browser.CDPBrowser`.

    Args:
        browser_getter: A callable that returns the current
            :class:`~guidewire.cdp.browser.CDPBrowser` instance.  Using a
            callable ensures the registry always uses the backend's current
            browser (important when tests swap the browser after construction).

    Usage::

        registry = WebSessionRegistry(lambda: backend._browser)
        session = registry.get_or_create("target-123")
        acc, dom, inp, page, target = registry.get_domains("target-123")
        registry.dispose()
    """

    def __init__(self, browser_getter: "Callable[[], CDPBrowser]") -> None:
        self._browser_getter = browser_getter
        self._sessions: dict[str, CDPSession] = {}
        self._domains: dict[str, _DomainTuple] = {}

    @property
    def _browser(self) -> "CDPBrowser":
        """Resolve the current browser instance via the getter callable."""
        return self._browser_getter()

    # -- Session management ---------------------------------------------------

    def get_or_create(self, target_id: str) -> CDPSession:
        """Get an existing session or attach a new one for *target_id*.

        If a cached session exists and is still attached, it is returned
        immediately.  If the session was proactively marked detached via
        ``Target.detachedFromTarget`` (GW-117), a new session is created
        automatically.

        Args:
            target_id: The CDP target identifier.

        Returns:
            An attached :class:`~guidewire.cdp.session.CDPSession`.

        Raises:
            WindowNotFoundError: If the target does not exist in the browser.
        """
        session = self._sessions.get(target_id)
        if session is not None and session.is_attached:
            return session

        if session is not None and not session.is_attached:
            logger.info(
                "Stale session detected for target_id=%s, creating new session",
                target_id,
            )

        target = self._browser.get_target(target_id)
        if target is None:
            raise WindowNotFoundError(
                f"No browser target found with id {target_id!r}"
            )

        session = self._browser.attach(target)
        self._sessions[target_id] = session
        # Invalidate cached domains for this target since the session changed
        self._domains.pop(target_id, None)
        return session

    def get_active(self) -> CDPSession:
        """Return any attached session.

        Returns:
            The first active :class:`~guidewire.cdp.session.CDPSession`.

        Raises:
            BackendUnavailableError: If no session is active.
        """
        for session in self._sessions.values():
            if session.is_attached:
                return session
        raise BackendUnavailableError("No active CDP session")

    # -- Domain management ----------------------------------------------------

    def get_domains(self, target_id: str) -> _DomainTuple:
        """Get or create domain wrappers for the given target.

        Domain wrappers are cached per target so they are only constructed
        once per session lifecycle.

        Includes a staleness guard (GW-130): if cached domains exist but
        the underlying session is no longer attached (e.g. the browser
        detached it between operations), the cached domains are discarded
        so fresh ones are created on the re-attached session.

        Args:
            target_id: The browser target identifier.

        Returns:
            A tuple of (AccessibilityDomain, DOMDomain, InputDomain,
            PageDomain, TargetDomain).
        """
        domains = self._domains.get(target_id)
        if domains is not None:
            # Staleness guard: verify the session is still attached (GW-130).
            # If the session was detached (e.g. by the browser sending
            # Target.detachedFromTarget between operations), discard cached
            # domains so fresh ones are created on the re-attached session.
            session = self._sessions.get(target_id)
            if session is not None and session.is_attached:
                return domains
            # Session is stale — discard cached domains and fall through to
            # create fresh ones after session re-attach.
            logger.debug(
                "Stale domain cache detected for target_id=%s, refreshing",
                target_id,
            )
            self._domains.pop(target_id, None)

        session = self.get_or_create(target_id)
        acc = AccessibilityDomain(session)
        dom = DOMDomain(session)
        inp = InputDomain(session)
        page = PageDomain(session)
        target = TargetDomain(session)
        domains = (acc, dom, inp, page, target)
        self._domains[target_id] = domains
        return domains

    # -- Invalidation (GW-130) -----------------------------------------------

    def invalidate(self, target_id: str) -> None:
        """Invalidate the session and domain caches for *target_id*.

        Marks the session as detached and removes cached domain wrappers so
        that the next access creates a fresh session and domain objects.
        This is the recommended way to handle page transitions, navigation,
        and tab switches — it defers the actual re-attach cost until the
        next tool call (lazy invalidation).

        Args:
            target_id: The CDP target identifier to invalidate.
        """
        session = self._sessions.get(target_id)
        if session is not None:
            try:
                session.mark_detached()
            except Exception:
                logger.debug(
                    "Failed to mark session detached during invalidate for %s",
                    target_id,
                    exc_info=True,
                )
        self._domains.pop(target_id, None)
        logger.debug("Invalidated session registry for target_id=%s", target_id)

    def remove(self, target_id: str) -> None:
        """Remove the session and domain caches for *target_id* entirely.

        Unlike :meth:`invalidate`, this completely removes the entry from
        the session cache — used when a target is closed or destroyed.

        Args:
            target_id: The CDP target identifier to remove.
        """
        session = self._sessions.pop(target_id, None)
        if session is not None:
            try:
                session.close()
            except Exception:
                logger.debug(
                    "Error closing session during remove for %s",
                    target_id,
                    exc_info=True,
                )
        self._domains.pop(target_id, None)
        logger.debug("Removed session registry entry for target_id=%s", target_id)

    # -- Cache management -----------------------------------------------------

    def put_session(self, key: str, session: CDPSession) -> None:
        """Register an externally-created session (e.g. iframe sessions).

        This is used when sessions are created outside the normal
        ``get_or_create`` flow, such as iframe target attachment
        during multi-frame snapshot collection.

        Args:
            key: The key to store the session under (may differ from
                ``session.target.id``, e.g. ``"iframe:<frame_id>"``).
            session: The :class:`~guidewire.cdp.session.CDPSession` to cache.
        """
        self._sessions[key] = session

    def clear(self) -> None:
        """Detach and remove all cached sessions and domain wrappers."""
        for session in self._sessions.values():
            try:
                session.close()
            except Exception:
                logger.debug("Error closing session during registry clear")

        self._sessions.clear()
        self._domains.clear()

    def dispose(self) -> None:
        """Release all sessions and clear caches (idempotent).

        Alias for :meth:`clear` provided for semantic clarity during
        backend teardown.
        """
        self.clear()

    @property
    def sessions(self) -> dict[str, CDPSession]:
        """Read-only view of the session cache (for iteration)."""
        return dict(self._sessions)

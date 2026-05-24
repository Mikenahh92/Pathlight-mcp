"""CDP session — attach/detach and session-scoped command sending.

Provides :class:`CDPSession` — a session manager that wraps a
:class:`~guidewire.cdp.connection.CDPConnection` and adds session-awareness
via CDP ``Target.attachToTarget`` / ``Target.detachFromTarget``.  Commands
sent through a session are automatically scoped to the attached target.

Sessions are created by :class:`~guidewire.cdp.browser.CDPBrowser` and
should not be constructed directly by callers.
"""

import logging
import threading
from typing import Any

from guidewire.cdp._types import CDPTarget, SessionState
from guidewire.cdp.connection import CDPConnection
from guidewire.errors import GuidewireError

__all__ = ["CDPSession"]

logger = logging.getLogger(__name__)


class CDPSession:
    """Manages a CDP session attached to a specific browser target.

    A session scopes CDP commands to a single target using the
    ``Target.attachToTarget`` mechanism.  It wraps an existing
    :class:`~guidewire.cdp.connection.CDPConnection` (the root connection)
    and sends commands through it with the ``sessionId`` parameter.

    Usage::

        session = CDPSession(connection, target)
        await session.attach()
        result = session.send_command("Page.navigate", {"url": "https://example.com"})
        session.detach()

    Args:
        connection: The root :class:`~guidewire.cdp.connection.CDPConnection`
            to send commands through.
        target: The :class:`~guidewire.cdp._types.CDPTarget` to attach to.

    Attributes:
        target: The target this session is bound to.
        state: Current :class:`~guidewire.cdp._types.SessionState`.
        session_id: The CDP session identifier (set after attach).
    """

    def __init__(
        self,
        connection: CDPConnection,
        target: CDPTarget,
    ) -> None:
        self._connection = connection
        self.target = target
        self._state: SessionState = SessionState.DETACHED
        self._session_id: str | None = None
        self._lock = threading.Lock()

    # -- Public API -----------------------------------------------------------

    @property
    def state(self) -> SessionState:
        """Current session state."""
        return self._state

    @property
    def session_id(self) -> str | None:
        """The CDP session identifier, or ``None`` if not attached."""
        return self._session_id

    @property
    def is_attached(self) -> bool:
        """Return ``True`` if the session is currently attached to a target."""
        return self._state == SessionState.ATTACHED

    def attach(self, *, flatten: bool = True) -> str:
        """Attach to the target and return the session ID.

        Sends ``Target.attachToTarget`` on the root connection to create
        a session scoped to :attr:`target`.

        Args:
            flatten: If ``True`` (default), request a flattened session
                which receives CDP events directly without nesting.

        Returns:
            The CDP session identifier string.

        Raises:
            GuidewireError: If already attached or the attach fails.
            BackendUnavailableError: If the root connection is not open.
        """
        with self._lock:
            if self._state == SessionState.ATTACHED:
                raise GuidewireError("Session is already attached")
            self._state = SessionState.ATTACHING

        try:
            params: dict[str, Any] = {
                "targetId": self.target.id,
                "flatten": flatten,
            }
            result = self._connection.send_command("Target.attachToTarget", params)
            session_id = result.get("sessionId", "")

            if not session_id:
                raise GuidewireError("Target.attachToTarget returned no sessionId")

            with self._lock:
                self._session_id = session_id
                self._state = SessionState.ATTACHED

            logger.info(
                "CDP session attached: session_id=%s target_id=%s",
                session_id,
                self.target.id,
            )
            return session_id

        except Exception:
            with self._lock:
                self._state = SessionState.DETACHED
            raise

    def detach(self) -> None:
        """Detach from the target.

        Sends ``Target.detachFromTarget`` on the root connection to end
        the session.

        Raises:
            GuidewireError: If not currently attached.
            BackendUnavailableError: If the root connection is not open.
        """
        with self._lock:
            if self._state != SessionState.ATTACHED:
                raise GuidewireError("Session is not attached")
            self._state = SessionState.DETACHING
            session_id = self._session_id

        try:
            self._connection.send_command(
                "Target.detachFromTarget",
                {"sessionId": session_id},
            )
        except Exception:
            logger.debug(
                "Detach command failed for session_id=%s, forcing state to DETACHED",
                session_id,
            )
        finally:
            with self._lock:
                self._session_id = None
                self._state = SessionState.DETACHED

        logger.info("CDP session detached: session_id=%s", session_id)

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command scoped to this session's target.

        Injects the ``sessionId`` into the command so it is routed to
        the correct target by the browser.

        Args:
            method: CDP domain method (e.g. ``"Page.navigate"``).
            params: Method parameters (optional).
            timeout: Per-command timeout in seconds.

        Returns:
            The ``result`` dict from the CDP response.

        Raises:
            GuidewireError: If not currently attached.
            BackendUnavailableError: If the root connection is not open.
        """
        with self._lock:
            if self._state != SessionState.ATTACHED or self._session_id is None:
                raise GuidewireError("Session is not attached")
            sid = self._session_id

        # Merge sessionId into params for session-scoped routing
        effective_params: dict[str, Any] = {"sessionId": sid}
        if params:
            effective_params.update(params)

        return self._connection.send_command(method, effective_params, timeout=timeout)

    def close(self) -> None:
        """Detach from the target if currently attached (safe to call anytime)."""
        try:
            if self._state == SessionState.ATTACHED:
                self.detach()
        except Exception:
            logger.debug("Error during session close", exc_info=True)

    def __enter__(self) -> "CDPSession":
        self.attach()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

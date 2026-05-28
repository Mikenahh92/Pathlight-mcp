"""CDP session — attach/detach and session-scoped command sending.

Provides :class:`CDPSession` — a session manager that wraps a
:class:`~pathlight_mcp.cdp.connection.CDPConnection` and adds session-awareness
via CDP ``Target.attachToTarget`` / ``Target.detachFromTarget``.  Commands
sent through a session are automatically scoped to the attached target.

Sessions are created by :class:`~pathlight_mcp.cdp.browser.CDPBrowser` and
should not be constructed directly by callers.
"""

import logging
import threading
import time
from typing import Any

from pathlight_mcp.cdp._types import CDPTarget, SessionState
from pathlight_mcp.cdp.connection import CDPConnection
from pathlight_mcp.errors import PathlightMCPError

__all__ = ["CDPSession"]

logger = logging.getLogger(__name__)

# Maximum number of automatic re-attach attempts when a stale session is
# detected during command sending (GW-128).
_MAX_REATTACH_ATTEMPTS = 3

# Base delay in seconds for exponential backoff between re-attach attempts
# (GW-128).  Actual delay = _REATtach_BACKOFF_BASE * 2^attempt.
_REATTACH_BACKOFF_BASE = 0.25


class CDPSession:
    """Manages a CDP session attached to a specific browser target.

    A session scopes CDP commands to a single target using the
    ``Target.attachToTarget`` mechanism.  It wraps an existing
    :class:`~pathlight_mcp.cdp.connection.CDPConnection` (the root connection)
    and sends commands through it with the ``sessionId`` parameter.

    Stale session handling (GW-117, GW-128):
        The browser can invalidate a CDP session at any time by sending a
        ``Target.detachedFromTarget`` event.  When this happens the session's
        local state is proactively set to ``DETACHED`` via
        :meth:`mark_detached`.  The next :meth:`send_command` call will
        detect the detached state and attempt to re-attach transparently
        before retrying the command.  Up to 3 re-attach attempts are made
        with exponential backoff (0.25s base) to allow the browser time to
        stabilize (GW-128).

    Usage::

        session = CDPSession(connection, target)
        session.attach()
        result = session.send_command("Page.navigate", {"url": "https://example.com"})
        session.detach()

    Args:
        connection: The root :class:`~pathlight_mcp.cdp.connection.CDPConnection`
            to send commands through.
        target: The :class:`~pathlight_mcp.cdp._types.CDPTarget` to attach to.

    Attributes:
        target: The target this session is bound to.
        state: Current :class:`~pathlight_mcp.cdp._types.SessionState`.
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
            PathlightMCPError: If already attached or the attach fails.
            BackendUnavailableError: If the root connection is not open.
        """
        with self._lock:
            if self._state == SessionState.ATTACHED:
                raise PathlightMCPError("Session is already attached")
            self._state = SessionState.ATTACHING

        try:
            params: dict[str, Any] = {
                "targetId": self.target.id,
                "flatten": flatten,
            }
            result = self._connection.send_command("Target.attachToTarget", params)
            session_id = result.get("sessionId", "")

            if not session_id:
                raise PathlightMCPError("Target.attachToTarget returned no sessionId")

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
            PathlightMCPError: If not currently attached.
            BackendUnavailableError: If the root connection is not open.
        """
        with self._lock:
            if self._state != SessionState.ATTACHED:
                raise PathlightMCPError("Session is not attached")
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

        Passes the ``sessionId`` as a top-level JSON-RPC field so it is
        routed to the correct target by the browser.

        If the command fails because the session has been invalidated by
        the browser (e.g. ``Target.detachedFromTarget`` was received),
        automatically re-attaches to the target and retries the command
        with exponential backoff (up to 3 attempts, 0.25s base) (GW-128).

        Args:
            method: CDP domain method (e.g. ``"Page.navigate"``).
            params: Method parameters (optional).
            timeout: Per-command timeout in seconds.

        Returns:
            The ``result`` dict from the CDP response.

        Raises:
            PathlightMCPError: If not currently attached.
            BackendUnavailableError: If the root connection is not open.
        """
        return self._send_command_with_reattach(
            method,
            params,
            timeout=timeout,
        )

    def mark_detached(self) -> None:
        """Mark this session as detached due to a browser-side invalidation.

        Called when a ``Target.detachedFromTarget`` event is received for
        this session.  Proactively invalidates the session so that the next
        :meth:`send_command` call will detect the stale state and attempt
        a transparent re-attach (GW-117).

        Safe to call from any thread (the receiver loop or event handlers).
        """
        with self._lock:
            old_sid = self._session_id
            self._session_id = None
            self._state = SessionState.DETACHED

        if old_sid is not None:
            logger.info(
                "CDP session marked detached (browser-side invalidation): "
                "session_id=%s target_id=%s",
                old_sid,
                self.target.id,
            )

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

    # -- Internal -------------------------------------------------------------

    def _send_command_with_reattach(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        timeout: float | None = None,
        _attempt: int = 0,
    ) -> dict[str, Any]:
        """Send a command with automatic re-attach on stale session.

        If the session has been proactively marked detached (via
        :meth:`mark_detached`) or if the browser returns an error indicating
        the session is no longer valid, this method re-attaches to the target
        and retries the command with exponential backoff (GW-128).

        Up to ``_MAX_REATTACH_ATTEMPTS`` retries are attempted.  Each retry
        introduces a delay of ``_REATtach_BACKOFF_BASE * 2^attempt`` seconds
        before re-attaching, giving the browser time to stabilize.
        """
        with self._lock:
            if self._state != SessionState.ATTACHED or self._session_id is None:
                sid = None
            else:
                sid = self._session_id

        # Session was proactively marked detached — re-attach before sending
        if sid is None:
            if _attempt >= _MAX_REATTACH_ATTEMPTS:
                raise PathlightMCPError(
                    "Session is not attached and re-attach attempts exhausted "
                    f"(after {_MAX_REATTACH_ATTEMPTS} retries)"
                )
            try:
                self._backoff_and_reattach(_attempt)
            except Exception as reattach_exc:
                if _attempt + 1 < _MAX_REATTACH_ATTEMPTS:
                    logger.info(
                        "Re-attach failed (attempt %d/%d), retrying: %s",
                        _attempt + 1,
                        _MAX_REATTACH_ATTEMPTS,
                        reattach_exc,
                    )
                    return self._send_command_with_reattach(
                        method,
                        params,
                        timeout=timeout,
                        _attempt=_attempt + 1,
                    )
                raise
            with self._lock:
                sid = self._session_id

        try:
            return self._connection.send_command(
                method,
                params,
                session_id=sid,
                timeout=timeout,
            )
        except Exception as exc:
            # Check if the error indicates a stale/invalid session
            if self._is_stale_session_error(exc) and _attempt < _MAX_REATTACH_ATTEMPTS:
                logger.info(
                    "Stale session detected for method=%s session_id=%s, "
                    "reattaching (attempt %d/%d)",
                    method,
                    sid,
                    _attempt + 1,
                    _MAX_REATTACH_ATTEMPTS,
                )
                try:
                    self._backoff_and_reattach(_attempt)
                except Exception as reattach_exc:
                    if _attempt + 1 < _MAX_REATTACH_ATTEMPTS:
                        logger.info(
                            "Re-attach after stale error failed (attempt %d/%d), retrying: %s",
                            _attempt + 1,
                            _MAX_REATTACH_ATTEMPTS,
                            reattach_exc,
                        )
                        return self._send_command_with_reattach(
                            method,
                            params,
                            timeout=timeout,
                            _attempt=_attempt + 1,
                        )
                    raise
                return self._send_command_with_reattach(
                    method,
                    params,
                    timeout=timeout,
                    _attempt=_attempt + 1,
                )
            raise

    def _reattach(self) -> None:
        """Force the session into DETACHED state and re-attach.

        Used when a stale session is detected — creates a fresh session
        ID by calling ``Target.attachToTarget`` again.
        """
        with self._lock:
            self._session_id = None
            self._state = SessionState.DETACHED

        self.attach()

    def _backoff_and_reattach(self, attempt: int) -> None:
        """Sleep with exponential backoff, then re-attach (GW-128).

        Computes a delay of ``_REATtach_BACKOFF_BASE * 2^attempt`` seconds
        before calling :meth:`_reattach`.  The first attempt (attempt=0) has
        no delay to keep the fast path fast; subsequent attempts use
        increasing backoff.

        Args:
            attempt: Zero-indexed attempt number (0 = first retry).
        """
        if attempt > 0:
            delay = _REATTACH_BACKOFF_BASE * (2 ** (attempt - 1))
            logger.debug(
                "Re-attach backoff: sleeping %.3fs before attempt %d",
                delay,
                attempt + 1,
            )
            time.sleep(delay)
        self._reattach()

    @staticmethod
    def _is_stale_session_error(exc: Exception) -> bool:
        """Check if an exception indicates a stale/invalid CDP session.

        The browser returns specific error codes when a session is no
        longer valid:

        - ``-32000`` with messages like ``"Not attached to target"`` or
          ``"Session not found"``
        - ``ConnectionError`` can also indicate a stale WebSocket

        Args:
            exc: The exception to inspect.

        Returns:
            ``True`` if the error suggests the session is stale.
        """
        exc_str = str(exc).lower()
        # Check for CDP errors that indicate the session is gone
        if "not attached" in exc_str:
            return True
        if "session not found" in exc_str:
            return True
        if "session is closing" in exc_str:
            return True
        if "target closed" in exc_str:
            return True
        # Check for wrapped CDPError with code -32000
        from pathlight_mcp.cdp.protocol import CDPError

        if isinstance(exc, CDPError) and exc.code == -32000:
            return True
        # Check for PathlightMCPError wrapping a CDP stale session error
        return isinstance(exc, PathlightMCPError) and "not attached" in exc.message.lower()

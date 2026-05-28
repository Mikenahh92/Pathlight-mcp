"""Base class for CDP domain wrappers.

Provides :class:`CDPDomain` — the abstract base that all CDP domain wrappers
inherit from.  Each subclass wraps a single CDP domain (e.g. ``Accessibility``,
``DOM``, ``Page``) and exposes typed Python methods that send CDP commands
via a :class:`~pathlight_mcp.cdp.session.CDPSession`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pathlight_mcp.cdp._errors import map_cdp_error
from pathlight_mcp.cdp.protocol import CDPError
from pathlight_mcp.errors import BackendUnavailableError

if TYPE_CHECKING:
    from pathlight_mcp.cdp.session import CDPSession

__all__ = ["CDPDomain"]

logger = logging.getLogger(__name__)


class CDPDomain:
    """Abstract base for a typed CDP domain wrapper.

    Each domain wrapper holds a reference to a
    :class:`~pathlight_mcp.cdp.session.CDPSession` and exposes high-level,
    typed methods that translate between raw CDP JSON and Pathlight MCP's
    model objects.

    Subclasses set :attr:`domain` to the CDP domain name
    (e.g. ``"Accessibility"``) so that fully-qualified method names can
    be constructed automatically.

    Args:
        session: The active CDP session to send commands through.

    Attributes:
        domain: CDP domain name (set by subclasses).
    """

    domain: str = ""

    def __init__(self, session: CDPSession) -> None:
        self._session = session

    # -- Internal helpers -----------------------------------------------------

    def _method(self, name: str) -> str:
        """Build a fully-qualified CDP method name.

        Args:
            name: The unqualified method name (e.g. ``"getFullAXTree"``).

        Returns:
            The fully-qualified name (e.g. ``"Accessibility.getFullAXTree"``).
        """
        return f"{self.domain}.{name}"

    def _send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and return the result, mapping errors.

        Args:
            method: Fully-qualified CDP method name.
            params: Command parameters (optional).
            timeout: Per-command timeout in seconds.

        Returns:
            The ``result`` dict from the CDP response.

        Raises:
            PathlightMCPError: For mapped CDP errors.
            BackendUnavailableError: If the session/connection is not open.
        """
        try:
            return self._session.send_command(method, params, timeout=timeout)
        except CDPError as exc:
            raise map_cdp_error(exc) from exc
        except ConnectionError as exc:
            raise BackendUnavailableError(str(exc)) from exc

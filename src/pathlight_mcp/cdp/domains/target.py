"""CDP Target domain wrapper.

Provides :class:`TargetDomain` — typed methods for the CDP ``Target``
domain that discover, activate, and manage browser targets (pages,
workers, etc.).

Key methods:
    - :meth:`set_discover_targets` — enable target discovery events
    - :meth:`get_targets` — list all available targets
    - :meth:`activate_target` — bring a target to the foreground
    - :meth:`create_target` — open a new page/tab
    - :meth:`close_target` — close a target
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pathlight_mcp.cdp._types import CDPTarget
from pathlight_mcp.cdp.domains._base import CDPDomain

if TYPE_CHECKING:
    from pathlight_mcp.cdp.session import CDPSession

__all__ = ["TargetDomain"]

logger = logging.getLogger(__name__)


class TargetDomain(CDPDomain):
    """Typed wrapper for the CDP ``Target`` domain.

    Discovers and manages browser targets (pages, service workers, etc.).

    Args:
        session: The active CDP session to send commands through.
    """

    domain = "Target"

    def __init__(self, session: CDPSession) -> None:
        super().__init__(session)

    def set_discover_targets(self, discover: bool = True) -> None:
        """Enable or disable target discovery.

        Sends ``Target.setDiscoverTargets``.

        Args:
            discover: Whether to enable target discovery events.
        """
        self._send(
            self._method("setDiscoverTargets"),
            {"discover": discover},
        )

    def get_targets(self) -> list[CDPTarget]:
        """List all available targets.

        Sends ``Target.getTargets`` and converts the results to
        :class:`~pathlight_mcp.cdp._types.CDPTarget` instances.

        Returns:
            List of discovered targets.
        """
        result = self._send(self._method("getTargets"))
        raw_targets = result.get("targetInfos", [])
        return [CDPTarget.from_dict(t) for t in raw_targets]

    def activate_target(self, target_id: str) -> None:
        """Activate (focus) a target.

        Sends ``Target.activateTarget``.

        Args:
            target_id: The target identifier to activate.
        """
        self._send(
            self._method("activateTarget"),
            {"targetId": target_id},
        )

    def create_target(self, url: str) -> str:
        """Create a new page target.

        Sends ``Target.createTarget``.

        Args:
            url: URL to open in the new target.

        Returns:
            The new target's identifier.
        """
        result = self._send(
            self._method("createTarget"),
            {"url": url},
        )
        return result.get("targetId", "")

    def close_target(self, target_id: str) -> bool:
        """Close a target.

        Sends ``Target.closeTarget``.

        Args:
            target_id: The target identifier to close.

        Returns:
            ``True`` if the target was successfully closed.
        """
        result = self._send(
            self._method("closeTarget"),
            {"targetId": target_id},
        )
        return result.get("success", False)

    def get_target_info(self, target_id: str) -> CDPTarget | None:
        """Get info about a specific target.

        Sends ``Target.getTargetInfo``.

        Args:
            target_id: The target identifier.

        Returns:
            The target info, or ``None`` if not found.
        """
        result = self._send(
            self._method("getTargetInfo"),
            {"targetId": target_id},
        )
        info = result.get("targetInfo")
        if info:
            return CDPTarget.from_dict(info)
        return None

    def set_auto_attach(
        self,
        auto_attach: bool = True,
        wait_for_debugger_on_start: bool = False,
        flatten: bool = True,
    ) -> None:
        """Control auto-attachment to new targets.

        Sends ``Target.setAutoAttach``.

        Args:
            auto_attach: Whether to auto-attach to new targets.
            wait_for_debugger_on_start: Whether to wait for debugger
                on newly attached targets.
            flatten: Whether to flatten session nesting.
        """
        self._send(
            self._method("setAutoAttach"),
            {
                "autoAttach": auto_attach,
                "waitForDebuggerOnStart": wait_for_debugger_on_start,
                "flatten": flatten,
            },
        )

    def attach_to_target(self, target_id: str, *, flatten: bool = True) -> str:
        """Attach to a target and return the session ID.

        Sends ``Target.attachToTarget``.

        Args:
            target_id: The target to attach to.
            flatten: Whether to flatten the session.

        Returns:
            The session identifier.
        """
        result = self._send(
            self._method("attachToTarget"),
            {"targetId": target_id, "flatten": flatten},
        )
        return result.get("sessionId", "")

    def detach_from_target(self, session_id: str | None = None) -> None:
        """Detach from a target.

        Sends ``Target.detachFromTarget``.

        Args:
            session_id: The session to detach. Uses the current
                session if ``None``.
        """
        params: dict[str, Any] = {}
        if session_id is not None:
            params["sessionId"] = session_id

        self._send(self._method("detachFromTarget"), params or None)

    def expose_dev_tools_protocol(self, target_id: str, binding_name: str = "cdp") -> None:
        """Expose CDP bindings to a target.

        Sends ``Target.exposeDevToolsProtocol``.

        Args:
            target_id: The target to expose bindings to.
            binding_name: The binding name (default ``"cdp"``).
        """
        self._send(
            self._method("exposeDevToolsProtocol"),
            {"targetId": target_id, "bindingName": binding_name},
        )

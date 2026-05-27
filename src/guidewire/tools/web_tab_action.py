"""desktop.web_tab_action — perform actions on browser tabs via CDP.

Provides tab management operations including activate (focus), close,
new (open new tab), navigate, and reload.  Operates on CDP Target IDs
returned by :func:`~guidewire.tools.web_list_tabs` or
:func:`~guidewire.tools.web_connect`.

Safety classification: SENSITIVE — tab actions modify browser state
(new, close, navigate, reload) and require explicit user opt-in
(``SYSTEM_ACTION_RISK_MAP`` in :mod:`guidewire.safety`).

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~guidewire.backends.web.WebBackend` and CDP Target domain.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.backends.router import BackendRouter
from guidewire.hints import hints_for
from guidewire.safety import classify_system_action

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)

_VALID_ACTIONS = frozenset({"activate", "close", "new", "navigate", "reload"})


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_tab_action tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool performs tab management actions.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_tab_action")
    def web_tab_action(
        action: str,
        target_id: str | None = None,
        url: str | None = None,
    ) -> str:
        """Perform an action on a browser tab.

        Args:
            action: The tab action to perform.  One of:
                - ``"activate"`` — bring a tab to the foreground
                - ``"close"`` — close a tab
                - ``"new"`` — open a new tab (optionally to *url*)
                - ``"navigate"`` — navigate a tab to *url*
                - ``"reload"`` — reload the current page
            target_id: The CDP target identifier for the tab.  Required
                for ``activate``, ``close``, ``navigate``, and ``reload``
                actions.  Not required for ``new``.
            url: URL for ``new`` and ``navigate`` actions.  For
                ``new``, defaults to ``"about:blank"`` if not provided.

        Returns:
            A JSON object with ``success``, ``action``, and action-specific
            fields on success, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps({"success": True, "action": action})

        # --- Input validation ---
        action_lower = action.lower() if action else ""
        if action_lower not in _VALID_ACTIONS:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        f"Invalid action '{action}'. "
                        f"Valid actions: {', '.join(sorted(_VALID_ACTIONS))}"
                    ),
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action(
            "web_tab_action", target=f"{action_lower}:{target_id or 'new'}"
        )

        # --- Resolve the BackendRouter ---
        if not isinstance(backend, BackendRouter):
            return json.dumps(
                {
                    "error": "web_tab_action_error",
                    "message": (
                        "web_tab_action requires a BackendRouter backend — "
                        "the server is not configured for web support"
                    ),
                    "hints": hints_for("web_tab_action_error"),
                }
            )

        web = backend.web
        if web is None:
            return json.dumps(
                {
                    "error": "web_tab_action_error",
                    "message": "No web connection — call desktop.web_connect first",
                    "hints": hints_for("web_tab_action_error"),
                }
            )

        # --- Dispatch action ---
        try:
            if action_lower == "activate":
                return _activate(web, target_id, ref_store, assessment)
            elif action_lower == "close":
                return _close(web, target_id, ref_store, assessment)
            elif action_lower == "new":
                return _new(web, target_id, url, assessment)
            elif action_lower == "navigate":
                return _navigate(web, target_id, url, assessment)
            elif action_lower == "reload":
                return _reload(web, target_id, assessment)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_tab_action_error",
                    "message": f"Tab action '{action_lower}' failed: {exc}",
                    "hints": hints_for("web_tab_action_error"),
                }
            )

        # Unreachable, but satisfy type checkers
        return json.dumps(  # pragma: no cover
            {"error": "web_tab_action_error", "message": "Unknown action"}
        )


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def _activate(
    web: Any,
    target_id: str | None,
    ref_store: "ElementRefStore",
    assessment: Any,
) -> str:
    """Activate (focus) a browser tab."""
    if not target_id or not target_id.strip():
        return json.dumps(
            {
                "error": "validation_error",
                "message": "target_id is required for activate action",
                "hints": [],
            }
        )

    # Target.activateTarget is a browser-level command
    connection = web._browser.connection
    if connection is None:
        return json.dumps(
            {
                "error": "web_tab_action_error",
                "message": "No browser connection available",
                "hints": hints_for("web_tab_action_error"),
            }
        )

    connection.send_command("Target.activateTarget", {"targetId": target_id})

    # Invalidate AX/bounds caches on activate
    web._ax_cache.clear()
    web._bounds_cache.clear()

    return json.dumps(
        {
            "success": True,
            "action": "activate",
            "target_id": target_id,
            "risk": assessment.risk_level.lower(),
            "confirmation_required": assessment.confirmation_required,
            "target_summary": f"activate tab {target_id}",
        }
    )


def _close(
    web: Any,
    target_id: str | None,
    ref_store: "ElementRefStore",
    assessment: Any,
) -> str:
    """Close a browser tab."""
    if not target_id or not target_id.strip():
        return json.dumps(
            {
                "error": "validation_error",
                "message": "target_id is required for close action",
                "hints": [],
            }
        )

    # Target.closeTarget is a browser-level command
    connection = web._browser.connection
    if connection is None:
        return json.dumps(
            {
                "error": "web_tab_action_error",
                "message": "No browser connection available",
                "hints": hints_for("web_tab_action_error"),
            }
        )

    result = connection.send_command("Target.closeTarget", {"targetId": target_id})
    closed = result.get("success", False)

    # Clean up session registry and remove from ref store on close
    if closed:
        web._sessions.pop(target_id, None)
        # Invalidate AX/bounds caches on close
        web._ax_cache.clear()
        web._bounds_cache.clear()

    return json.dumps(
        {
            "success": closed,
            "action": "close",
            "target_id": target_id,
            "risk": assessment.risk_level.lower(),
            "confirmation_required": assessment.confirmation_required,
            "target_summary": f"close tab {target_id}",
        }
    )


def _new(
    web: Any,
    target_id: str | None,
    url: str | None,
    assessment: Any,
) -> str:
    """Open a new browser tab."""
    new_url = url or "about:blank"
    if not new_url.strip():
        new_url = "about:blank"

    # Target.createTarget is a browser-level command, sent on the root
    # connection (not through a page-scoped session).
    connection = web._browser.connection
    if connection is None:
        return json.dumps(
            {
                "error": "web_tab_action_error",
                "message": "No browser connection available for tab creation",
                "hints": hints_for("web_tab_action_error"),
            }
        )

    result = connection.send_command("Target.createTarget", {"url": new_url})
    new_target_id = result.get("targetId", "")

    if not new_target_id:
        return json.dumps(
            {
                "error": "web_tab_action_error",
                "message": "Browser did not return a target ID for new tab",
                "hints": hints_for("web_tab_action_error"),
            }
        )

    return json.dumps(
        {
            "success": True,
            "action": "new",
            "target_id": new_target_id,
            "url": new_url,
            "risk": assessment.risk_level.lower(),
            "confirmation_required": assessment.confirmation_required,
            "target_summary": f"new tab {new_target_id} -> {new_url}",
        }
    )


def _navigate(
    web: Any,
    target_id: str | None,
    url: str | None,
    assessment: Any,
) -> str:
    """Navigate a tab to a URL."""
    if not target_id or not target_id.strip():
        return json.dumps(
            {
                "error": "validation_error",
                "message": "target_id is required for navigate action",
                "hints": [],
            }
        )

    if not url or not url.strip():
        return json.dumps(
            {
                "error": "validation_error",
                "message": "url is required for navigate action",
                "hints": [],
            }
        )

    from guidewire.cdp.domains.page import PageDomain

    session = web._get_or_create_session(target_id)
    page = PageDomain(session)
    page.navigate(url)

    return json.dumps(
        {
            "success": True,
            "action": "navigate",
            "target_id": target_id,
            "url": url,
            "risk": assessment.risk_level.lower(),
            "confirmation_required": assessment.confirmation_required,
            "target_summary": f"navigate tab {target_id} to {url}",
        }
    )


def _reload(
    web: Any,
    target_id: str | None,
    assessment: Any,
) -> str:
    """Reload a browser tab using CDP Page.reload."""
    if not target_id or not target_id.strip():
        return json.dumps(
            {
                "error": "validation_error",
                "message": "target_id is required for reload action",
                "hints": [],
            }
        )

    from guidewire.cdp.domains.page import PageDomain

    session = web._get_or_create_session(target_id)
    page = PageDomain(session)
    page.reload()

    # Invalidate caches after reload
    web._ax_cache.clear()
    web._bounds_cache.clear()

    return json.dumps(
        {
            "success": True,
            "action": "reload",
            "target_id": target_id,
            "risk": assessment.risk_level.lower(),
            "confirmation_required": assessment.confirmation_required,
            "target_summary": f"reload tab {target_id}",
        }
    )

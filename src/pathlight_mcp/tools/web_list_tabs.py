"""desktop.web_list_tabs — list browser tabs via CDP Target domain.

Lists all browser page targets (tabs) available in the connected browser,
returning structured metadata for each tab including target ID, title, URL,
and type.  Requires an active web connection established via
:func:`~pathlight_mcp.tools.web_connect`.

Safety classification: READ_ONLY — read-only tab discovery.

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~pathlight_mcp.backends.web.WebBackend` and
:class:`~pathlight_mcp.cdp.browser.CDPBrowser` for target discovery.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends.router import BackendRouter, _tag
from pathlight_mcp.hints import hints_for

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore

logger = logging.getLogger(__name__)


def _is_internal_page(url: str) -> bool:
    """Check if a URL is an internal browser page that should be filtered.

    Internal pages like ``chrome://newtab``, ``edge://newtab``,
    ``about:blank``, and ``about:newtab`` are created by auto-launched
    browsers but cannot be meaningfully interacted with via CDP.

    Args:
        url: The page URL to check.

    Returns:
        ``True`` if the URL is an internal browser page.
    """
    if not url:
        return False
    internal_prefixes = (
        "chrome://",
        "chrome-extension://",
        "edge://",
        "about:",
    )
    return any(url.startswith(prefix) for prefix in internal_prefixes)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_list_tabs tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool lists all browser page targets.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_list_tabs")
    def web_list_tabs(
        include_internal: bool = False,
    ) -> str:
        """List all browser tabs (page targets) in the connected browser.

        Returns metadata for each tab including target ID, title, URL,
        type, and a ``w``-prefixed window reference.  Requires an active
        web connection (desktop.web_connect).

        Args:
            include_internal: When ``True``, include internal browser
                pages (``chrome://``, ``edge://``, ``about:``) in the
                results.  Defaults to ``False``.

        Returns:
            A JSON object with ``success``, ``tabs``, and ``tab_count``
            on success, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps({"success": True, "tabs": [], "tab_count": 0})

        # --- Resolve the BackendRouter ---
        if not isinstance(backend, BackendRouter):
            return json.dumps(
                {
                    "error": "web_list_tabs_error",
                    "message": (
                        "web_list_tabs requires a BackendRouter backend — "
                        "the server is not configured for web support"
                    ),
                    "hints": hints_for("web_list_tabs_error"),
                }
            )

        web = backend.web
        if web is None:
            return json.dumps(
                {
                    "error": "web_list_tabs_error",
                    "message": "No web connection — call desktop.web_connect first",
                    "hints": hints_for("web_list_tabs_error"),
                }
            )

        # --- Discover targets ---
        try:
            targets = web._browser.list_targets(target_type="page")
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_list_tabs_error",
                    "message": f"Failed to list browser targets: {exc}",
                    "hints": hints_for("web_list_tabs_error"),
                }
            )

        # --- Determine active target ID ---
        active_target_id: str | None = None
        try:
            active_session = web._get_active_session()
            active_target_id = active_session.target.id
        except Exception:
            pass

        # --- Build tab list ---
        tabs: list[dict[str, Any]] = []
        for target in targets:
            url = target.url or ""

            # Filter internal pages unless explicitly requested
            if not include_internal and _is_internal_page(url):
                continue

            # Assign w-prefixed ref via ref_store
            tagged = _tag(target, "web")
            window_ref = ref_store.store(tagged, prefix="w")

            tab_info: dict[str, Any] = {
                "ref": window_ref,
                "target_id": target.id,
                "title": target.title or "",
                "url": url,
                "type": target.type,
                "active": target.id == active_target_id,
            }
            tabs.append(tab_info)

        return json.dumps(
            {
                "success": True,
                "tabs": tabs,
                "tab_count": len(tabs),
            }
        )

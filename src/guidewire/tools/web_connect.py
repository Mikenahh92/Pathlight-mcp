"""desktop.web_connect — establish a CDP browser connection.

Connects to a Chromium-based browser's debug port, discovers available
page targets, and returns window references for each page so the caller
can immediately interact with the browser via the web backend.

The tool creates a :class:`~guidewire.backends.web.WebBackend` instance,
connects it, and registers it with the
:class:`~guidewire.backends.router.BackendRouter` so that subsequent
tool calls (snapshot, find, click, type_text, etc.) route transparently
to the web backend.

Safety classification: SENSITIVE — establishing a browser connection
requires explicit user opt-in (``SYSTEM_ACTION_RISK_MAP`` in
:mod:`guidewire.safety`).

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~guidewire.backends.web.WebBackend` and
:class:`~guidewire.cdp.browser.CDPBrowser` for all browser interaction.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.backends.router import BackendRouter
from guidewire.backends.web import WebBackend
from guidewire.hints import hints_for
from guidewire.safety import classify_system_action

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Default CDP connection parameters.
_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 9222


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_connect tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter`, the tool
    creates a :class:`WebBackend`, connects it, and registers it with
    the router.  Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_connect")
    def web_connect(
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
    ) -> str:
        """Connect to a browser's CDP debug port and discover page targets.

        Args:
            host: Hostname or IP of the browser debug target
                (default ``"localhost"``).
            port: Debug port number (default ``9222``).

        Returns:
            A JSON object with ``success``, ``pages``, ``risk``,
            ``confirmation_required``, and ``target_summary`` on success,
            or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return f"Connected to {host}:{port}"

        # --- Input validation ---
        if not host or not host.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "host must be a non-empty string",
                    "hints": [],
                }
            )

        if port <= 0 or port > 65535:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "port must be between 1 and 65535",
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        target_desc = f"{host}:{port}"
        assessment = classify_system_action("web_connect", target=target_desc)

        # --- Resolve the BackendRouter ---
        router = _require_router(backend)
        if isinstance(router, str):
            return router  # error JSON

        # --- Check if a web backend is already connected ---
        existing_web = router.web
        if existing_web is not None:
            # Already connected — return current state
            try:
                pages = _discover_pages(existing_web, ref_store)
                return json.dumps(
                    {
                        "success": True,
                        "pages": pages,
                        "host": host,
                        "port": port,
                        "warning": "Already connected — returning existing pages",
                        "risk": assessment.risk_level.lower(),
                        "confirmation_required": assessment.confirmation_required,
                        "target_summary": f"web connect {target_desc}",
                    }
                )
            except Exception as exc:
                # Existing backend is stale — dispose and reconnect
                logger.info("Existing web backend error, reconnecting: %s", exc)
                try:
                    router._web = None
                    router._backends.pop("web", None)
                    existing_web.dispose()
                except Exception:
                    pass

        # --- Create and connect the WebBackend ---
        try:
            web_backend = WebBackend(host=host, port=port)
            web_backend.connect()
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_connect_error",
                    "message": f"Failed to connect to browser at {target_desc}: {exc}",
                    "hints": hints_for("web_connect_error"),
                }
            )

        # --- Register with the router ---
        router._web = web_backend
        router._backends["web"] = web_backend

        # --- Discover available pages ---
        try:
            pages = _discover_pages(web_backend, ref_store)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_connect_error",
                    "message": f"Connected but failed to discover pages: {exc}",
                    "hints": hints_for("web_connect_error"),
                }
            )

        return json.dumps(
            {
                "success": True,
                "pages": pages,
                "host": host,
                "port": port,
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
                "target_summary": f"web connect {target_desc}",
            }
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_router(
    backend: "DesktopBackend",
) -> "BackendRouter | str":
    """Validate that *backend* is a :class:`BackendRouter`.

    Returns:
        The :class:`BackendRouter` instance, or a JSON error string.
    """
    if isinstance(backend, BackendRouter):
        return backend
    return json.dumps(
        {
            "error": "web_connect_error",
            "message": (
                "web_connect requires a BackendRouter backend — "
                "the server is not configured for web support"
            ),
            "hints": hints_for("web_connect_error"),
        }
    )


def _discover_pages(
    web_backend: WebBackend,
    ref_store: "ElementRefStore",
) -> list[dict[str, Any]]:
    """Discover browser page targets and return window refs.

    Args:
        web_backend: Connected :class:`WebBackend` instance.
        ref_store: Reference store for assigning window refs.

    Returns:
        List of dicts with ``ref``, ``title``, ``url`` keys.
    """
    from guidewire.backends.router import _tag
    from guidewire.cdp._types import CDPTarget

    handles = web_backend.list_windows()
    pages: list[dict[str, Any]] = []

    for handle in handles:
        # handle wraps a CDPTarget — extract it for URL and title
        target = handle
        title = ""
        url = ""
        if isinstance(target, CDPTarget):
            title = target.title or ""
            url = target.url or ""

        # Tag the handle for the router so downstream tools route correctly
        tagged = _tag(handle, "web")
        window_ref = ref_store.store(tagged, prefix="w")

        pages.append(
            {
                "ref": window_ref,
                "title": title,
                "url": url,
            }
        )

    return pages

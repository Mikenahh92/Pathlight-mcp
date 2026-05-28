"""desktop.web_connect — establish a CDP browser connection.

Connects to a Chromium-based browser's debug port, discovers available
page targets, and returns window references for each page so the caller
can immediately interact with the browser via the web backend.

The tool creates a :class:`~pathlight_mcp.backends.web.WebBackend` instance,
connects it, and registers it with the
:class:`~pathlight_mcp.backends.router.BackendRouter` so that subsequent
tool calls (snapshot, find, click, type_text, etc.) route transparently
to the web backend.

Auto-launch (GW-114): when no browser is reachable on the configured CDP
port, the tool can automatically spawn one with
``--remote-debugging-port`` using the :class:`~pathlight_mcp.cdp.browser_resolver.BrowserResolver`.
Pass ``browser="chrome"`` to override the default discovery order, or
``auto_launch=False`` to disable auto-launch entirely.

Safety classification: SENSITIVE — establishing a browser connection
requires explicit user opt-in (``SYSTEM_ACTION_RISK_MAP`` in
:mod:`pathlight_mcp.safety`).

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~pathlight_mcp.backends.web.WebBackend` and
:class:`~pathlight_mcp.cdp.browser.CDPBrowser` for all browser interaction.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends.router import BackendRouter
from pathlight_mcp.backends.web import WebBackend
from pathlight_mcp.cdp.browser_resolver import BROWSER_NAMES, BrowserResolver
from pathlight_mcp.hints import hints_for
from pathlight_mcp.safety import classify_system_action

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Default CDP connection parameters.
_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 9222

# Module-level resolver — shared across tool calls so the discovery cache
# and spawned process tracking persist for the server lifetime.
_resolver: BrowserResolver | None = None


def _get_resolver(port: int = _DEFAULT_PORT) -> BrowserResolver:
    """Return the shared :class:`BrowserResolver`, creating it on first use."""
    global _resolver
    if _resolver is None:
        _resolver = BrowserResolver(port=port)
    return _resolver


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
        browser: str | None = None,
        auto_launch: bool = True,
    ) -> str:
        """Connect to a browser's CDP debug port and discover page targets.

        If no browser is reachable on ``host:port`` and ``auto_launch`` is
        ``True`` (default), a Chromium-based browser is automatically
        launched with ``--remote-debugging-port`` and the connection is
        retried.

        Args:
            host: Hostname or IP of the browser debug target
                (default ``"localhost"``).
            port: Debug port number (default ``9222``).
            browser: Override the browser to launch (``"edge"``,
                ``"chrome"``, ``"brave"``, ``"chromium"``).  Ignored when
                a browser is already running on ``host:port``.
            auto_launch: When ``True`` (default), automatically launch a
                browser if no debug-enabled browser is found.  Set to
                ``False`` to preserve the original connect-only behavior.

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

        if browser is not None:
            browser_lower = browser.lower()
            if browser_lower not in BROWSER_NAMES:
                return json.dumps(
                    {
                        "error": "validation_error",
                        "message": (
                            f"Unknown browser '{browser}'. "
                            f"Available options: {', '.join(BROWSER_NAMES)}"
                        ),
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
        web_backend = WebBackend(host=host, port=port)
        try:
            web_backend.connect()
        except Exception as connect_exc:
            # Connection failed — try auto-launch if enabled
            if auto_launch:
                launched = _try_auto_launch(host, port, browser, connect_exc)
                if launched:
                    # Retry connection after auto-launch
                    try:
                        web_backend = WebBackend(host=host, port=port)
                        web_backend.connect()
                    except Exception as retry_exc:
                        return _fallback_error(target_desc, retry_exc, auto_launch_enabled=True)
                else:
                    # Auto-launch itself failed — return fallback error
                    return _fallback_error(target_desc, connect_exc, auto_launch_enabled=True)
            else:
                # Auto-launch disabled — return original error with fallback hint
                return _fallback_error(target_desc, connect_exc, auto_launch_enabled=False)

        # --- Register with the router ---
        router._web = web_backend
        router._backends["web"] = web_backend

        # --- Enable target discovery for popup detection ---
        try:
            web_backend.enable_popup_detection()
        except Exception:
            logger.debug("Failed to enable popup detection after connect", exc_info=True)

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

        result: dict[str, Any] = {
            "success": True,
            "pages": pages,
            "host": host,
            "port": port,
            "risk": assessment.risk_level.lower(),
            "confirmation_required": assessment.confirmation_required,
            "target_summary": f"web connect {target_desc}",
        }

        # Include auto-launch info if we launched a browser
        resolver = _get_resolver(port)
        if resolver.spawned_process is not None:
            result["auto_launched"] = True

        return json.dumps(result)


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


def _try_auto_launch(
    host: str,
    port: int,
    browser: str | None,
    original_exc: Exception,
) -> bool:
    """Attempt to auto-launch a browser and wait for readiness.

    Args:
        host: CDP hostname.
        port: CDP port.
        browser: Optional browser name override.
        original_exc: The exception that triggered auto-launch.

    Returns:
        ``True`` if a browser was launched and is ready, ``False`` otherwise.
    """
    resolver = _get_resolver(port)

    try:
        resolver.launch(browser, port=port)
    except (FileNotFoundError, RuntimeError, OSError) as launch_exc:
        logger.info(
            "Auto-launch failed (no browser found or launch error): %s",
            launch_exc,
        )
        return False

    logger.info("Browser auto-launched, waiting for CDP endpoint on %s:%s", host, port)
    ready = resolver.wait_for_ready(host=host, port=port)

    if not ready:
        logger.warning("Auto-launched browser did not become ready in time")
        resolver.cleanup()
        return False

    logger.info("Auto-launched browser is ready on %s:%s", host, port)
    return True


def _fallback_error(
    target_desc: str,
    exc: Exception,
    *,
    auto_launch_enabled: bool,
) -> str:
    """Build a structured error response with desktop automation fallback hint.

    Args:
        target_desc: ``host:port`` string.
        exc: The exception that caused the failure.
        auto_launch_enabled: Whether auto-launch was attempted.

    Returns:
        JSON error string.
    """
    hints = hints_for("web_connect_error")

    if auto_launch_enabled:
        hints.append(
            "Auto-launch was attempted but failed — the desktop automation "
            "fallback may work: use launch_app to start a browser, then "
            "snapshot + find to interact with it via the native accessibility "
            "backend instead of CDP."
        )
    else:
        hints.append(
            "auto_launch is disabled — either launch a browser manually with "
            "--remote-debugging-port, or use the desktop automation fallback: "
            "launch_app + snapshot + find to interact with the browser via "
            "the native accessibility backend."
        )

    return json.dumps(
        {
            "error": "web_connect_error",
            "message": f"Failed to connect to browser at {target_desc}: {exc}",
            "hints": hints,
        }
    )


def _discover_pages(
    web_backend: WebBackend,
    ref_store: "ElementRefStore",
) -> list[dict[str, Any]]:
    """Discover browser page targets and return window refs.

    Filters out internal browser pages (e.g. ``chrome://newtab``,
    ``edge://newtab``, ``about:blank``) that are created by
    auto-launched browsers but are not useful for web automation.

    Args:
        web_backend: Connected :class:`WebBackend` instance.
        ref_store: Reference store for assigning window refs.

    Returns:
        List of dicts with ``ref``, ``title``, ``url`` keys.
    """
    from pathlight_mcp.backends.router import _tag
    from pathlight_mcp.cdp._types import CDPTarget

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

        # Filter internal browser pages (GW-115)
        if _is_internal_page(url):
            continue

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


def _is_internal_page(url: str) -> bool:
    """Check if a URL is an internal browser page that should be filtered.

    Internal pages like ``chrome://newtab``, ``edge://newtab``,
    ``about:blank``, and ``about:newtab`` are created by auto-launched
    browsers but cannot be meaningfully interacted with via CDP
    ``Runtime.evaluate`` and should be excluded from target discovery.

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

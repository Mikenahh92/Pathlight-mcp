"""desktop.web_navigate — navigate a browser page to a URL.

Navigates a connected browser page to a specified URL using the CDP
``Page.navigate`` command.  Requires an active web connection established
via :func:`~pathlight_mcp.tools.web_connect` — the window reference passed to
this tool must be one of the ``w``-prefixed refs returned by
``desktop.web_connect``.

After navigation, the tool waits for the page load to complete (with a
configurable timeout) and returns the final URL and page title.

Safety classification: SENSITIVE — navigating to a URL is a destructive
browser action that requires explicit user opt-in
(``SYSTEM_ACTION_RISK_MAP`` in :mod:`pathlight_mcp.safety`).

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~pathlight_mcp.backends.web.WebBackend` for session management and
:class:`~pathlight_mcp.cdp.domains.page.PageDomain` for navigation.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends.router import BackendRouter, _untag
from pathlight_mcp.backends.web import WebBackend
from pathlight_mcp.cdp._types import CDPTarget
from pathlight_mcp.hints import hints_for
from pathlight_mcp.safety import classify_system_action

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Default navigation timeout in seconds.
_DEFAULT_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_navigate tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool navigates the specified page to a URL.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_navigate")
    async def web_navigate(
        window_ref: str,
        url: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> str:
        """Navigate a connected browser page to a URL.

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page to navigate.
            url: URL to navigate to (must include scheme, e.g.
                ``"https://example.com"``).
            timeout: Maximum seconds to wait for the page to load
                (default 10).  Set to 0 to skip waiting.

        Returns:
            A JSON object with ``success``, ``url``, ``title``,
            ``risk``, ``confirmation_required``, and ``target_summary``
            on success, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return f"Navigated {window_ref} to {url}"

        # --- Input validation ---
        if not window_ref or not window_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "window_ref must be a non-empty string",
                    "hints": [],
                }
            )

        if not url or not url.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "url must be a non-empty string",
                    "hints": [],
                }
            )

        if timeout < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "timeout must be non-negative",
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("web_navigate", target=url)

        # --- Resolve the BackendRouter ---
        if not isinstance(backend, BackendRouter):
            return json.dumps(
                {
                    "error": "web_navigate_error",
                    "message": (
                        "web_navigate requires a BackendRouter backend — "
                        "the server is not configured for web support"
                    ),
                    "hints": hints_for("web_navigate_error"),
                }
            )

        web = backend.web
        if web is None:
            return json.dumps(
                {
                    "error": "web_navigate_error",
                    "message": "No web connection — call desktop.web_connect first",
                    "hints": hints_for("web_navigate_error"),
                }
            )

        # --- Resolve the window reference to a CDPTarget ---
        tagged_handle = ref_store.resolve(window_ref)
        if tagged_handle is None:
            return json.dumps(
                {
                    "error": "web_navigate_error",
                    "message": f"Window reference '{window_ref}' not found in ref store",
                    "hints": hints_for("web_navigate_error"),
                }
            )

        inner, backend_id = _untag(tagged_handle)
        if backend_id != "web":
            return json.dumps(
                {
                    "error": "web_navigate_error",
                    "message": (
                        f"Window reference '{window_ref}' is not a web window "
                        f"(backend_id={backend_id!r})"
                    ),
                    "hints": hints_for("web_navigate_error"),
                }
            )

        # inner should be a CDPTarget (possibly wrapped in NativeHandle)
        target = _extract_target(inner)
        if target is None:
            return json.dumps(
                {
                    "error": "web_navigate_error",
                    "message": f"Could not resolve window reference '{window_ref}' to a CDP target",
                    "hints": hints_for("web_navigate_error"),
                }
            )

        # --- Navigate ---
        try:
            session = web._get_or_create_session(target.id)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_navigate_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_navigate_error"),
                }
            )

        try:
            from pathlight_mcp.cdp.domains.page import PageDomain

            page = PageDomain(session)
            nav_result = page.navigate(url)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_navigate_error",
                    "message": f"Navigation failed: {exc}",
                    "hints": hints_for("web_navigate_error"),
                }
            )

        # --- Wait for page load (optional) ---
        frame_id = nav_result.get("frameId", "")
        final_url = url
        title = ""
        if timeout > 0:
            final_url, title = await _wait_for_load(web, target.id, timeout, frame_id)
        else:
            # Try to get current title without waiting
            try:
                targets = web._browser.list_targets(target_type="page")
                for t in targets:
                    if t.id == target.id:
                        title = t.title or ""
                        final_url = t.url or url
                        break
            except Exception:
                pass

        # --- Invalidate caches (AC-5 / Architecture §5, GW-130) ---
        # Navigation replaces the page DOM, so all cached AX trees, bounds,
        # element references, and CDP session state are stale.  Invalidate
        # the session registry so the next tool call gets a fresh session
        # and domain wrappers — this prevents stale session errors in long
        # sequential browsing operations (GW-130).
        ref_store.clear_prefix("e")
        web._invalidate_session(target.id)

        return json.dumps(
            {
                "success": True,
                "url": final_url,
                "title": title,
                "frame_id": frame_id,
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
                "target_summary": f"navigate to {url}",
            }
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_target(handle: object) -> CDPTarget | None:
    """Extract a CDPTarget from a possibly-wrapped handle.

    The handle may be a CDPTarget directly, or it could be wrapped
    in a NativeHandle (which is ``NewType("NativeHandle", Any)``).

    Args:
        handle: The handle to extract from.

    Returns:
        A :class:`CDPTarget` instance, or ``None``.
    """
    if isinstance(handle, CDPTarget):
        return handle
    # NativeHandle is NewType — at runtime it's just the raw value
    return None


async def _wait_for_load(
    web_backend: WebBackend,
    target_id: str,
    timeout: float,
    expected_frame_id: str,
) -> tuple[str, str]:
    """Poll the browser for page load completion using async sleep.

    Checks the target list for an updated URL and title, polling at a
    short interval until the timeout expires.

    Uses ``asyncio.sleep`` instead of ``time.sleep`` to avoid blocking
    the MCP event loop (Architecture §3.2).

    Args:
        web_backend: Connected :class:`WebBackend`.
        target_id: The CDP target identifier.
        timeout: Maximum wait time in seconds.
        expected_frame_id: The frame ID from the navigate response.

    Returns:
        Tuple of (final_url, title).
    """
    import time

    deadline = time.monotonic() + timeout
    poll_interval = 0.2

    final_url = ""
    title = ""

    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        try:
            targets = web_backend._browser.list_targets(target_type="page")
            for t in targets:
                if t.id == target_id:
                    final_url = t.url or ""
                    title = t.title or ""
                    # Consider loaded if we have a non-empty URL
                    if final_url:
                        return final_url, title
        except Exception:
            pass

    return final_url, title

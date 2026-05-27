"""desktop.web_type — type text into a web page element via CSS selector or ref.

Resolves a target element using either a CSS selector string or an existing
element reference, focuses it, clears existing text if requested, and inserts
new text via the CDP Input domain.  Supports implicit auto-wait with
configurable timeout for selector resolution.  When ``slowly=True``, types
each character individually via ``Input.dispatchKeyEvent`` for cases where
``Input.insertText`` is not intercepted by the page (AC8).

The tool operates at the web-session layer — it bypasses the DesktopBackend
ABC and directly uses CDP sessions, similar to ``web_evaluate`` and
``web_navigate``.

Safety classification: INTERACTION — typing into a web element is a standard
user interaction with bounded side-effects.

Tool-layer only — no ABC changes.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.hints import hints_for
from guidewire.safety import classify_system_action
from guidewire.tools._web_selector import (
    DEFAULT_TIMEOUT_MS,
    resolve_element,
    resolve_web_session,
)

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_type tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool resolves the target element and inserts
    text.  Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_type")
    def web_type(
        window_ref: str,
        text: str,
        selector: str | None = None,
        element_ref: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        clear: bool = False,
        slowly: bool = False,
    ) -> str:
        """Type text into a web page element by CSS selector or element reference.

        Resolves the target element using a CSS selector (preferred) or an
        existing element reference from a prior snapshot.  If the element is
        not immediately found via selector, polls automatically until the
        timeout expires (implicit auto-wait).

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page.
            text: The text to type into the element.
            selector: CSS selector to resolve the target element
                (e.g. ``"#email-input"``, ``"input[name='user']"``).
            element_ref: Existing element reference (``e``-prefixed)
                from a prior snapshot.  Ignored when *selector* is set.
            timeout_ms: Maximum milliseconds to wait for the selector
                to resolve (default 5000).  Set to 0 for no wait.
            clear: When ``True``, clear existing text before typing
                (default ``False``).
            slowly: When ``True``, type one character at a time via
                ``dispatchKeyEvent`` instead of the bulk ``insertText``
                call.  Use this when the page intercepts keyboard events
                (e.g. React controlled inputs, masked fields).

        Returns:
            A JSON object with ``success``, ``selector``, ``risk``,
            ``confirmation_required``, and ``target_summary`` on success,
            or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "selector": selector,
                    "text_length": len(text),
                    "slowly": slowly,
                }
            )

        # --- Input validation ---
        if not window_ref or not window_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "window_ref must be a non-empty string",
                    "hints": [],
                }
            )

        if selector is None and element_ref is None:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "Either selector or element_ref must be provided",
                    "hints": [],
                }
            )

        if selector is not None and not selector.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "selector must be a non-empty string when provided",
                    "hints": [],
                }
            )

        if timeout_ms < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "timeout_ms must be non-negative",
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("web_type", target=selector or element_ref)

        # --- Resolve the web session ---
        web, target = resolve_web_session(backend, ref_store, window_ref)
        if isinstance(web, str):
            return web  # error JSON
        if isinstance(target, str):
            return target  # error JSON

        # --- Resolve the element ---
        result = resolve_element(web, target, ref_store, selector, element_ref, timeout_ms)
        if isinstance(result, str):
            return result  # error JSON

        node_id, _bounds = result

        # --- Focus, optionally clear, then type ---
        try:
            session = web._get_or_create_session(target.id)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_element_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_element_error"),
                }
            )

        try:
            from guidewire.cdp.domains.dom import DOMDomain
            from guidewire.cdp.domains.input import InputDomain

            dom = DOMDomain(session)
            inp = InputDomain(session)

            # Focus the element
            if node_id is not None:
                try:
                    dom.scroll_into_view_if_needed(node_id=node_id)
                except Exception:
                    logger.debug(
                        "scrollIntoViewIfNeeded failed for node %s",
                        node_id,
                        exc_info=True,
                    )
                try:
                    dom.focus(node_id=node_id)
                except Exception:
                    logger.debug(
                        "DOM focus failed for node %s",
                        node_id,
                        exc_info=True,
                    )

            # Optionally clear existing text
            if clear:
                inp.dispatch_key_event("keyDown", key="a", modifiers=2)  # Ctrl+A
                inp.dispatch_key_event("keyUp", key="a", modifiers=2)
                inp.dispatch_key_event("keyDown", key="Backspace")
                inp.dispatch_key_event("keyUp", key="Backspace")

            # Insert text — either bulk or per-character
            if slowly:
                _type_slowly(inp, text)
            else:
                inp.insert_text(text)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_element_error",
                    "message": f"Type dispatch failed: {exc}",
                    "hints": hints_for("web_element_error"),
                }
            )

        return json.dumps(
            {
                "success": True,
                "selector": selector,
                "text_length": len(text),
                "clear": clear,
                "slowly": slowly,
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
                "target_summary": f"type into {selector or element_ref}",
            }
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _type_slowly(inp: Any, text: str) -> None:
    """Type text one character at a time via dispatchKeyEvent.

    For each character, dispatches a ``keyDown`` followed by a ``char``
    event (with the ``text`` field set) and a ``keyUp`` event.  This
    matches how real keyboard input reaches the page and works with
    frameworks that intercept individual key events (React, Vue, etc.).

    Args:
        inp: :class:`InputDomain` for key event dispatch.
        text: The text to type character-by-character.
    """
    for ch in text:
        # keyDown for the character
        inp.dispatch_key_event("keyDown", key=ch, text=ch)
        # char event carries the generated text
        inp.dispatch_key_event("char", key=ch, text=ch)
        # keyUp to complete the stroke
        inp.dispatch_key_event("keyUp", key=ch)

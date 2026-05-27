"""desktop.type_text — type text into a desktop accessibility element.

Resolves an ``e``-prefixed short reference to a native handle via the
:class:`~guidewire.refs.ElementRefStore`, validates it through the backend,
applies safety classification, and invokes the OS accessibility type action
via :meth:`~guidewire.backends.base.DesktopBackend.perform_action` with
``DesktopAction.TYPE`` (PRD R7).

Slowly mode (GW-123):
    When ``slowly=True`` and the element belongs to a web backend, the tool
    types each character individually using CDP ``Input.dispatchKeyEvent``
    instead of the bulk ``Input.insertText`` command.  This is essential for
    web pages with typeahead, input masking, or character-by-character
    validation (e.g. date fields, phone number fields).

    For native elements, ``slowly`` has no effect — the text is typed in
    one shot as usual.
"""

import json
import time
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.backends.router import BackendRouter, _untag
from guidewire.backends.types import DesktopAction
from guidewire.cdp._types import AXNode, CDPTarget
from guidewire.errors import (
    ActionNotSupportedError,
    ElementNotFoundError,
    StaleElementReferenceError,
)
from guidewire.hints import hints_for
from guidewire.models import ElementStates, NormalizedElement
from guidewire.safety import classify

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.cdp.domains.input import InputDomain
    from guidewire.refs import ElementRefStore

# Delay between keystrokes in "slowly" mode (seconds).
_SLOWLY_CHAR_DELAY = 0.05


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.type_text tool on *mcp*.

    When *backend* is provided the tool resolves *element_ref* through
    *ref_store*, validates the handle, runs safety classification, and
    delegates to ``backend.perform_action(TYPE, text=text)``.  Without a
    backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.type_text")
    def type_text(
        element_ref: str,
        text: str,
        slowly: bool = False,
    ) -> str:
        """Type text into a desktop element.

        Args:
            element_ref: Short reference handle for the target element
                (e.g. ``"e1"``).
            text: The text to type into the element.
            slowly: When ``True``, type each character individually
                (web elements only).  Use for pages with typeahead,
                input masking, or per-character validation.

        Returns:
            A JSON object with ``success``, ``ref``, ``role``, ``risk``,
            and ``target_summary`` on success, or a structured error
            payload on failure.
        """
        if backend is None or ref_store is None:
            return f'Typed "{text}" into {element_ref}'

        # --- Input validation ---
        if not element_ref or not element_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "element_ref must be a non-empty string",
                    "ref": element_ref,
                    "hints": [],
                }
            )

        # --- Resolve reference ---
        handle = ref_store.resolve(element_ref)

        if handle is None:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (f"Element reference '{element_ref}' not found in reference store"),
                    "ref": element_ref,
                    "hints": hints_for("element_not_found"),
                }
            )

        # --- Staleness check ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Element reference '{element_ref}' is no longer valid"),
                    "ref": element_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Check if slowly mode applies (web backend only) ---
        if slowly and isinstance(backend, BackendRouter):
            slow_result = _type_text_slowly_web(
                backend,
                ref_store,
                handle,
                element_ref,
                text,
            )
            if slow_result is not None:
                return slow_result
            # If slow_result is None, the element is not web — fall through
            # to the normal path.

        # --- Perform type action with structured error handling ---
        try:
            backend.perform_action(handle, DesktopAction.TYPE, text=text)
        except ElementNotFoundError as exc:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (
                        f"Element reference '{element_ref}' not found in accessibility tree"
                    ),
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )
        except StaleElementReferenceError as exc:
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Element reference '{element_ref}' is stale"),
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )
        except ActionNotSupportedError as exc:
            return json.dumps(
                {
                    "error": "action_not_supported",
                    "message": (f"Type action is not supported for element '{element_ref}'"),
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )

        # --- Safety metadata ---
        element = NormalizedElement(
            ref=element_ref,
            backend_id=str(handle),
            role="element",
            states=ElementStates(enabled=True),
        )
        assessment = classify(element, "type_text")

        return json.dumps(
            {
                "success": True,
                "ref": element_ref,
                "role": "element",
                "risk": assessment.risk_level.lower(),
                "target_summary": "element type_text",
            }
        )


# ---------------------------------------------------------------------------
# Slowly mode helpers (web backend only)
# ---------------------------------------------------------------------------


def _type_text_slowly_web(
    router: BackendRouter,
    ref_store: "ElementRefStore",
    handle: object,
    element_ref: str,
    text: str,
) -> str | None:
    """Attempt to type text character-by-character via CDP Input domain.

    Returns:
        A JSON response string if the element is a web element, or ``None``
        if the element is native (caller should fall through to normal path).
    """
    inner, backend_id = _untag(handle)
    if backend_id != "web":
        return None

    web = router.web
    if web is None:
        return None

    # We need to find the CDP target/window for this element.
    # The element handle may contain AX node info with backend_dom_node_id.
    # We use the element's associated window to create a session.
    ax_node = inner
    if isinstance(inner, AXNode):
        ax_node = inner
    else:
        # Not an AX node — can't do slow typing
        return None

    # Find the window ref that contains this element.
    # Walk through ref_store to find a w-prefixed ref with a CDPTarget.
    target = _find_target_for_element(router, ref_store)
    if target is None:
        return None

    try:
        session = web._get_or_create_session(target.id)
    except Exception:
        return None

    try:
        from guidewire.cdp.domains.dom import DOMDomain
        from guidewire.cdp.domains.input import InputDomain

        dom = DOMDomain(session)
        input_domain = InputDomain(session)

        # Focus the element first via DOM
        if ax_node.backend_dom_node_id:
            try:
                node = dom.describe_node(
                    backend_node_id=ax_node.backend_dom_node_id,
                    depth=0,
                )
                dom.focus(node_id=node.node_id)
            except Exception:
                pass

        # Type each character individually
        for char in text:
            _dispatch_char(input_domain, char)
            time.sleep(_SLOWLY_CHAR_DELAY)

    except Exception as exc:
        return json.dumps(
            {
                "error": "web_type_slowly_error",
                "message": f"Slow typing failed: {exc}",
                "ref": element_ref,
                "hints": hints_for("web_type_slowly_error"),
            }
        )

    # --- Safety metadata ---
    element = NormalizedElement(
        ref=element_ref,
        backend_id=str(handle),
        role="element",
        states=ElementStates(enabled=True),
    )
    assessment = classify(element, "type_text")

    return json.dumps(
        {
            "success": True,
            "ref": element_ref,
            "role": "element",
            "risk": assessment.risk_level.lower(),
            "target_summary": "element type_text (slowly)",
            "slowly": True,
        }
    )


def _find_target_for_element(
    router: BackendRouter,
    ref_store: "ElementRefStore",
) -> CDPTarget | None:
    """Find a CDP target from the ref store for a web element.

    Walks the ref store looking for a w-prefixed ref that contains a
    CDPTarget.
    """
    web = router.web
    if web is None:
        return None

    # Get all windows from the web backend
    try:
        windows = web.list_windows()
        for w in windows:
            if isinstance(w, CDPTarget):
                return w
    except Exception:
        pass

    return None


def _dispatch_char(
    input_domain: "InputDomain",
    char: str,
) -> None:
    """Dispatch a single character key event via CDP Input domain.

    Sends keyDown, char, and keyUp events for a single character,
    mimicking real keyboard input.

    Args:
        input_domain: CDP Input domain instance.
        char: Single character to type.
    """
    # keyDown event
    input_domain.dispatch_key_event(
        "keyDown",
        key=char,
        code=f"Key{char.upper()}" if char.isalpha() else "",
        text=char,
    )
    # char event
    input_domain.dispatch_key_event(
        "char",
        key=char,
        text=char,
    )
    # keyUp event
    input_domain.dispatch_key_event(
        "keyUp",
        key=char,
        code=f"Key{char.upper()}" if char.isalpha() else "",
    )

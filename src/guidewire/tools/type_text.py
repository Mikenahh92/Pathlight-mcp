"""desktop.type_text — type text into a desktop accessibility element.

Resolves an ``e``-prefixed short reference to a native handle via the
:class:`~guidewire.refs.ElementRefStore`, validates it through the backend,
applies safety classification, and invokes the OS accessibility type action
via :meth:`~guidewire.backends.base.DesktopBackend.perform_action` with
``DesktopAction.TYPE`` (PRD R7).
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.backends.types import DesktopAction
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
    from guidewire.refs import ElementRefStore


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
    def type_text(element_ref: str, text: str) -> str:
        """Type text into a desktop element.

        Args:
            element_ref: Short reference handle for the target element
                (e.g. ``"e1"``).
            text: The text to type into the element.

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

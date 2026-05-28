"""desktop.get_text — retrieve the text value of a desktop accessibility element.

Resolves an ``e``-prefixed short reference to a native handle via the
:class:`~pathlight_mcp.refs.ElementRefStore`, validates it through the backend,
applies privacy redaction for password fields, runs safety classification,
and invokes ``backend.perform_action(GET_TEXT)`` to retrieve the element's
text content (PRD R9, R12).
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends.types import DesktopAction
from pathlight_mcp.errors import (
    ActionNotSupportedError,
    ElementNotFoundError,
    StaleElementReferenceError,
)
from pathlight_mcp.hints import hints_for
from pathlight_mcp.models import ElementStates, NormalizedElement
from pathlight_mcp.privacy import is_password_field
from pathlight_mcp.safety import classify

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.get_text tool on *mcp*.

    When *backend* is provided the tool resolves *element_ref* through
    *ref_store*, validates the handle, applies privacy redaction, runs
    safety classification, and delegates to ``backend.perform_action(GET_TEXT)``.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.get_text")
    def get_text(element_ref: str) -> str:
        """Get the text value of a desktop element.

        Args:
            element_ref: Short reference handle for the target element
                (e.g. ``"e1"``).

        Returns:
            A JSON object with ``success``, ``ref``, ``text``, ``role``,
            ``name`` (when available), ``risk``, and ``target_summary`` on
            success, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return f"Text for {element_ref}"

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

        # --- Retrieve text with structured error handling ---
        try:
            text = backend.perform_action(handle, DesktopAction.GET_TEXT)
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
                    "message": (f"Get text action is not supported for element '{element_ref}'"),
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )

        text = text or ""

        # --- Retrieve element metadata for privacy/safety ---
        info = backend.get_element_info(handle)
        element_role = info["role"]
        element_name = info.get("name")
        backend_states = info.get("states", {})

        element = NormalizedElement(
            ref=element_ref,
            backend_id=str(handle),
            role=element_role,
            name=element_name,
            states=ElementStates(
                enabled=backend_states.get("enabled", True),
                is_password=backend_states.get("is_password"),
            ),
        )

        # --- Privacy redaction ---
        if is_password_field(element):
            text = "[REDACTED]"

        # --- Safety metadata ---
        assessment = classify(element, "get_text")

        response: dict[str, object] = {
            "success": True,
            "ref": element_ref,
            "text": text,
            "role": element_role,
            "risk": assessment.risk_level.lower(),
            "target_summary": "element text retrieval",
        }
        if element_name is not None:
            response["name"] = element_name

        return json.dumps(response)

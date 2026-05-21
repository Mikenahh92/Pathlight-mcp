"""desktop.clipboard_write — write text to the system clipboard.

Delegates to ``backend.clipboard_write(text)`` via the
:class:`~guidewire.backends.base.DesktopBackend` contract and returns a
structured success/error response with safety metadata (PRD R8, §6).

Safety classification: SENSITIVE — requires user confirmation.
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.errors import BackendUnavailableError
from guidewire.hints import hints_for
from guidewire.safety import classify_system_action

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend

# Maximum clipboard text length (characters) — prevents absurdly large writes.
_MAX_CLIPBOARD_LENGTH = 1_000_000

# -- Tool registration --------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    **kwargs: object,
) -> None:
    """Register the desktop.clipboard_write tool on *mcp*.

    When *backend* is provided the tool delegates to
    ``backend.clipboard_write(text)`` and returns a structured JSON
    response with safety metadata.  Without a backend it returns a
    static stub response.
    """

    @mcp.tool(name="desktop.clipboard_write")
    def clipboard_write(text: str) -> str:
        """Write text to the system clipboard.

        Args:
            text: The text to write to the clipboard.  Must be non-empty
                and at most 1 000 000 characters.

        Returns:
            A JSON object with ``success``, ``chars_written``, ``risk``,
            and ``confirmation_required`` on success, or a structured
            error payload on failure.
        """
        if backend is None:
            return f'Clipboard set to: "{text}"'

        # --- Input validation ---
        if not isinstance(text, str):
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "text must be a string",
                    "hints": [],
                }
            )

        if not text or not text.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "text must not be empty or whitespace-only",
                    "hints": [],
                }
            )

        if len(text) > _MAX_CLIPBOARD_LENGTH:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        f"text exceeds maximum length of {_MAX_CLIPBOARD_LENGTH} characters"
                    ),
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("clipboard_write", target=text[:50] if text else "")

        # --- Delegate to backend with structured error handling ---
        try:
            backend.clipboard_write(text)
        except BackendUnavailableError as exc:
            return json.dumps(
                {
                    "error": "backend_unavailable",
                    "message": "Accessibility backend is not available",
                    "hints": exc.hints,
                }
            )
        except Exception as exc:
            return json.dumps(
                {
                    "error": "backend_error",
                    "message": str(exc),
                    "hints": [],
                }
            )

        return json.dumps(
            {
                "success": True,
                "chars_written": len(text),
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
            }
        )

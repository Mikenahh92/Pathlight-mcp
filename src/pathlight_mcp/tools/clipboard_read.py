"""desktop.clipboard_read — read text content from the system clipboard.

Delegates to ``backend.clipboard_read()`` via the
:class:`~pathlight_mcp.backends.base.DesktopBackend` contract, applies privacy
redaction via :func:`~pathlight_mcp.privacy.redact_clipboard_text`, and returns a
structured success/error response with safety metadata (PRD R8, §6).

Safety classification: INTERACTION — no user confirmation required.
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.errors import BackendUnavailableError
from pathlight_mcp.privacy import redact_clipboard_text
from pathlight_mcp.safety import classify_system_action

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend


# -- Tool registration --------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    **kwargs: object,
) -> None:
    """Register the desktop.clipboard_read tool on *mcp*.

    When *backend* is provided the tool delegates to
    ``backend.clipboard_read()`` and returns a structured JSON
    response with safety metadata and privacy redaction.  Without a
    backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.clipboard_read")
    def clipboard_read() -> str:
        """Read text content from the system clipboard.

        Returns:
            A JSON object with ``success``, ``text``, ``risk``, and
            ``confirmation_required`` on success, or a structured error
            payload on failure.
        """
        if backend is None:
            return "Clipboard content: [stub]"

        # --- Safety metadata ---
        assessment = classify_system_action("clipboard_read")

        # --- Delegate to backend with structured error handling ---
        try:
            raw_text = backend.clipboard_read()
        except BackendUnavailableError as exc:
            return json.dumps(
                {
                    "error": "backend_unavailable",
                    "message": "Accessibility backend is not available",
                    "hints": exc.hints,
                }
            )

        # --- Privacy redaction ---
        text = redact_clipboard_text(raw_text)

        return json.dumps(
            {
                "success": True,
                "text": text,
                "length": len(text),
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
            }
        )

"""desktop.press_key — send keyboard input via the backend.

Parses a key combo string (e.g. ``"Ctrl+S"``, ``"Enter"``, ``"Alt+Tab"``),
delegates to ``backend.perform_action(PRESS_KEY, keys=...)`` via the
:class:`~guidewire.backends.base.DesktopBackend` contract, and returns a
structured success/error response with safety metadata (PRD R8, §6.8).
"""

import json
import re
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.backends.types import DesktopAction
from guidewire.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
)
from guidewire.hints import hints_for
from guidewire.models import ElementStates, NormalizedElement
from guidewire.safety import classify

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend


# -- Key-combo normalisation --------------------------------------------------


_MODIFIER_MAP: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "super": "super",
    "cmd": "super",
    "command": "super",
    "win": "super",
    "meta": "super",
}

_KEY_MAP: dict[str, str] = {
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "escape": "escape",
    "esc": "escape",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "pageup": "page_up",
    "page_up": "page_up",
    "pagedown": "page_down",
    "page_down": "page_down",
    "arrowup": "up",
    "up": "up",
    "arrowdown": "down",
    "down": "down",
    "arrowleft": "left",
    "left": "left",
    "arrowright": "right",
    "right": "right",
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
}

_COMBO_RE = re.compile(r"^\s*([a-zA-Z0-9_\s+]+)\s*$")


def _normalise_key_combo(keys: str) -> str:
    """Normalise a key combo string into a canonical ``modifier+key`` form.

    Examples::

        "Ctrl+S"       -> "ctrl+s"
        "Alt+Tab"      -> "alt+tab"
        "Command+Q"    -> "super+q"
        "Enter"        -> "enter"
        "  shift+A  "  -> "shift+a"
    """
    match = _COMBO_RE.match(keys)
    if not match:
        return keys.strip().lower()

    parts = [p.strip().lower() for p in match.group(1).split("+")]
    normalised: list[str] = []

    for part in parts:
        if part in _MODIFIER_MAP:
            normalised.append(_MODIFIER_MAP[part])
        elif part in _KEY_MAP:
            normalised.append(_KEY_MAP[part])
        else:
            # Single printable character or unknown key — pass through
            normalised.append(part)

    return "+".join(normalised)


# -- Tool registration --------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    **kwargs: object,
) -> None:
    """Register the desktop.press_key tool on *mcp*.

    When *backend* is provided the tool parses the key combo string,
    delegates to ``backend.perform_action(PRESS_KEY, keys=...)``, and
    returns a structured JSON response with safety metadata.  Without a
    backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.press_key")
    def press_key(keys: str) -> str:
        """Press a keyboard key or key combination.

        Args:
            keys: The key or key combination to press (e.g. ``"Enter"``,
                ``"Tab"``, ``"Ctrl+S"``, ``"Alt+Tab"``).

        Returns:
            A JSON object with ``success``, ``keys``, ``risk``, and
            ``target_summary`` on success, or a structured error payload
            on failure.
        """
        if backend is None:
            return f'Pressed "{keys}"'

        # --- Input validation ---
        if not keys or not keys.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "keys must be a non-empty string",
                    "keys": keys,
                    "hints": [],
                }
            )

        normalised = _normalise_key_combo(keys)

        # --- Resolve target window ---
        windows = backend.list_windows()
        if not windows:
            return json.dumps(
                {
                    "error": "backend_unavailable",
                    "message": "No windows available for key press",
                    "keys": keys,
                    "hints": hints_for("backend_unavailable"),
                }
            )
        target = windows[0]

        # --- Delegate to backend with structured error handling ---
        try:
            backend.perform_action(
                target,
                DesktopAction.PRESS_KEY,
                keys=normalised,
            )
        except BackendUnavailableError as exc:
            return json.dumps(
                {
                    "error": "backend_unavailable",
                    "message": "Accessibility backend is not available",
                    "keys": keys,
                    "hints": exc.hints,
                }
            )
        except ActionNotSupportedError as exc:
            return json.dumps(
                {
                    "error": "action_not_supported",
                    "message": f"Press key action is not supported for '{keys}'",
                    "keys": keys,
                    "hints": exc.hints,
                }
            )

        # --- Safety metadata ---
        element = NormalizedElement(
            ref="",
            backend_id="",
            role="element",
            states=ElementStates(enabled=True),
        )
        assessment = classify(element, "press_key")

        return json.dumps(
            {
                "success": True,
                "keys": normalised,
                "risk": assessment.risk_level.lower(),
                "target_summary": f"key press: {normalised}",
            }
        )

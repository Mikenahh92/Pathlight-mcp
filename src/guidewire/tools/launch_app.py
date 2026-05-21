"""desktop.launch_app — launch a desktop application by name or path.

Launches an application using ``subprocess.Popen`` (stdlib, zero new deps),
then polls ``backend.list_windows()`` until a matching window appears or the
readiness timeout expires.  Returns a ``w``-prefixed window reference so the
caller can immediately interact with the launched application.

Safety classification: SENSITIVE — ``app_launch`` requires user confirmation
(``SYSTEM_ACTION_RISK_MAP`` in :mod:`guidewire.safety`).

Tool-layer only — no ABC changes.  Relies on the existing
:meth:`~guidewire.backends.base.DesktopBackend.list_windows` and
:meth:`~guidewire.backends.base.DesktopBackend.get_window_info` methods for
readiness detection.
"""

import json
import logging
import subprocess
import time
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.safety import classify_system_action

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Default readiness-poll timeout in seconds.
_DEFAULT_TIMEOUT = 10.0

# Interval between readiness polls in seconds.
_POLL_INTERVAL = 0.5


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.launch_app tool on *mcp*.

    When *backend* is provided the tool launches the application, polls for a
    matching window via the backend, stores a ``w``-prefixed reference, and
    returns a structured JSON response.  Without a backend it returns a static
    stub response.
    """

    @mcp.tool(name="desktop.launch_app")
    def launch_app(
        app: str,
        args: list[str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> str:
        """Launch a desktop application by name or path.

        Args:
            app: Application name (e.g. ``"notepad"``) or filesystem path
                (e.g. ``"C:\\Program Files\\App\\app.exe"``).
            args: Optional command-line arguments to pass to the application.
            timeout: Maximum seconds to wait for the application window to
                appear (default 10).  Set to 0 to skip readiness detection.

        Returns:
            A JSON object with ``success``, ``ref``, ``title``, ``app_name``,
            ``pid``, ``risk``, ``confirmation_required``, and
            ``target_summary`` on success, or a structured error payload on
            failure.
        """
        if backend is None or ref_store is None:
            arg_str = " ".join(args) if args else ""
            return f"Launched {app} {arg_str}".strip()

        # --- Input validation ---
        if not app or not app.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "app must be a non-empty string",
                }
            )

        if timeout < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "timeout must be non-negative",
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("app_launch", target=app)

        # --- Launch application ---
        cmd = [app] + (args or [])
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return json.dumps(
                {
                    "error": "app_not_found",
                    "message": f"Application not found: '{app}'",
                }
            )
        except OSError as exc:
            return json.dumps(
                {
                    "error": "launch_error",
                    "message": f"Failed to launch '{app}': {exc}",
                }
            )

        pid = proc.pid

        # --- Readiness detection ---
        # Snapshot windows before launch so we can detect the new one.
        pre_handles = set(backend.list_windows())
        # Collect app_name candidates from the app path for matching.
        # e.g. "notepad" or "notepad.exe" → "notepad"
        app_stem = app.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        app_stem_lower = app_stem.lower()
        # Strip common extensions for matching
        for ext in (".exe", ".bat", ".cmd", ".sh", ".app"):
            if app_stem_lower.endswith(ext):
                app_stem_lower = app_stem_lower[: -len(ext)]
                break

        window_ref: str | None = None
        title: str | None = None
        matched_app_name: str | None = None

        if timeout > 0:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                time.sleep(_POLL_INTERVAL)
                try:
                    post_handles = backend.list_windows()
                except Exception:
                    continue

                new_handles = [h for h in post_handles if h not in pre_handles]
                for handle in new_handles:
                    try:
                        info = backend.get_window_info(handle)
                    except Exception:
                        continue

                    app_name = (info.get("app_name") or "").lower()
                    win_title = info.get("title", "")

                    # Match by app_name containing the app stem, or title match
                    if (
                        app_stem_lower in app_name
                        or app_stem_lower in win_title.lower()
                        or app.lower() in app_name
                    ):
                        ref = ref_store.store(handle, prefix="w")
                        window_ref = ref
                        title = win_title
                        matched_app_name = info.get("app_name")
                        break

                if window_ref is not None:
                    break

        # --- Build response ---
        result: dict = {
            "success": True,
            "pid": pid,
            "risk": assessment.risk_level.lower(),
            "confirmation_required": assessment.confirmation_required,
            "target_summary": f"launch {app}",
        }

        if window_ref is not None:
            result["ref"] = window_ref
            result["title"] = title
            result["app_name"] = matched_app_name
        elif timeout > 0:
            # Timed out waiting for window — still report success with pid
            # but note the readiness timeout
            result["ref"] = None
            result["warning"] = (
                f"Application launched (pid={pid}) but no matching window "
                f"detected within {timeout}s"
            )
            logger.info(
                "launch_app: pid=%d — readiness poll timed out after %.1fs",
                pid,
                timeout,
            )

        return json.dumps(result)

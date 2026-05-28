"""desktop.launch_app — launch a desktop application by name or path.

Launches an application using ``subprocess.Popen`` (stdlib, zero new deps),
then polls ``backend.list_windows()`` until a matching window appears or the
readiness timeout expires.  Returns a ``w``-prefixed window reference so the
caller can immediately interact with the launched application.

Linux-specific fixes (GW-068):
- Propagates ``DISPLAY`` environment variable to child processes so X11/Wayland
  applications can connect to the display server.
- Detects snap-wrapped binaries (e.g. ``/snap/bin/firefox``) and resolves them
  to their actual binary path so the launched process has a usable ``PATH``.
- Performs a post-launch liveness check to detect immediate crashes and report
  them as errors instead of false successes.

Safety classification: SENSITIVE — ``app_launch`` requires user confirmation
(``SYSTEM_ACTION_RISK_MAP`` in :mod:`pathlight_mcp.safety`).

Tool-layer only — no ABC changes.  Relies on the existing
:meth:`~pathlight_mcp.backends.base.DesktopBackend.list_windows` and
:meth:`~pathlight_mcp.backends.base.DesktopBackend.get_window_info` methods for
readiness detection.
"""

import json
import logging
import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.hints import hints_for
from pathlight_mcp.safety import classify_system_action

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Default readiness-poll timeout in seconds.
_DEFAULT_TIMEOUT = 10.0

# Interval between readiness polls in seconds.
_POLL_INTERVAL = 0.5

# Brief pause after Popen to detect immediate crashes (seconds).
_LIVENESS_CHECK_DELAY = 0.5


# ---------------------------------------------------------------------------
# Linux helpers
# ---------------------------------------------------------------------------


def _build_env() -> dict[str, str] | None:
    """Build a child-process environment with ``DISPLAY`` propagated.

    On Linux, ``subprocess.Popen`` inherits the parent environment by default,
    but if the MCP server was launched without ``DISPLAY`` (e.g. by a systemd
    unit), the child will also lack it.  This helper explicitly propagates
    ``DISPLAY`` and ``WAYLAND_DISPLAY`` from the *current* process environment.

    Returns:
        A copy of ``os.environ`` with display variables forced in, or ``None``
        on non-Linux platforms (so the default inheritance behaviour is used).
    """
    if sys.platform != "linux":
        return None

    env = os.environ.copy()
    # Ensure DISPLAY is present — default to :0 if not set anywhere.
    if "DISPLAY" not in env or not env["DISPLAY"]:
        env["DISPLAY"] = ":0"
    return env


def _resolve_snap_binary(app: str) -> str:
    """Resolve a snap-wrapped binary to its actual executable path.

    On Ubuntu and other snap-enabled distributions, ``/snap/bin/<name>`` is a
    wrapper script that invokes the snap runtime.  Launching these directly
    via ``Popen`` can fail because the wrapper may not propagate the display
    environment correctly.  This function:

    1. Detects if *app* looks like a snap wrapper (lives under ``/snap/bin/``).
    2. Reads the wrapper script to extract the actual binary path.
    3. Falls back to *app* unchanged if resolution fails.

    Also handles bare names like ``"firefox"`` when a snap wrapper exists at
    ``/snap/bin/firefox``.

    Args:
        app: The application name or path as provided by the caller.

    Returns:
        The resolved binary path, or *app* unchanged if not a snap binary.
    """
    if sys.platform != "linux":
        return app

    snap_bin_dir = "/snap/bin/"

    # Check if the app is already a /snap/bin/ path
    if app.startswith(snap_bin_dir):
        snap_name = app[len(snap_bin_dir) :]
        return _try_resolve_snap(snap_name, app)

    # Check if /snap/bin/<app> exists for a bare name
    snap_path = os.path.join(snap_bin_dir, app)
    if os.path.isfile(snap_path) and os.access(snap_path, os.X_OK):
        return _try_resolve_snap(app, snap_path)

    return app


def _try_resolve_snap(snap_name: str, fallback: str) -> str:
    """Attempt to resolve the real binary for a snap package.

    Tries ``snap run --command=<snap_name>`` via ``which`` to find the actual
    binary, then falls back to reading the wrapper script at
    ``/snap/bin/<snap_name>``.

    Args:
        snap_name: The snap package name (e.g. ``"firefox"``).
        fallback: The path to return if resolution fails.

    Returns:
        The resolved binary path, or *fallback*.
    """
    # Strategy 1: use "which" to locate the actual binary
    try:
        result = subprocess.run(
            ["which", snap_name],
            capture_output=True,
            text=True,
            timeout=2,
        )
        resolved = result.stdout.strip()
        if resolved and not resolved.startswith("/snap/bin/"):
            logger.debug("Resolved snap '%s' to '%s'", snap_name, resolved)
            return resolved
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Strategy 2: read the snap wrapper script to extract the actual command
    wrapper_path = os.path.join("/snap/bin", snap_name)
    try:
        with open(wrapper_path) as f:
            content = f.read()
        # Snap wrappers typically contain: exec <path> "$@"
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("exec "):
                parts = stripped.split()
                if len(parts) >= 2:
                    candidate = parts[1]
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        logger.debug(
                            "Resolved snap '%s' via wrapper to '%s'",
                            snap_name,
                            candidate,
                        )
                        return candidate
    except (OSError, ValueError):
        pass

    logger.debug("Could not resolve snap binary for '%s', using fallback '%s'", snap_name, fallback)
    return fallback


def _is_electron_binary(binary_path: str) -> bool:
    """Detect whether a binary is an Electron/Chromium application.

    Electron and Chromium apps require ``--no-sandbox`` when running under
    certain Linux configurations (e.g. snap confinement, containers, or CI).
    This function detects such binaries by checking for known filenames and
    sibling files (``chrome-sandbox``, ``electron``) next to the binary.

    Args:
        binary_path: The resolved binary path to check.

    Returns:
        ``True`` if the binary appears to be an Electron/Chromium app.
    """
    name = os.path.basename(binary_path).lower()

    # Direct binary name matches for common Electron/Chromium apps
    electron_names = {
        "electron",
        "electron4",
        "electron5",
        "electron6",
        "electron7",
        "electron8",
        "electron9",
        "electron10",
        "electron11",
        "electron12",
        "electron13",
        "electron14",
        "electron15",
        "electron16",
        "electron17",
        "electron18",
        "electron19",
        "electron20",
        "electron21",
        "electron22",
        "electron23",
        "electron24",
        "electron25",
        "electron26",
        "electron27",
        "electron28",
        "electron29",
        "electron30",
        "chrome",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "microsoft-edge",
        "brave-browser",
        "code",
        "codium",
        "slack",
        "discord",
        "signal-desktop",
        "whatsapp",
        "teams",
        "spotify",
    }
    # Match by exact name or name starting with "electron"
    if name in electron_names or name.startswith("electron"):
        return True

    # Check for chrome-sandbox sibling file — a hallmark of Chromium-based apps
    parent_dir = os.path.dirname(binary_path)
    sandbox_path = os.path.join(parent_dir, "chrome-sandbox")
    return bool(os.path.isfile(sandbox_path))


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


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

        # --- Resolve snap binaries (Linux) ---
        resolved_app = _resolve_snap_binary(app)

        # --- Build environment with DISPLAY propagated (Linux) ---
        env = _build_env()

        # --- Build command, injecting --no-sandbox for Electron apps ---
        cmd = [resolved_app] + (args or [])
        if sys.platform == "linux" and _is_electron_binary(resolved_app):
            if "--no-sandbox" not in cmd:
                cmd.append("--no-sandbox")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except FileNotFoundError:
            return json.dumps(
                {
                    "error": "app_not_found",
                    "message": f"Application not found: '{app}'",
                    "hints": hints_for("app_not_found"),
                }
            )
        except OSError as exc:
            return json.dumps(
                {
                    "error": "launch_error",
                    "message": f"Failed to launch '{app}': {exc}",
                    "hints": hints_for("launch_error"),
                }
            )

        pid = proc.pid

        # --- Post-launch liveness check ---
        # Brief pause to catch immediate crashes (e.g. missing shared libs,
        # display connection failures) before reporting success.
        time.sleep(_LIVENESS_CHECK_DELAY)
        exit_code = proc.poll()
        if exit_code is not None:
            return json.dumps(
                {
                    "error": "launch_error",
                    "message": (f"Application '{app}' exited immediately with code {exit_code}"),
                    "hints": hints_for("launch_error"),
                }
            )

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

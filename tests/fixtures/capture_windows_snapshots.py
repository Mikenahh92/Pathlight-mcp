"""Capture accessibility tree snapshots from Windows applications.

This script launches target applications (Notepad, Calculator, Settings),
captures their UI accessibility trees via WindowsBackend.snapshot(), and
saves the raw pre-normalization output as JSON golden fixtures.

Usage:
    python -m tests.fixtures.capture_windows_snapshots

Requirements:
    - Windows 10/11 with UIAutomation enabled
    - pathlight_mcp installed in development mode
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "windows"

# UIA ControlType IDs for the root window elements
_WINDOW_CONTROL_TYPE = 50032

# Target applications to capture
TARGETS: list[dict[str, Any]] = [
    {
        "name": "Notepad",
        "exe": "notepad.exe",
        "filename": "notepad_snapshot.json",
        "min_elements": 10,
    },
    {
        "name": "Calculator",
        "exe": "calc.exe",
        "filename": "calculator_snapshot.json",
        "min_elements": 20,
    },
    {
        "name": "Windows Settings",
        "exe": "ms-settings:",
        "filename": "settings_snapshot.json",
        "min_elements": 20,
    },
    {
        "name": "File Explorer",
        "exe": "explorer.exe",
        "filename": "file_explorer_snapshot.json",
        "min_elements": 20,
    },
]


def _build_metadata(app_name: str) -> dict[str, Any]:
    """Build the _metadata envelope for a snapshot fixture."""
    return {
        "captured_at": datetime.now(tz=datetime.UTC).isoformat(),
        "os_version": _get_os_version(),
        "app_name": app_name,
        "pathlight_mcp_version": _get_pathlight_mcp_version(),
        "max_depth": 4,
        "max_nodes": 500,
    }


def _get_os_version() -> str:
    """Return a human-readable OS version string."""
    import platform

    return f"Windows {platform.version()}"


def _get_pathlight_mcp_version() -> str:
    """Return the installed pathlight_mcp package version."""
    try:
        from importlib.metadata import version

        return version("pathlight-mcp")
    except Exception:
        return "unknown"


def _launch_app(exe: str) -> subprocess.Popen[str]:
    """Launch a target application and return the process handle."""
    try:
        proc = subprocess.Popen(
            [exe] if ":" not in exe else ["cmd", "/c", "start", exe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(f"  ERROR: Cannot launch '{exe}' — application not found.")
        sys.exit(1)
    return proc


def _capture_snapshot(
    app_name: str,
    proc: subprocess.Popen[str],
) -> dict[str, Any] | None:
    """Capture the accessibility snapshot using WindowsBackend.

    Returns the raw snapshot dict, or None on failure.
    """
    try:
        from pathlight_mcp.backends import WindowsBackend

        backend = WindowsBackend()

        # Give the app time to initialize its UI
        time.sleep(2)

        windows = backend.list_windows()
        target = None
        for win in windows:
            info = backend.get_window_info(win)
            if info and app_name.lower() in (info.get("name") or "").lower():
                target = win
                break

        if target is None:
            print(f"  WARNING: Could not find window for '{app_name}'.")
            return None

        snapshot = backend.snapshot(target, max_depth=4, max_nodes=500)
        return snapshot
    except Exception as exc:
        print(f"  ERROR: Failed to capture snapshot: {exc}")
        return None
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _save_fixture(
    filename: str,
    app_name: str,
    snapshot: dict[str, Any],
) -> Path:
    """Save a snapshot with _metadata envelope to the fixtures directory."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    fixture = {
        "_metadata": _build_metadata(app_name),
        "snapshot": snapshot,
    }

    path = FIXTURES_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=2, ensure_ascii=False)

    return path


def main() -> None:
    """Run the capture pipeline for all target applications."""
    print("Capturing Windows accessibility snapshots...")
    print(f"Output directory: {FIXTURES_DIR}\n")

    for target in TARGETS:
        print(f"[*] Capturing {target['name']}...")
        proc = _launch_app(target["exe"])
        snapshot = _capture_snapshot(target["name"], proc)

        if snapshot is None:
            print(f"  Skipping {target['name']} — capture failed.\n")
            continue

        path = _save_fixture(target["filename"], target["name"], snapshot)
        print(f"  Saved to {path}")
        print("  Done.\n")

    print("Capture complete.")


if __name__ == "__main__":
    main()

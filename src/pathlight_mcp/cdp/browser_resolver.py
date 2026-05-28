"""BrowserResolver — per-platform browser discovery and auto-launch (GW-114).

Discovers installed Chromium-based browsers on the current platform and
auto-launches them with the ``--remote-debugging-port`` flag so the
CDP transport can connect without any manual browser setup.

Discovery order (tried in sequence, first match wins):
    - **Windows**: Edge → Chrome → Brave → Chromium
    - **Linux**: Chromium → Chrome → Brave

Override:
    ``web_connect(browser="chrome")`` skips discovery and uses the named
    browser directly.

The resolver caches the discovered path so subsequent calls avoid re-scanning
the filesystem.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

__all__ = [
    "BROWSER_NAMES",
    "BrowserResolver",
    "resolve_browser",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BROWSER_NAMES = ("edge", "chrome", "brave", "chromium")
"""Valid browser name strings accepted by the ``browser`` parameter."""

# Default CDP port used when auto-launching.
_DEFAULT_CDP_PORT = 9222

# Maximum time to wait for a launched browser to become ready (seconds).
_LAUNCH_READY_TIMEOUT = 8.0

# Polling interval when waiting for browser readiness (seconds).
_LAUNCH_POLL_INTERVAL = 0.25

# Windows install paths (ordered by discovery priority).
_WINDOWS_PATHS: dict[str, list[str]] = {
    "edge": [
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"
        ),
    ],
    "chrome": [
        os.path.expandvars(
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe"
        ),
    ],
    "brave": [
        os.path.expandvars(
            r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"
        ),
    ],
    "chromium": [
        os.path.expandvars(
            r"%LocalAppData%\Chromium\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles%\Chromium\Application\chrome.exe"
        ),
    ],
}

# Linux binary names (ordered by discovery priority).
_LINUX_BINARIES: dict[str, list[str]] = {
    "chromium": ["chromium-browser", "chromium"],
    "chrome": ["google-chrome-stable", "google-chrome", "chrome"],
    "brave": ["brave-browser", "brave"],
}

# Default discovery order per platform.
_DEFAULT_ORDER_WINDOWS = ("edge", "chrome", "brave", "chromium")
_DEFAULT_ORDER_LINUX = ("chromium", "chrome", "brave")


# ---------------------------------------------------------------------------
# BrowserResolver
# ---------------------------------------------------------------------------


class BrowserResolver:
    """Per-platform browser discovery and auto-launch.

    Discovers installed browsers via platform-specific paths / ``which``
    lookups, and auto-launches them with ``--remote-debugging-port`` so
    CDP can connect.

    Args:
        port: CDP debug port for the launched browser.
        auto_launch: Whether to auto-launch when no browser is found
            (default ``True``).  When ``False``, only discovery is
            performed.

    Attributes:
        spawned_process: The :class:`subprocess.Popen` handle for a
            browser launched by this resolver, or ``None``.
    """

    def __init__(
        self,
        port: int = _DEFAULT_CDP_PORT,
        *,
        auto_launch: bool = True,
    ) -> None:
        self._port = port
        self._auto_launch = auto_launch
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()
        self.spawned_process: subprocess.Popen[Any] | None = None

    # -- Public API -----------------------------------------------------------

    def resolve(self, browser: str | None = None) -> str | None:
        """Discover the browser executable path.

        When *browser* is provided, only that specific browser is looked
        up.  When ``None`` (default), the platform's default discovery
        order is used.

        Args:
            browser: Optional browser name (``"edge"``, ``"chrome"``,
                ``"brave"``, ``"chromium"``).  When provided, skips the
                default discovery order and resolves only the named
                browser.

        Returns:
            The absolute path to the browser executable, or ``None`` if
            no suitable browser is found.
        """
        if browser is not None:
            browser = browser.lower()
            self._validate_browser_name(browser)
            return self._find_browser(browser)

        for name in self._default_order():
            path = self._find_browser(name)
            if path is not None:
                return path

        return None

    def launch(
        self,
        browser: str | None = None,
        *,
        port: int | None = None,
        extra_args: list[str] | None = None,
    ) -> subprocess.Popen[Any]:
        """Launch a browser with ``--remote-debugging-port`` and return the process.

        Resolves the browser (via :meth:`resolve` if not specified),
        spawns it with the required CDP flags, and stores the process
        reference in :attr:`spawned_process`.

        Args:
            browser: Optional browser name override.
            port: Override the CDP port (defaults to ``self._port``).
            extra_args: Additional command-line arguments.

        Returns:
            The :class:`subprocess.Popen` handle.

        Raises:
            FileNotFoundError: If no browser executable is found.
            RuntimeError: If auto-launch is disabled.
        """
        if not self._auto_launch:
            raise RuntimeError("Auto-launch is disabled (auto_launch=False)")

        exe_path = self.resolve(browser)
        if exe_path is None:
            name = browser or "any browser"
            raise FileNotFoundError(
                f"Could not find a suitable browser executable for '{name}'"
            )

        cdp_port = port or self._port
        cmd = [
            exe_path,
            f"--remote-debugging-port={cdp_port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-client-side-phishing-detection",
            "--disable-default-apps",
            "--disable-hang-monitor",
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--disable-sync",
            "--metrics-recording-only",
        ]
        if extra_args:
            cmd.extend(extra_args)

        logger.info("Auto-launching browser: %s", " ".join(cmd))

        popen_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            popen_kwargs["creationflags"] = 0x00000200 | 0x08000000
            popen_kwargs["stdin"] = subprocess.DEVNULL
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
        else:
            popen_kwargs["stdin"] = subprocess.DEVNULL
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)
        self.spawned_process = proc
        logger.info("Browser process started (PID %d)", proc.pid)
        return proc

    def wait_for_ready(
        self,
        host: str = "localhost",
        port: int | None = None,
        *,
        timeout: float = _LAUNCH_READY_TIMEOUT,
    ) -> bool:
        """Wait until the auto-launched browser's CDP endpoint is responsive.

        Polls the ``/json/version`` HTTP endpoint until it responds or
        the timeout expires.

        Args:
            host: Hostname to check.
            port: Port to check (defaults to ``self._port``).
            timeout: Maximum time to wait in seconds.

        Returns:
            ``True`` if the browser is ready, ``False`` if timed out.
        """
        import json as _json
        from urllib.request import urlopen

        cdp_port = port or self._port
        url = f"http://{host}:{cdp_port}/json/version"
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                resp = urlopen(url, timeout=2)
                data = _json.loads(resp.read())
                if data.get("webSocketDebuggerUrl"):
                    return True
            except Exception:
                pass
            time.sleep(_LAUNCH_POLL_INTERVAL)

        return False

    def cleanup(self) -> None:
        """Terminate the spawned browser process (if any).

        Safe to call multiple times.  On Windows, terminates the process
        tree.  On Unix, sends SIGTERM.
        """
        proc = self.spawned_process
        if proc is None:
            return

        try:
            if proc.poll() is not None:
                # Already exited
                self.spawned_process = None
                return

            logger.info("Cleaning up spawned browser (PID %d)", proc.pid)

            if sys.platform == "win32":
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

        except Exception:
            logger.debug("Error cleaning up browser process", exc_info=True)
        finally:
            self.spawned_process = None

    # -- Discovery helpers ----------------------------------------------------

    @staticmethod
    def _default_order() -> tuple[str, ...]:
        """Return the default browser discovery order for this platform."""
        if sys.platform == "win32":
            return _DEFAULT_ORDER_WINDOWS
        return _DEFAULT_ORDER_LINUX

    def _find_browser(self, name: str) -> str | None:
        """Look up a single browser by name.

        Uses the cached path if available.

        Args:
            name: Browser name (e.g. ``"chrome"``).

        Returns:
            Absolute executable path, or ``None``.
        """
        with self._lock:
            cached = self._cache.get(name)
            if cached is not None:
                return cached

        if sys.platform == "win32":
            path = self._find_windows(name)
        else:
            path = self._find_linux(name)

        if path is not None:
            with self._lock:
                self._cache[name] = path

        return path

    @staticmethod
    def _find_windows(name: str) -> str | None:
        """Search for a browser on Windows via known install paths."""
        candidates = _WINDOWS_PATHS.get(name, [])
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None

    @staticmethod
    def _find_linux(name: str) -> str | None:
        """Search for a browser on Linux via ``shutil.which``."""
        binaries = _LINUX_BINARIES.get(name, [])
        for binary in binaries:
            found = shutil.which(binary)
            if found is not None:
                return found
        return None

    @staticmethod
    def _validate_browser_name(name: str) -> None:
        """Validate that *name* is a recognized browser identifier.

        Args:
            name: Browser name to validate.

        Raises:
            ValueError: If the name is not recognized.
        """
        if name not in BROWSER_NAMES:
            raise ValueError(
                f"Unknown browser '{name}'. "
                f"Available options: {', '.join(BROWSER_NAMES)}"
            )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_module_resolver: BrowserResolver | None = None
_module_lock = threading.Lock()


def resolve_browser(
    browser: str | None = None,
    *,
    port: int = _DEFAULT_CDP_PORT,
    auto_launch: bool = True,
) -> BrowserResolver:
    """Return a (possibly cached) :class:`BrowserResolver` instance.

    Creates a module-level singleton on first call so that browser
    discovery is only performed once per process.

    Args:
        browser: Optional browser name override.
        port: CDP debug port for auto-launch.
        auto_launch: Whether to enable auto-launch.

    Returns:
        A configured :class:`BrowserResolver`.
    """
    global _module_resolver
    with _module_lock:
        if _module_resolver is None:
            _module_resolver = BrowserResolver(port=port, auto_launch=auto_launch)
        return _module_resolver

"""Tests for BrowserResolver discovery, auto-launch, and desktop fallback (GW-114).

Covers:
- BrowserResolver discovers installed browsers via platform-specific paths/commands
- Default discovery order per platform:
  Windows (Edge→Chrome→Brave→Chromium), Linux (Chromium→Chrome→Brave)
- browser parameter override skips discovery and uses the named browser
- Invalid browser name returns a clear error listing available options
- Auto-launch spawns the resolved browser with CDP debug port
- auto_launch=False disables auto-launch and preserves current behavior
- Desktop automation fallback hint in error messages
- Spawned browser process tracking and cleanup
- CDP connection retry after auto-launch
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from pathlight_mcp.cdp.browser_resolver import (
    _DEFAULT_ORDER_LINUX,
    _DEFAULT_ORDER_WINDOWS,
    BROWSER_NAMES,
    BrowserResolver,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def resolver() -> BrowserResolver:
    """Return a fresh BrowserResolver for each test."""
    return BrowserResolver(port=9222, auto_launch=True)


@pytest.fixture()
def no_launch_resolver() -> BrowserResolver:
    """Return a BrowserResolver with auto_launch=False."""
    return BrowserResolver(port=9222, auto_launch=False)


# ---------------------------------------------------------------------------
# Browser name validation
# ---------------------------------------------------------------------------


class TestBrowserNameValidation:
    """Validate browser name constants and validation logic."""

    def test_browser_names_tuple(self) -> None:
        """BROWSER_NAMES contains the expected browsers."""
        assert "edge" in BROWSER_NAMES
        assert "chrome" in BROWSER_NAMES
        assert "brave" in BROWSER_NAMES
        assert "chromium" in BROWSER_NAMES

    def test_validate_known_names(self, resolver: BrowserResolver) -> None:
        """Validating known browser names does not raise."""
        for name in BROWSER_NAMES:
            resolver._validate_browser_name(name)

    def test_validate_unknown_name_raises(self, resolver: BrowserResolver) -> None:
        """Validating an unknown browser name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown browser 'firefox'"):
            resolver._validate_browser_name("firefox")

    def test_validate_case_insensitive_name_raises(self, resolver: BrowserResolver) -> None:
        """Validation is case-sensitive — uppercase names are rejected."""
        with pytest.raises(ValueError, match="Unknown browser"):
            resolver._validate_browser_name("Chrome")

    def test_error_lists_available_options(self, resolver: BrowserResolver) -> None:
        """The error message lists all available browser options."""
        with pytest.raises(ValueError, match="edge, chrome, brave, chromium") as exc_info:
            resolver._validate_browser_name("safari")
        assert "safari" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Discovery: default order
# ---------------------------------------------------------------------------


class TestDefaultDiscoveryOrder:
    """Default discovery order is platform-specific."""

    @patch("pathlight_mcp.cdp.browser_resolver.sys")
    def test_windows_order(self, mock_sys: MagicMock) -> None:
        """Windows uses Edge → Chrome → Brave → Chromium."""
        mock_sys.platform = "win32"
        assert BrowserResolver._default_order() == _DEFAULT_ORDER_WINDOWS

    @patch("pathlight_mcp.cdp.browser_resolver.sys")
    def test_linux_order(self, mock_sys: MagicMock) -> None:
        """Linux uses Chromium → Chrome → Brave."""
        mock_sys.platform = "linux"
        assert BrowserResolver._default_order() == _DEFAULT_ORDER_LINUX

    def test_windows_order_has_edge_first(self) -> None:
        """On Windows, Edge is the first browser tried."""
        if sys.platform == "win32":
            assert _DEFAULT_ORDER_WINDOWS[0] == "edge"

    def test_linux_order_has_chromium_first(self) -> None:
        """On Linux, Chromium is the first browser tried."""
        assert _DEFAULT_ORDER_LINUX[0] == "chromium"


# ---------------------------------------------------------------------------
# Discovery: Windows
# ---------------------------------------------------------------------------


class TestWindowsDiscovery:
    """BrowserResolver._find_windows uses known install paths."""

    def test_finds_edge(self, resolver: BrowserResolver) -> None:
        """Finds Edge when msedge.exe exists at a known path."""
        fake_path = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
        with (
            patch("os.path.isfile", return_value=True),
            patch("pathlight_mcp.cdp.browser_resolver._WINDOWS_PATHS", {"edge": [fake_path]}),
        ):
            result = resolver._find_windows("edge")
            assert result == fake_path

    def test_finds_chrome(self, resolver: BrowserResolver) -> None:
        """Finds Chrome when chrome.exe exists at a known path."""
        fake_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        with (
            patch("os.path.isfile", return_value=True),
            patch("pathlight_mcp.cdp.browser_resolver._WINDOWS_PATHS", {"chrome": [fake_path]}),
        ):
            result = resolver._find_windows("chrome")
            assert result == fake_path

    def test_returns_none_when_not_found(self, resolver: BrowserResolver) -> None:
        """Returns None when no browser executable is found."""
        with patch("os.path.isfile", return_value=False):
            result = resolver._find_windows("brave")
            assert result is None

    def test_tries_multiple_paths_in_order(self, resolver: BrowserResolver) -> None:
        """Tries multiple install paths in order and returns the first match."""
        paths = [
            r"C:\Program Files\Brave\brave.exe",
            r"C:\Program Files (x86)\Brave\brave.exe",
            r"C:\Users\test\AppData\Local\Brave\brave.exe",
        ]
        # Simulate: first two don't exist, third does
        isfile_results = {p: (i == 2) for i, p in enumerate(paths)}

        def isfile_side_effect(path: str) -> bool:
            return isfile_results.get(path, False)

        with (
            patch("os.path.isfile", side_effect=isfile_side_effect),
            patch("pathlight_mcp.cdp.browser_resolver._WINDOWS_PATHS", {"brave": paths}),
        ):
            result = resolver._find_windows("brave")
            assert result == paths[2]

    def test_unknown_browser_returns_none(self, resolver: BrowserResolver) -> None:
        """Returns None for a browser not in _WINDOWS_PATHS."""
        result = resolver._find_windows("firefox")
        assert result is None


# ---------------------------------------------------------------------------
# Discovery: Linux
# ---------------------------------------------------------------------------


class TestLinuxDiscovery:
    """BrowserResolver._find_linux uses shutil.which."""

    def test_finds_chromium(self, resolver: BrowserResolver) -> None:
        """Finds Chromium via which."""
        with patch("shutil.which", return_value="/usr/bin/chromium-browser"):
            result = resolver._find_linux("chromium")
            assert result == "/usr/bin/chromium-browser"

    def test_finds_chrome(self, resolver: BrowserResolver) -> None:
        """Finds Chrome via which."""
        with patch("shutil.which", return_value="/usr/bin/google-chrome"):
            result = resolver._find_linux("chrome")
            assert result == "/usr/bin/google-chrome"

    def test_returns_none_when_not_found(self, resolver: BrowserResolver) -> None:
        """Returns None when which finds nothing."""
        with patch("shutil.which", return_value=None):
            result = resolver._find_linux("brave")
            assert result is None

    def test_tries_multiple_binaries(self, resolver: BrowserResolver) -> None:
        """Tries multiple binary names and returns the first found."""

        def which_side_effect(name: str) -> str | None:
            if name == "google-chrome-stable":
                return "/usr/bin/google-chrome-stable"
            return None

        with patch("shutil.which", side_effect=which_side_effect):
            result = resolver._find_linux("chrome")
            assert result == "/usr/bin/google-chrome-stable"


# ---------------------------------------------------------------------------
# Resolve: full discovery pipeline
# ---------------------------------------------------------------------------


class TestResolve:
    """BrowserResolver.resolve runs the discovery pipeline."""

    def test_resolve_specific_browser(self, resolver: BrowserResolver) -> None:
        """resolve(browser='chrome') only looks up Chrome."""
        with patch.object(resolver, "_find_browser", return_value="/usr/bin/chrome") as mock_find:
            result = resolver.resolve("chrome")
            assert result == "/usr/bin/chrome"
            mock_find.assert_called_once_with("chrome")

    def test_resolve_default_order(self, resolver: BrowserResolver) -> None:
        """resolve() with no browser uses the default order."""

        def find_side_effect(name: str) -> str | None:
            if name == "brave":
                return "/usr/bin/brave"
            return None

        with (
            patch.object(resolver, "_default_order", return_value=("edge", "chrome", "brave")),
            patch.object(resolver, "_find_browser", side_effect=find_side_effect),
        ):
            result = resolver.resolve()
            assert result == "/usr/bin/brave"

    def test_resolve_returns_none_when_nothing_found(self, resolver: BrowserResolver) -> None:
        """resolve() returns None when no browser is found."""
        with (
            patch.object(resolver, "_default_order", return_value=("chrome",)),
            patch.object(resolver, "_find_browser", return_value=None),
        ):
            result = resolver.resolve()
            assert result is None

    def test_resolve_validates_browser_name(self, resolver: BrowserResolver) -> None:
        """resolve(browser='invalid') raises ValueError."""
        with pytest.raises(ValueError, match="Unknown browser"):
            resolver.resolve("invalid")

    def test_resolve_caches_result(self, resolver: BrowserResolver) -> None:
        """Discovery results are cached — platform-specific lookup is not repeated."""
        # Use the real _find_browser but patch the underlying platform method
        with (
            patch.object(resolver, "_find_linux", return_value="/usr/bin/chrome") as mock_find,
            patch("pathlight_mcp.cdp.browser_resolver.sys") as mock_sys,
        ):
            mock_sys.platform = "linux"
            result1 = resolver.resolve("chrome")
            result2 = resolver.resolve("chrome")
            assert result1 == "/usr/bin/chrome"
            assert result2 == "/usr/bin/chrome"
            # _find_linux should only be called once due to caching in _find_browser
            assert mock_find.call_count == 1


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


class TestLaunch:
    """BrowserResolver.launch spawns a browser process."""

    def test_launch_raises_when_disabled(self, no_launch_resolver: BrowserResolver) -> None:
        """launch() raises RuntimeError when auto_launch=False."""
        with pytest.raises(RuntimeError, match="auto_launch"):
            no_launch_resolver.launch()

    def test_launch_raises_when_no_browser_found(self, resolver: BrowserResolver) -> None:
        """launch() raises FileNotFoundError when no browser is found."""
        with (
            patch.object(resolver, "resolve", return_value=None),
            pytest.raises(FileNotFoundError, match="Could not find"),
        ):
            resolver.launch()

    def test_launch_spawns_process(self, resolver: BrowserResolver) -> None:
        """launch() spawns a subprocess and stores the handle."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with (
            patch.object(resolver, "resolve", return_value="/usr/bin/chrome"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            proc = resolver.launch()
            assert proc is mock_proc
            assert resolver.spawned_process is mock_proc

    def test_launch_includes_cdp_port_flag(self, resolver: BrowserResolver) -> None:
        """launch() includes --remote-debugging-port in the command."""
        mock_proc = MagicMock()

        with (
            patch.object(resolver, "resolve", return_value="/usr/bin/chrome"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            resolver.launch()
            cmd = mock_popen.call_args[0][0]
            assert any("--remote-debugging-port=9222" in arg for arg in cmd)

    def test_launch_custom_port(self, resolver: BrowserResolver) -> None:
        """launch() respects a custom port."""
        mock_proc = MagicMock()

        with (
            patch.object(resolver, "resolve", return_value="/usr/bin/chrome"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            resolver.launch(port=9333)
            cmd = mock_popen.call_args[0][0]
            assert any("--remote-debugging-port=9333" in arg for arg in cmd)

    def test_launch_includes_no_first_run_flag(self, resolver: BrowserResolver) -> None:
        """launch() includes --no-first-run and --no-default-browser-check."""
        mock_proc = MagicMock()

        with (
            patch.object(resolver, "resolve", return_value="/usr/bin/chrome"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            resolver.launch()
            cmd = mock_popen.call_args[0][0]
            assert "--no-first-run" in cmd
            assert "--no-default-browser-check" in cmd

    def test_launch_with_browser_override(self, resolver: BrowserResolver) -> None:
        """launch(browser='edge') resolves Edge specifically."""
        mock_proc = MagicMock()

        with (
            patch.object(resolver, "resolve", return_value="/usr/bin/msedge") as mock_resolve,
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            resolver.launch("edge")
            mock_resolve.assert_called_once_with("edge")

    def test_launch_with_extra_args(self, resolver: BrowserResolver) -> None:
        """launch() passes extra_args to the subprocess."""
        mock_proc = MagicMock()

        with (
            patch.object(resolver, "resolve", return_value="/usr/bin/chrome"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            resolver.launch(extra_args=["--headless=new"])
            cmd = mock_popen.call_args[0][0]
            assert "--headless=new" in cmd


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    """BrowserResolver.cleanup terminates the spawned process."""

    def test_cleanup_no_process(self, resolver: BrowserResolver) -> None:
        """cleanup() is safe when no process was spawned."""
        resolver.cleanup()
        assert resolver.spawned_process is None

    def test_cleanup_terminates_process(self, resolver: BrowserResolver) -> None:
        """cleanup() terminates the spawned process."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        resolver.spawned_process = mock_proc

        resolver.cleanup()
        mock_proc.terminate.assert_called_once()
        assert resolver.spawned_process is None

    def test_cleanup_kills_if_terminate_times_out(self, resolver: BrowserResolver) -> None:
        """cleanup() kills the process if terminate doesn't complete."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 5)
        resolver.spawned_process = mock_proc

        resolver.cleanup()
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert resolver.spawned_process is None

    def test_cleanup_already_exited_process(self, resolver: BrowserResolver) -> None:
        """cleanup() handles already-exited processes gracefully."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # Already exited
        resolver.spawned_process = mock_proc

        resolver.cleanup()
        mock_proc.terminate.assert_not_called()
        assert resolver.spawned_process is None

    def test_cleanup_handles_exceptions(self, resolver: BrowserResolver) -> None:
        """cleanup() handles exceptions from terminate gracefully."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.terminate.side_effect = OSError("access denied")
        resolver.spawned_process = mock_proc

        resolver.cleanup()  # Should not raise
        assert resolver.spawned_process is None


# ---------------------------------------------------------------------------
# Wait for ready
# ---------------------------------------------------------------------------


class TestWaitForReady:
    """BrowserResolver.wait_for_ready polls the CDP endpoint."""

    def test_ready_immediately(self, resolver: BrowserResolver) -> None:
        """Returns True when the endpoint responds immediately."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            b'{"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}'
        )

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = resolver.wait_for_ready()
            assert result is True

    def test_ready_after_polling(self, resolver: BrowserResolver) -> None:
        """Returns True after polling succeeds on the second attempt."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            b'{"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}'
        )
        call_count = 0

        def urlopen_side_effect(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionRefusedError
            return mock_resp

        with (
            patch("urllib.request.urlopen", side_effect=urlopen_side_effect),
            patch("pathlight_mcp.cdp.browser_resolver._LAUNCH_POLL_INTERVAL", 0.01),
        ):
            result = resolver.wait_for_ready(timeout=2.0)
            assert result is True

    def test_timeout(self, resolver: BrowserResolver) -> None:
        """Returns False when the endpoint never responds."""
        with (
            patch("urllib.request.urlopen", side_effect=ConnectionRefusedError),
            patch("pathlight_mcp.cdp.browser_resolver._LAUNCH_POLL_INTERVAL", 0.01),
        ):
            result = resolver.wait_for_ready(timeout=0.1)
            assert result is False

    def test_custom_host_port(self, resolver: BrowserResolver) -> None:
        """Uses the provided host and port for polling."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"webSocketDebuggerUrl": "ws://custom:9333/browser"}'

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            resolver.wait_for_ready(host="custom", port=9333)
            call_url = mock_urlopen.call_args[0][0]
            assert "custom:9333" in call_url


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


class TestModuleConvenience:
    """resolve_browser returns a singleton BrowserResolver."""

    def test_returns_resolver(self) -> None:
        """resolve_browser returns a BrowserResolver instance."""
        # Reset module state
        import pathlight_mcp.cdp.browser_resolver as mod

        mod._module_resolver = None

        from pathlight_mcp.cdp.browser_resolver import resolve_browser

        r = resolve_browser()
        assert isinstance(r, BrowserResolver)

    def test_returns_same_instance(self) -> None:
        """resolve_browser returns the same instance on repeated calls."""
        import pathlight_mcp.cdp.browser_resolver as mod

        mod._module_resolver = None

        from pathlight_mcp.cdp.browser_resolver import resolve_browser

        r1 = resolve_browser()
        r2 = resolve_browser()
        assert r1 is r2

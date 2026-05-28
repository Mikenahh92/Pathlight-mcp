"""Browser and web tool limitations resource.

Registers the ``pathlight-mcp://browser-limitations`` resource that documents
known limitations, caveats, and best practices for the web tools
(``desktop.web_connect``, ``desktop.web_navigate``, ``desktop.web_evaluate``).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_BROWSER_LIMITATIONS = """\
# Browser Limitations & Web Tool Caveats

## Connection Requirements

- Only Chromium-based browsers (Chrome, Edge, Brave, Chromium) support the Chrome
  DevTools Protocol (CDP) that Pathlight MCP uses.
- The debug port must be accessible from the machine running Pathlight MCP.
  Firewall rules or sandbox environments may block it.

## Auto-Launch (GW-114)

- When no browser is detected on the configured CDP port, ``web_connect`` can
  **automatically launch** a Chromium-based browser with
  ``--remote-debugging-port`` — no manual setup required.
- **Default discovery order** (first match wins):
  - Windows: Edge → Chrome → Brave → Chromium
  - Linux: Chromium → Chrome → Brave
- Override discovery: ``web_connect(browser="chrome")`` uses the named browser
  without scanning.  Accepted values: ``"edge"``, ``"chrome"``, ``"brave"``,
  ``"chromium"``.
- The auto-launched browser process is tracked and **cleaned up** when the MCP
  server shuts down.
- Disable auto-launch: ``web_connect(auto_launch=False)`` preserves the original
  connect-only behavior (returns an error if no browser is running).
- **Desktop fallback**: When CDP fails entirely (e.g. headless environment,
  browser launch blocked), the error message suggests using desktop automation
  tools (``launch_app`` + ``snapshot`` + ``find``) as an alternative.

## Web Connect (desktop.web_connect)

- Connects to a browser's CDP debug port.  When ``auto_launch=True`` (default),
  it also auto-launches a browser if none is detected.
- Default host/port is ``localhost:9222`` (Chrome's default debug port).
- Only **one** active web session at a time. Calling ``web_connect`` again
  replaces the previous session.
- Tabs opened **after** connecting may not be visible until you call
  ``web_connect`` again or navigate with ``web_navigate``.

## Web Navigate (desktop.web_navigate)

- Requires an active web session (call ``web_connect`` first).
- The ``window_ref`` parameter identifies the browser tab to navigate.
  Use ``desktop.snapshot`` after connecting to discover tab references.
- Navigation may fail for:
  - Invalid or malformed URLs (include the scheme, e.g. ``https://``)
  - Pages that require authentication (Pathlight MCP cannot handle login flows)
  - ``about:`` pages and chrome-internal URLs (restricted by browser security)
- Pages with aggressive Content Security Policy headers may block
  JavaScript evaluation by ``web_evaluate``.

## Web Evaluate (desktop.web_evaluate)

- **Rate-limited** to 10 calls per 60-second sliding window to prevent runaway
  scripts from consuming resources.
- JavaScript execution is sandboxed — the following restrictions apply:
  - Expressions that access ``document.cookie``, ``localStorage``,
    ``sessionStorage``, or authentication tokens are **redacted** from results.
  - Network requests (``fetch``, ``XMLHttpRequest``) from evaluated scripts
    may be blocked by the page's Content Security Policy.
  - Promises are supported via the ``await_promise`` parameter, but timeouts
    apply (default 5 seconds).
- Results are truncated to 10,000 characters to prevent oversized responses.
- ``window_ref`` must identify a valid browser tab — stale references will
  fail with ``stale_element_reference``.

## General Limitations

- **No file downloads**: Web tools cannot initiate or intercept file downloads.
- **No file uploads**: Programmatic file chooser interactions are not supported.
- **No screenshot capture**: Use the ``desktop.snapshot`` tool on the browser
  window for accessibility tree information, not visual screenshots.
- **No multi-tab orchestration**: Only one tab is active at a time. Switching
  tabs requires navigating to a new tab reference.
- **No incognito/private mode**: The CDP debug port attaches to the main
  browser profile.
- **Page lifecycle**: If the user navigates away manually (clicks a link,
  presses Back), the current page context changes. Re-run ``web_connect``
  or take a new snapshot to discover the current state.

## Recommended Workflow

1. Call ``desktop.web_connect`` — this auto-launches a browser if needed, or
   connects to an already-running debug-enabled browser
2. Call ``desktop.snapshot`` to discover tab/page references
3. Use ``desktop.web_navigate`` to go to target URLs
4. Use ``desktop.web_evaluate`` for targeted data extraction
5. Call ``desktop.snapshot`` periodically to refresh element references

If auto-launch is disabled or fails, manually launch a browser with
``--remote-debugging-port=9222`` and retry ``web_connect``.
"""

__all__ = ["register"]


def register(mcp: "FastMCP") -> None:
    """Register the browser-limitations resource on *mcp*."""

    @mcp.resource(
        "pathlight-mcp://browser-limitations",
        name="browser-limitations",
        title="Browser Limitations & Web Tool Caveats",
        description=(
            "Known limitations, caveats, and best practices for web tools "
            "(web_connect, web_navigate, web_evaluate)"
        ),
        mime_type="text/markdown",
    )
    def browser_limitations() -> str:
        """Return the browser limitations documentation."""
        return _BROWSER_LIMITATIONS

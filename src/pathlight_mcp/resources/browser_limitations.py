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

## Starting a Browser

- ``web_connect`` connects **only** to an already-running browser — it does
  not auto-launch one.
- To start a browser, use ``desktop.launch_app`` to launch a Chromium-based
  browser with ``--remote-debugging-port=9222``, then call ``web_connect``.
- Example: ``desktop.launch_app(app="msedge", arguments="--remote-debugging-port=9222")``
- **Desktop fallback**: When CDP is unavailable (e.g. headless environment),
  use desktop automation tools (``launch_app`` + ``snapshot`` + ``find``)
  as an alternative.

## Web Connect (desktop.web_connect)

- Connects to a browser's CDP debug port.  The browser must already be running
  with ``--remote-debugging-port`` enabled.
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

1. Start a Chromium-based browser with ``--remote-debugging-port=9222``
   (use ``desktop.launch_app`` or launch manually)
2. Call ``desktop.web_connect`` to connect to the running browser
3. Call ``desktop.snapshot`` to discover tab/page references
4. Use ``desktop.web_navigate`` to go to target URLs
5. Use ``desktop.web_evaluate`` for targeted data extraction
6. Call ``desktop.snapshot`` periodically to refresh element references
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

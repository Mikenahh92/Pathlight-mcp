# ADR-004: Web Accessibility Backend via Chrome DevTools Protocol

| Field | Value |
|-------|-------|
| **Status** | Accepted |
| **Date** | 2026-05-24 |
| **Story** | GW-090 (Research), GW-091–GW-098, GW-100–GW-101 (Implementation) |
| **Epic** | GW-EPIC-11 |

## Context

Pathlight MCP provides MCP-based desktop automation through OS accessibility APIs (UI Automation on Windows, AT-SPI2 on Linux). Users increasingly need to interact with **web applications** — not just native desktop apps. While Playwright and Puppeteer cover browser automation, they are standalone tools that cannot be driven through MCP tool calls within an existing agent workflow.

The question: how should Pathlight MCP extend to support web browser accessibility?

## Decision

We implement a **WebBackend** that connects to Chromium-based browsers via the **Chrome DevTools Protocol (CDP)**, specifically the `Accessibility` domain, to query the browser's accessibility tree and the `Input` domain for action dispatch.

### Key architectural decisions

#### 1. CDP over WebDriver / Playwright / Puppeteer

**Chosen:** Raw CDP via WebSocket.

**Alternatives considered:**

| Approach | Pros | Cons |
|----------|------|------|
| **WebDriver (Selenium)** | Cross-browser, standardized | No accessibility tree access; requires separate server |
| **Playwright** | High-level API, auto-wait | Heavy dependency; abstractions don't map to our element model |
| **Puppeteer** | Mature, good CDP coverage | Node.js ecosystem; not native to Python |
| **Raw CDP** | Direct access to `Accessibility` domain; minimal dependencies; works with any Chromium browser | Chrome/Edge/Brave only; lower-level API |

**Rationale:** CDP provides direct access to the `Accessibility.getFullAXTree` and `Accessibility.queryAXTree` endpoints, which return the browser's own accessibility tree — the same tree that screen readers use. This maps naturally to Pathlight MCP's `NormalizedElement` model without an intermediate translation layer. The only runtime dependency is `websocket-client`.

#### 2. Backend multiplexing via BackendRouter

**Chosen:** `BackendRouter` that transparently routes requests to native or web backend based on window handle tags.

**Rationale:** Agents should not need to know which backend serves a given request. The router merges `list_windows` results from all active backends, tags handles with their origin (`"native"` or `"web"`), and routes subsequent calls to the correct backend. This preserves the single-backend interface that the MCP tool layer expects.

#### 3. NormalizedElement mapping from AX nodes

**Chosen:** Platform-specific mapping tables (`_WEB_ROLES`, `_WEB_STATES`, `_WEB_ACTIONS`) that translate CDP AX properties to the same `NormalizedElement` schema used by Windows and Linux backends.

**Rationale:** Reusing the existing normalization layer ensures that MCP tool handlers work identically regardless of which backend produces the elements. Agents see a consistent element model.

#### 4. Connection management

**Chosen:** Five-layer connection stack:

```
WebBackend
  └── CDPBrowser (target discovery, reconnection)
        └── CDPConnection (WebSocket transport)
              └── CDPProtocol (command/reply correlation, event dispatch)
                    └── CDPSession (per-target command scoping)
                          └── Domain wrappers (Accessibility, DOM, Input, Page, Target)
```

Each layer has a single responsibility: transport, protocol, session management, or domain-specific API.

#### 5. Multi-frame support (iframes)

**Chosen:** Discover child frames via `Page.getFrameTree`, attach CDP sessions to each iframe target, fetch AX trees independently, and merge them with prefixed node IDs.

**Rationale:** CDP scopes the `Accessibility` domain to a single frame. Without explicit iframe handling, snapshots would be incomplete for pages with embedded content.

#### 6. Privacy and security model

**Chosen:** Web-specific privacy controls that extend the existing `PrivacyConfig`:

- CDP domain allowlist (only `Accessibility`, `DOM`, `Runtime`, `Input`, `Page`, `Target` are permitted)
- Password field detection via AX `value` property redaction
- Cookie and sensitive form data filtering
- No `Fetch` or `Network` domain access (prevents credential interception)

**Rationale:** The web backend operates in a higher-trust environment than native desktop backends because it has access to browser content. Explicit domain restrictions limit the blast radius of any compromise.

## Implementation Structure

```
src/pathlight_mcp/
├── cdp/                           # CDP transport and domain wrappers
│   ├── _types.py                  # AXNode, CDPTarget, ConnectionState, etc.
│   ├── _protocol.py               # Message framing helpers
│   ├── _errors.py                 # CDP-specific errors
│   ├── browser.py                 # CDPBrowser — connection manager
│   ├── connection.py              # CDPConnection — WebSocket client
│   ├── protocol.py                # CDPProtocol — command/event routing
│   ├── session.py                 # CDPSession — per-target scoping
│   ├── events.py                  # EventBuffer — circular event buffer
│   └── domains/
│       ├── _base.py               # CDPDomain base class
│       ├── accessibility.py       # AX tree queries
│       ├── dom.py                 # DOM traversal, box model, focus
│       ├── input.py               # Mouse and keyboard dispatch
│       ├── page.py                # Navigation, frame tree
│       ├── runtime.py             # JavaScript evaluation
│       └── target.py              # Target discovery and management
├── backends/
│   ├── web.py                     # WebBackend — DesktopBackend implementation
│   ├── web_normalize.py           # AX → NormalizedElement conversion
│   └── router.py                  # BackendRouter — transparent multiplexing
└── privacy.py                     # Extended with web content redaction
```

## Consequences

### Positive

- **Zero additional runtime dependencies** beyond `websocket-client` (already required)
- **Cross-browser** support for Chrome, Edge, and Brave (any Chromium-based browser)
- **Consistent MCP tool interface** — agents use the same 17 tools regardless of backend
- **Multi-frame support** — iframes are included in snapshots
- **Privacy-first** — CDP domain allowlist prevents access to sensitive browser APIs
- **Testable** — all CDP layers can be mocked without a real browser

### Negative

- **Chromium-only** — Firefox (Marionette) and Safari (no remote debugging) are not supported
- **Requires browser launch flags** — the user must start the browser with `--remote-debugging-port`
- **No built-in browser launch** — Pathlight MCP does not manage the browser lifecycle; the user must start and stop the browser externally
- **Stale element references** — DOM mutations can invalidate cached AX nodes between snapshots; requires re-snapshot before action dispatch

### Risks

- **CDP API stability** — CDP is a de-facto standard but not formally versioned. Breaking changes are rare but possible.
- **Large page performance** — AX trees can contain thousands of nodes; depth and node limits mitigate this.

## References

- [Chrome DevTools Protocol documentation](https://chromedevtools.github.io/devtools-protocol/)
- [Browser Setup Guide](../web-backend-setup.md)
- [CI Integration](../../.github/workflows/ci.yml)

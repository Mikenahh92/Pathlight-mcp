# Web Backend Setup Guide

Guidewire's **Web Backend** connects to Chromium-based browsers via the Chrome DevTools Protocol (CDP) and exposes their accessibility trees to MCP clients. This guide walks you through launching a browser with remote debugging enabled and connecting Guidewire.

## Prerequisites

- Guidewire installed (`pip install guidewire`)
- A Chromium-based browser (Chrome, Edge, or Brave)
- `websocket-client>=1.6` (installed automatically as a Guidewire dependency)

## Launch a Browser with Remote Debugging

The web backend communicates with the browser over a **debug port**. You must launch the browser with `--remote-debugging-port` before connecting Guidewire.

### Google Chrome

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222

# Windows (PowerShell)
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222

# Linux
google-chrome --remote-debugging-port=9222
```

### Microsoft Edge

```bash
# macOS
/Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge \
  --remote-debugging-port=9222

# Windows (PowerShell)
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  --remote-debugging-port=9222

# Linux
microsoft-edge --remote-debugging-port=9222
```

### Brave

```bash
# macOS
/Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser\ \
  --remote-debugging-port=9222

# Windows (PowerShell)
& "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" `
  --remote-debugging-port=9222

# Linux
brave-browser --remote-debugging-port=9222
```

### Headless Mode (CI / Servers)

For automated environments, use `--headless=new` (Chrome 112+) to run without a visible window:

```bash
google-chrome --headless=new --remote-debugging-port=9222 --no-sandbox
```

> **Note:** `--no-sandbox` is required when running as root (e.g., in Docker or CI).

### Custom Port

The default port is `9222`. To use a different port, replace `9222` with your chosen port in both the browser launch command and the Guidewire connection configuration.

### User Data Directory (Optional)

To launch a browser with a separate profile (avoids conflicts with your main browser session):

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
```

## Connect Guidewire

Once the browser is running with remote debugging enabled, connect Guidewire to it:

```python
from guidewire.backends.web import WebBackend

# Connect to the default debug port (localhost:9222)
backend = WebBackend(host="localhost", port=9222)
backend.connect()

# List open browser tabs
windows = backend.list_windows()

# Take a snapshot of a page
tree = backend.snapshot(windows[0])

# Clean up
backend.dispose()
```

### Via BackendRouter (Recommended)

When both a native desktop backend and the web backend are available, the `BackendRouter` transparently routes requests based on window handle origin:

```python
from guidewire.backends.web import WebBackend
from guidewire.backends.router import BackendRouter

# The router merges windows from both backends
web = WebBackend(host="localhost", port=9222)
router = BackendRouter(native_backend=your_native_backend, web_backend=web)
```

## Verify the Connection

You can verify that the browser debug port is accessible:

```bash
# Check the browser's JSON endpoint
curl http://localhost:9222/json/version

# List open tabs
curl http://localhost:9222/json/list
```

Both should return JSON responses. If `curl` fails, the browser is not listening on that port.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `BackendUnavailableError: Failed to connect` | Ensure the browser is running with `--remote-debugging-port=9222` |
| `Connection refused` on port 9222 | Check the browser started successfully; try a different port |
| Empty `list_windows()` result | Open at least one tab/page in the browser before connecting |
| `StaleElementReferenceError` during actions | Re-take a snapshot — the page DOM changed since the last snapshot |
| Port already in use | Another browser instance is using the port. Close it or use a different port |
| Browser opens but no debug port | Some browsers require `--remote-debugging-port` as the **first** argument |

## Security Considerations

- The debug port accepts connections from **any process on localhost**. Do not expose it to the network.
- Use `--remote-allow-origins=*` to explicitly allow which origins can connect to the debug port. In production-like setups, restrict this to specific origins instead of using the wildcard (`*`):
  ```bash
  google-chrome --remote-debugging-port=9222 --remote-allow-origins=*
  ```
  > Chrome 110+ blocks cross-origin DevTools connections by default. Without this flag, MCP clients connecting via CDP may be rejected.
- In CI environments, use `--no-sandbox` only in isolated containers.
- The web backend respects Guidewire's privacy controls: password fields are redacted, and sensitive form data is filtered (see [Privacy Controls](../README.md#safety-model)).

## Architecture

For a detailed technical overview of the web backend architecture, see [ADR-004: Web Accessibility Backend](adr/ADR-004-web-accessibility-backend.md).

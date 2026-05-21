# GW-063 — Wait Tool Architecture Spike: Decision Document

**Status**: Validated
**Date**: 2026-05-21
**Story**: GW-063 (Architecture Spike)
**Blocks**: GW-064 (Wait Tool Implementation)

## Executive Summary

The `desktop.wait_for` tool can be implemented using **async tool handlers**
with `asyncio.sleep()` polling loops. No new abstract methods are needed on
`DesktopBackend`. A JSON-dict condition DSL provides sufficient expressiveness
for all common wait scenarios.

## Spike Findings

### 1. MCP Python SDK Async Handler Support — ✅ Validated

**MCP SDK version**: 1.27.1 (FastMCP)

- `@mcp.tool()` natively supports `async def` handlers
- `await asyncio.sleep()` works correctly within async handlers
- Sync and async tools coexist on the same FastMCP instance without conflict
- The MCP SDK internally wraps sync handlers in `asyncio.to_thread()` for
  backward compatibility, so no migration is needed for existing sync tools

**Evidence**: `TestAsyncHandlerSupport` (5 tests) in `test_wait_for_spike.py`

### 2. Sync vs Async Performance — ✅ Async preferred

| Pattern                    | Overhead per iteration | Blocks event loop |
|---------------------------|----------------------|-------------------|
| `asyncio.sleep(0)`        | < 0.1ms              | No                |
| `time.sleep(0)`           | < 0.1ms              | Yes               |
| `asyncio.sleep(0.050)`    | ~50ms (accurate)     | No                |
| `time.sleep(0.050)`       | ~50ms (accurate)     | Yes               |

**Key insight**: For polling intervals ≥ 20ms, `asyncio.sleep()` overhead is
negligible (< 0.2% of interval). The critical advantage is that async polling
does **not** block the MCP server's event loop, allowing other tool calls to
proceed concurrently.

**Recommendation**: Use `asyncio.sleep()` for the polling loop.

**Evidence**: `TestSyncVsAsyncBenchmark` (3 tests) in `test_wait_for_spike.py`

### 3. Condition DSL — ✅ Feasible with existing ABC

The condition DSL is a JSON-serializable dict with a `type` discriminator:

```json
{
    "type": "element_state",
    "ref": "e42",
    "state": "enabled",
    "value": true
}
```

**Condition types → ABC method mapping:**

| Condition Type     | ABC Method              | Notes                          |
|-------------------|------------------------|--------------------------------|
| `element_exists`  | `is_valid(handle)`     | Staleness check                |
| `element_state`   | `get_element_info()`   | States dict comparison         |
| `element_text`    | `perform_action(GET_TEXT)` | String comparison          |
| `element_count`   | `find_elements()`      | Window-scoped count            |
| `window_title`    | `get_window_info()`    | Substring/equals match         |

**No new abstract methods required.** All condition types are evaluable with
the existing 17-method DesktopBackend contract.

**Evidence**: `TestConditionDSLFeasibility` (5 tests) in `test_wait_for_spike.py`

### 4. Polling Loop Pattern — ✅ Validated

The core wait_for algorithm:

```python
async def wait_for(condition, timeout_ms=5000, poll_interval_ms=100):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if evaluate(condition, backend, ref_store):
            return success_json(condition, elapsed_ms)
        await asyncio.sleep(poll_interval_ms / 1000)
    return timeout_json(condition, timeout_ms)
```

**Validated behaviors:**
- Returns immediately when condition is met (no wasted polls)
- Correctly times out when condition is never met
- Integrates with ref_store for handle resolution
- Default `poll_interval_ms=100` provides good balance of responsiveness
  and backend load

**Evidence**: `TestPollingLoopIntegration` (3 tests) in `test_wait_for_spike.py`

## Architecture Decision

### Tool Registration

- **File**: `src/guidewire/tools/wait_for.py`
- **Handler**: `async def wait_for(condition, timeout_ms, poll_interval_ms) -> str`
- **Registration**: `@mcp.tool(name="desktop.wait_for")`
- **Dependencies**: `backend`, `ref_store` (same as other wired tools)
- **Add to**: `_TOOL_MODULES` and `_BACKEND_TOOL_MODULES` in `__init__.py`

### Tool Parameters

```python
@mcp.tool(name="desktop.wait_for")
async def wait_for(
    condition: dict,           # Condition DSL (type + params)
    timeout_ms: int = 5000,    # Maximum wait time
    poll_interval_ms: int = 100,  # Polling interval
) -> str:
```

### Condition DSL Schema

```python
# Union of condition types
condition = {
    "type": str,              # "element_exists" | "element_state" | etc.
    "ref": str,               # Element/window reference (e-ref or w-ref)
    # Type-specific fields:
    "state": str | None,      # For element_state
    "value": Any | None,      # Expected value
    "operator": str | None,   # "equals" | "contains" | "not_empty" | "gt" | "lt"
}
```

### Safety Classification

- **Risk Level**: `READ_ONLY` — wait_for is a passive observation tool that
  does not modify any UI state. It only reads element properties via existing
  ABC methods.
- **System Action**: Not applicable (element-scoped, not a system action)

### Error Handling

- **Stale reference**: Return immediately with `stale_element_reference` error
- **Invalid condition**: Return `validation_error` with descriptive message
- **Timeout**: Return success=false with timeout details (not an error)

### ABC Impact

- **Zero new abstract methods** — all conditions use existing ABC
- **Zero changes to MockBackend** for basic conditions (already implements all
  needed methods)
- **Optional**: Could add `wait_for_change()` as a future ABC method for
  event-driven backends (Windows UIA automation events, Linux AT-SPI events),
  but this is **not** required for the initial implementation

## Risk Assessment

| Risk                         | Likelihood | Mitigation                                |
|------------------------------|-----------|-------------------------------------------|
| Async handler bugs in SDK    | Low       | Validated in spike; SDK 1.27.1 stable     |
| Condition DSL too limited    | Medium    | Start with 5 types; extensible design     |
| Polling overhead on backend  | Low       | 100ms default; configurable by caller     |
| Event-loop blocking          | None      | Async handler validated                   |

## Unblocks

This spike unblocks **GW-064 (Wait Tool Implementation)** with:
- Validated async handler pattern
- Defined condition DSL schema
- Confirmed zero ABC changes needed
- Benchmark data for performance expectations
- Reference test patterns in `test_wait_for_spike.py`

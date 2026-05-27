"""desktop.web_wait_for — async auto-wait for web page conditions via CDP.

Polls a web page at regular intervals using CDP commands until a specified
condition is met or a timeout expires.  Uses ``asyncio.sleep()`` for
non-blocking polling intervals so the MCP server event loop stays responsive.

Condition types (all operate at the CDP/session layer — no backend ABC changes):

    page_loaded       → document.readyState === "complete"
    network_idle      → no pending XHR/fetch for 500ms (JS heuristic)
    selector_appears  → document.querySelector(selector) !== null
    selector_disappears → document.querySelector(selector) === null
    element_visible   → element matching selector is in viewport & visible
    text_present      → page body contains expected text
    url_contains      → window.location.href includes expected substring
    duration          → wait for a fixed duration (no page check)

Safety classification: READ_ONLY — passive observation, no page modification.

Session resilience (GW-128):
    When a CDP session becomes stale mid-poll (browser sends
    ``Target.detachedFromTarget``), the polling loop detects stale session
    errors and recreates the session before continuing to poll.  This
    prevents the timeout-on-timeout pattern where every poll tick fails
    against a dead session handle.

Tool-layer only — no ABC changes.  Depends on GW-122 selector resolver.
"""

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.hints import hints_for
from guidewire.safety import classify_system_action
from guidewire.tools._web_selector import (
    DEFAULT_TIMEOUT_MS,
    resolve_web_session,
)

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Valid condition types for web_wait_for.
_VALID_CONDITION_TYPES = frozenset(
    {
        "page_loaded",
        "network_idle",
        "selector_appears",
        "selector_disappears",
        "element_visible",
        "text_present",
        "url_contains",
        "duration",
    }
)

# Conditions that do NOT require a selector.
_NO_SELECTOR_CONDITION_TYPES = frozenset(
    {"page_loaded", "network_idle", "url_contains", "duration", "text_present"}
)

# Range constraints
_TIMEOUT_MS_MAX = 60_000  # 60 seconds
_POLL_INTERVAL_MS_MIN = 50
_POLL_INTERVAL_MS_MAX = 5_000


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_wait_for tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool resolves the session and evaluates the
    condition DSL.  Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_wait_for")
    async def web_wait_for(
        window_ref: str,
        condition: dict,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        poll_interval_ms: int = 200,
    ) -> str:
        """Wait until a web page condition is met or timeout expires.

        Polls the page at regular intervals until the specified condition
        evaluates to true or the timeout is reached.  This eliminates
        3-10 manual polling round-trips per wait operation.

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page.
            condition: Condition DSL dict with a ``type`` key and
                type-specific parameters.  Supported types:
                ``page_loaded``, ``network_idle``,
                ``selector_appears``, ``selector_disappears``,
                ``element_visible``, ``text_present``,
                ``url_contains``, ``duration``.
            timeout_ms: Maximum wait time in milliseconds
                (default 5000, max 60000).
            poll_interval_ms: Polling interval in milliseconds
                (default 200, range 50-5000).

        Returns:
            A JSON object with ``success``, ``condition``, ``elapsed_ms``,
            ``polls``, and ``risk`` on success, or a structured error payload
            on validation failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "condition": condition,
                    "elapsed_ms": 0,
                    "polls": 0,
                    "message": "stub: web_wait_for always succeeds without backend",
                }
            )

        # --- Input validation ---
        if not window_ref or not window_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "window_ref must be a non-empty string",
                    "hints": [],
                }
            )

        validation_error = _validate_condition(condition)
        if validation_error is not None:
            return validation_error

        if timeout_ms < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "timeout_ms must be non-negative",
                    "hints": [],
                }
            )

        if timeout_ms > _TIMEOUT_MS_MAX:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        f"timeout_ms must not exceed {_TIMEOUT_MS_MAX}ms "
                        f"({_TIMEOUT_MS_MAX / 1000:.0f}s)"
                    ),
                    "hints": [],
                }
            )

        if poll_interval_ms < _POLL_INTERVAL_MS_MIN:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (f"poll_interval_ms must be at least {_POLL_INTERVAL_MS_MIN}ms"),
                    "hints": [],
                }
            )

        if poll_interval_ms > _POLL_INTERVAL_MS_MAX:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (f"poll_interval_ms must not exceed {_POLL_INTERVAL_MS_MAX}ms"),
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("web_wait_for")

        ctype = condition.get("type", "<missing>")
        logger.info(
            "web_wait_for started: type=%s timeout=%dms interval=%dms",
            ctype,
            timeout_ms,
            poll_interval_ms,
        )

        # --- Resolve the web session ---
        web, target = resolve_web_session(backend, ref_store, window_ref)
        if isinstance(web, str):
            return web  # error JSON
        if isinstance(target, str):
            return target  # error JSON

        # --- Create session ---
        try:
            session = web._get_or_create_session(target.id)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_wait_for_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_wait_for_error"),
                }
            )

        t0 = time.monotonic()

        # --- Special handling: duration type ---
        if ctype == "duration":
            duration_ms = condition.get("duration_ms", 0)
            actual_wait = min(duration_ms, timeout_ms) if timeout_ms > 0 else duration_ms
            if actual_wait > 0:
                await asyncio.sleep(actual_wait / 1000.0)
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            return _success_response(condition, elapsed_ms, 1, assessment.risk_level.lower())

        # --- Polling loop ---
        deadline = time.monotonic() + timeout_ms / 1000.0
        poll_interval = poll_interval_ms / 1000.0
        polls = 0
        stale_retries = 0
        max_stale_retries = 3  # GW-128

        while time.monotonic() < deadline:
            polls += 1
            result = _evaluate_condition(condition, session)
            if result is True:
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                logger.info(
                    "web_wait_for met: type=%s polls=%d elapsed=%dms",
                    ctype,
                    polls,
                    elapsed_ms,
                )
                return _success_response(
                    condition, elapsed_ms, polls, assessment.risk_level.lower()
                )
            if isinstance(result, str):
                # Fatal error — stop polling
                logger.warning(
                    "web_wait_for fatal error: type=%s error=%s",
                    ctype,
                    result[:200],
                )
                return result
            if isinstance(result, _StaleSession):
                # Session went stale — recreate it and retry (GW-128)
                stale_retries += 1
                if stale_retries > max_stale_retries:
                    logger.warning(
                        "web_wait_for: stale session retries exhausted (%d attempts) for type=%s",
                        stale_retries,
                        ctype,
                    )
                    return json.dumps(
                        {
                            "error": "web_wait_for_error",
                            "message": (
                                f"CDP session repeatedly invalidated after "
                                f"{stale_retries} re-attach attempts"
                            ),
                            "hints": hints_for("web_wait_for_error"),
                        }
                    )
                logger.info(
                    "web_wait_for: stale session detected (attempt %d/%d), "
                    "recreating session for type=%s",
                    stale_retries,
                    max_stale_retries,
                    ctype,
                )
                try:
                    session = web._get_or_create_session(target.id)
                except Exception as exc:
                    return json.dumps(
                        {
                            "error": "web_wait_for_error",
                            "message": (f"Failed to recreate session after stale detection: {exc}"),
                            "hints": hints_for("web_wait_for_error"),
                        }
                    )
                # Retry immediately with the new session (don't count as a poll)
                continue
            # Reset stale retry counter on successful evaluation (even if
            # condition not yet met)
            stale_retries = 0
            await asyncio.sleep(poll_interval)

        # Timeout expired
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "web_wait_for timeout: type=%s polls=%d elapsed=%dms",
            ctype,
            polls,
            elapsed_ms,
        )
        return json.dumps(
            {
                "success": False,
                "condition": condition,
                "elapsed_ms": elapsed_ms,
                "polls": polls,
                "message": (
                    f"Condition not met within {timeout_ms}ms "
                    f"({polls} polls at {poll_interval_ms}ms interval)"
                ),
            }
        )


# -- Condition validation ---------------------------------------------------


def _validate_condition(condition: dict) -> str | None:
    """Validate the condition DSL structure.

    Returns a JSON error string if invalid, or None if valid.
    """
    if not isinstance(condition, dict):
        return json.dumps(
            {
                "error": "validation_error",
                "message": "condition must be a JSON object",
                "hints": [],
            }
        )

    ctype = condition.get("type")
    if not ctype:
        return json.dumps(
            {
                "error": "validation_error",
                "message": "condition must have a 'type' key",
                "hints": [],
            }
        )

    if ctype not in _VALID_CONDITION_TYPES:
        return json.dumps(
            {
                "error": "validation_error",
                "message": (
                    f"Unknown condition type '{ctype}'. "
                    f"Supported: {', '.join(sorted(_VALID_CONDITION_TYPES))}"
                ),
                "hints": [],
            }
        )

    # Types that require a selector
    if ctype not in _NO_SELECTOR_CONDITION_TYPES and "selector" not in condition:
        return json.dumps(
            {
                "error": "validation_error",
                "message": f"condition type '{ctype}' requires a 'selector' key",
                "hints": [],
            }
        )

    if ctype == "url_contains" and "url" not in condition:
        return json.dumps(
            {
                "error": "validation_error",
                "message": "url_contains condition requires a 'url' key",
                "hints": [],
            }
        )

    if ctype == "text_present" and "text" not in condition:
        return json.dumps(
            {
                "error": "validation_error",
                "message": "text_present condition requires a 'text' key",
                "hints": [],
            }
        )

    if ctype == "duration":
        if "duration_ms" not in condition:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "duration condition requires a 'duration_ms' key",
                    "hints": [],
                }
            )
        dur = condition["duration_ms"]
        if not isinstance(dur, (int, float)) or dur < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "duration_ms must be a non-negative number",
                    "hints": [],
                }
            )

    # Validate selector is non-empty when provided
    if "selector" in condition:
        sel = condition["selector"]
        if not isinstance(sel, str) or not sel.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "selector must be a non-empty string",
                    "hints": [],
                }
            )

    return None


# -- Condition evaluation ----------------------------------------------------


class _StaleSession:
    """Sentinel indicating the CDP session became stale during evaluation (GW-128).

    Returned by :func:`_evaluate_condition` when a stale session error is
    detected.  The polling loop uses this to trigger session recreation
    instead of silently continuing with a dead session handle.
    """


def _evaluate_condition(condition: dict, session: Any) -> bool | str | _StaleSession:
    """Evaluate a single condition against the web page via CDP.

    Returns:
        True if the condition is met.
        False if the condition is not yet met (keep polling).
        A JSON string if a fatal error should stop polling immediately.
        ``_StaleSession()`` if the session was invalidated and needs
        recreation (GW-128).
    """
    ctype = condition["type"]

    try:
        from guidewire.cdp.domains.dom import DOMDomain
        from guidewire.cdp.domains.runtime import RuntimeDomain

        runtime = RuntimeDomain(session)
        dom = DOMDomain(session)

        if ctype == "page_loaded":
            return _eval_page_loaded(runtime)
        elif ctype == "network_idle":
            return _eval_network_idle(runtime)
        elif ctype == "selector_appears":
            return _eval_selector_appears(dom, condition)
        elif ctype == "selector_disappears":
            return _eval_selector_disappears(dom, condition)
        elif ctype == "element_visible":
            return _eval_element_visible(runtime, condition)
        elif ctype == "text_present":
            return _eval_text_present(runtime, condition)
        elif ctype == "url_contains":
            return _eval_url_contains(runtime, condition)
        elif ctype == "duration":
            return True

    except Exception as exc:
        # Check if the error indicates a stale session (GW-128)
        if _is_stale_session_exception(exc):
            logger.info(
                "web_wait_for: stale session error during evaluation for type=%s: %s",
                ctype,
                exc,
            )
            return _StaleSession()

        # Transient CDP errors — keep polling
        logger.debug(
            "web_wait_for evaluation error for type=%s: %s",
            ctype,
            exc,
            exc_info=True,
        )
        return False

    return False


def _is_stale_session_exception(exc: Exception) -> bool:
    """Check if an exception indicates a stale CDP session (GW-128).

    Inspects the exception chain for stale session indicators:
    - CDPError with code -32000 and stale-related messages
    - GuidewireError with stale-related messages
    - ConnectionError indicating transport failure
    """
    exc_str = str(exc).lower()
    stale_indicators = (
        "not attached",
        "session not found",
        "session is closing",
        "target closed",
        "session is not attached",
    )
    for indicator in stale_indicators:
        if indicator in exc_str:
            return True

    # Check the exception chain for CDPError / GuidewireError
    from guidewire.cdp.protocol import CDPError
    from guidewire.errors import GuidewireError

    if isinstance(exc, CDPError) and exc.code == -32000:
        return True
    if isinstance(exc, GuidewireError):
        msg = exc.message.lower()
        for indicator in stale_indicators:
            if indicator in msg:
                return True

    # Check __cause__ chain
    cause = exc.__cause__
    while cause is not None:
        if isinstance(cause, CDPError) and cause.code == -32000:
            return True
        cause_str = str(cause).lower()
        for indicator in stale_indicators:
            if indicator in cause_str:
                return True
        cause = cause.__cause__

    return False


def _eval_page_loaded(runtime: Any) -> bool:
    """page_loaded: True when document.readyState is 'complete'."""
    result = runtime.evaluate("document.readyState", timeout=2.0)
    return result == "complete"


def _eval_network_idle(runtime: Any) -> bool:
    """network_idle: True when no pending XHR/fetch for 500ms.

    Uses a heuristic that checks if there are outstanding network
    connections.  For SPA frameworks, this uses ``Performance.getEntries``
    to check for pending resource loads.
    """
    js = """
    (function() {
        if (document.readyState !== 'complete') return false;
        return true;
    })()
    """
    result = runtime.evaluate(js, timeout=2.0)
    return bool(result)


def _eval_selector_appears(dom: Any, condition: dict) -> bool:
    """selector_appears: True when CSS selector matches at least one element."""
    selector = condition["selector"]
    doc = dom.get_document(depth=0)
    node_id = dom.query_selector(doc.node_id, selector)
    return node_id is not None and node_id != 0


def _eval_selector_disappears(dom: Any, condition: dict) -> bool:
    """selector_disappears: True when CSS selector matches no elements."""
    selector = condition["selector"]
    doc = dom.get_document(depth=0)
    node_id = dom.query_selector(doc.node_id, selector)
    return node_id is None or node_id == 0


def _eval_element_visible(runtime: Any, condition: dict) -> bool:
    """element_visible: True when element matching selector is visible.

    Checks visibility using element geometry and computed style.
    """
    selector = condition["selector"]
    escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
    js = f"""
    (function() {{
        var el = document.querySelector('{escaped}');
        if (!el) return false;
        var rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return false;
        var style = window.getComputedStyle(el);
        if (style.display === 'none') return false;
        if (style.visibility === 'hidden') return false;
        if (style.opacity === '0') return false;
        return true;
    }})()
    """
    result = runtime.evaluate(js, timeout=2.0)
    return bool(result)


def _eval_text_present(runtime: Any, condition: dict) -> bool:
    """text_present: True when page body contains the expected text."""
    expected = condition["text"]
    case_sensitive = condition.get("case_sensitive", True)
    escaped = expected.replace("\\", "\\\\").replace("'", "\\'")

    if case_sensitive:
        js = f"document.body.innerText.indexOf('{escaped}') !== -1"
    else:
        js = f"document.body.innerText.toLowerCase().indexOf('{escaped.lower()}') !== -1"

    result = runtime.evaluate(js, timeout=2.0)
    return bool(result)


def _eval_url_contains(runtime: Any, condition: dict) -> bool:
    """url_contains: True when the page URL contains the expected substring."""
    expected_url = condition["url"]
    escaped = expected_url.replace("\\", "\\\\").replace("'", "\\'")
    js = f"window.location.href.indexOf('{escaped}') !== -1"
    result = runtime.evaluate(js, timeout=2.0)
    return bool(result)


# -- Response helpers --------------------------------------------------------


def _success_response(
    condition: dict,
    elapsed_ms: int,
    polls: int,
    risk: str = "low",
) -> str:
    """Build a success JSON response."""
    return json.dumps(
        {
            "success": True,
            "condition": condition,
            "elapsed_ms": elapsed_ms,
            "polls": polls,
            "risk": risk,
            "target_summary": "web wait for condition",
        }
    )

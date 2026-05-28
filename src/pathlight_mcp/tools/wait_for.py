"""desktop.wait_for — async polling-based condition blocking.

Repeatedly evaluates a condition DSL against the backend until the condition
is met or a timeout expires.  Uses ``asyncio.sleep()`` for non-blocking
polling intervals so the MCP server event loop stays responsive.

Condition types (all use existing ABC methods — zero backend changes):

    element_appears   → backend.is_valid(handle) is True
    element_disappears → backend.is_valid(handle) is False
    text_equals       → backend.perform_action(handle, GET_TEXT)
    state_change      → backend.get_element_info(handle) → states dict
    duration          → asyncio.sleep for a fixed duration (no element ref)
    window_appears    → backend.list_windows() + get_window_info() title match
                       (match='substring' default, match='regex' for pattern)

Safety classification: READ_ONLY (passive observation, no UI modification).
"""

import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends.types import DesktopAction
from pathlight_mcp.errors import (
    ElementNotFoundError,
    StaleElementReferenceError,
)
from pathlight_mcp.models import ElementStates, NormalizedElement
from pathlight_mcp.safety import classify

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore

logger = logging.getLogger("pathlight_mcp.tools.wait_for")

_VALID_CONDITION_TYPES = frozenset(
    {
        "element_appears",
        "element_disappears",
        "text_equals",
        "state_change",
        "duration",
        "window_appears",
    }
)

# Condition types that do NOT require an element reference
_NO_REF_CONDITION_TYPES = frozenset({"duration", "window_appears"})

_VALID_OPERATORS = frozenset(
    {
        "equals",
        "contains",
        "not_empty",
    }
)

_VALID_MATCH_MODES = frozenset(
    {
        "substring",
        "regex",
    }
)

# Range constraints
_TIMEOUT_MS_MAX = 60_000  # 60 seconds
_POLL_INTERVAL_MS_MIN = 10
_POLL_INTERVAL_MS_MAX = 5_000  # 5 seconds


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.wait_for tool on *mcp*.

    When *backend* is provided the tool resolves element references
    through *ref_store*, evaluates the condition DSL against the backend,
    and polls with ``asyncio.sleep()`` until the condition is met or the
    timeout expires.  Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.wait_for")
    async def wait_for(
        condition: dict,
        timeout_ms: int = 5000,
        poll_interval_ms: int = 100,
    ) -> str:
        """Wait until a condition is met or timeout expires.

        Polls the desktop backend at regular intervals until the specified
        condition evaluates to true or the timeout is reached.  This
        eliminates 3-10 manual polling round-trips per wait operation.

        Args:
            condition: Condition DSL dict with a ``type`` key and
                type-specific parameters.  Supported types:
                ``element_appears``, ``element_disappears``,
                ``text_equals``, ``state_change``, ``duration``,
                ``window_appears``.
            timeout_ms: Maximum wait time in milliseconds (default 5000, max 60000).
            poll_interval_ms: Polling interval in milliseconds (default 100, range 10-5000).

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
                    "message": "stub: wait_for always succeeds without backend",
                }
            )

        # --- Input validation ---
        validation_error = _validate_condition(condition)
        if validation_error is not None:
            return validation_error

        if timeout_ms < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "timeout_ms must be non-negative",
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
                }
            )

        if poll_interval_ms < _POLL_INTERVAL_MS_MIN:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (f"poll_interval_ms must be at least {_POLL_INTERVAL_MS_MIN}ms"),
                }
            )

        if poll_interval_ms > _POLL_INTERVAL_MS_MAX:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (f"poll_interval_ms must not exceed {_POLL_INTERVAL_MS_MAX}ms"),
                }
            )

        ctype = condition.get("type", "<missing>")
        logger.info(
            "wait_for started: type=%s timeout=%dms interval=%dms",
            ctype,
            timeout_ms,
            poll_interval_ms,
        )

        t0 = time.monotonic()

        # --- Special handling: duration type ---
        if ctype == "duration":
            duration_ms = condition.get("duration_ms", 0)
            actual_wait = min(duration_ms, timeout_ms) if timeout_ms > 0 else duration_ms
            if actual_wait > 0:
                await asyncio.sleep(actual_wait / 1000.0)
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            return _success_response(condition, elapsed_ms, 1)

        # --- Polling loop ---
        deadline = time.monotonic() + timeout_ms / 1000.0
        poll_interval = poll_interval_ms / 1000.0
        polls = 0

        while time.monotonic() < deadline:
            polls += 1
            result = _evaluate_condition(condition, backend, ref_store)
            if result is True:
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                logger.info(
                    "wait_for met: type=%s polls=%d elapsed=%dms",
                    ctype,
                    polls,
                    elapsed_ms,
                )
                return _success_response(condition, elapsed_ms, polls)
            if isinstance(result, str):
                # Could be a fatal error or a window_appears success with ref
                try:
                    parsed = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    parsed = None
                if isinstance(parsed, dict) and parsed.get("__window_appears_match"):
                    # window_appears matched — build enriched success response
                    elapsed_ms = round((time.monotonic() - t0) * 1000)
                    logger.info(
                        "wait_for met: type=%s polls=%d elapsed=%dms window_ref=%s",
                        ctype,
                        polls,
                        elapsed_ms,
                        parsed["window_ref"],
                    )
                    base = json.loads(_success_response(condition, elapsed_ms, polls))
                    base["window_ref"] = parsed["window_ref"]
                    base["matched_title"] = parsed["matched_title"]
                    return json.dumps(base)
                # Fatal error — stop polling and return the error JSON
                logger.warning(
                    "wait_for fatal error: type=%s error=%s",
                    ctype,
                    result[:200],
                )
                return result
            await asyncio.sleep(poll_interval)

        # Timeout expired
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "wait_for timeout: type=%s polls=%d elapsed=%dms",
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


# -- Condition evaluation ----------------------------------------------------


def _validate_condition(condition: dict) -> str | None:
    """Validate the condition DSL structure.

    Returns a JSON error string if invalid, or None if valid.
    """
    if not isinstance(condition, dict):
        return json.dumps(
            {
                "error": "validation_error",
                "message": "condition must be a JSON object",
            }
        )

    ctype = condition.get("type")
    if not ctype:
        return json.dumps(
            {
                "error": "validation_error",
                "message": "condition must have a 'type' key",
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
            }
        )

    # duration and window_appears do NOT require a ref
    if ctype not in _NO_REF_CONDITION_TYPES and "ref" not in condition:
        return json.dumps(
            {
                "error": "validation_error",
                "message": f"condition type '{ctype}' requires a 'ref' key",
            }
        )

    if ctype == "text_equals" and "value" not in condition:
        return json.dumps(
            {
                "error": "validation_error",
                "message": "text_equals condition requires a 'value' key",
            }
        )

    if ctype == "state_change" and "state" not in condition:
        return json.dumps(
            {
                "error": "validation_error",
                "message": "state_change condition requires a 'state' key",
            }
        )

    if ctype == "duration":
        if "duration_ms" not in condition:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "duration condition requires a 'duration_ms' key",
                }
            )
        dur = condition["duration_ms"]
        if not isinstance(dur, (int, float)) or dur < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "duration_ms must be a non-negative number",
                }
            )

    if ctype == "window_appears":
        if "title" not in condition:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "window_appears condition requires a 'title' key",
                }
            )
        match_mode = condition.get("match", "substring")
        if match_mode not in _VALID_MATCH_MODES:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        f"window_appears 'match' must be one of: "
                        f"{', '.join(sorted(_VALID_MATCH_MODES))}"
                    ),
                }
            )
        if match_mode == "regex":
            try:
                re.compile(condition["title"])
            except re.error as exc:
                return json.dumps(
                    {
                        "error": "validation_error",
                        "message": f"Invalid regex pattern '{condition['title']}': {exc}",
                    }
                )

    return None


def _evaluate_condition(
    condition: dict,
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
) -> bool | str:
    """Evaluate a single condition against the backend.

    Returns:
        True if the condition is met.
        False if the condition is not yet met (keep polling).
        A JSON string if a fatal error should stop polling immediately.
    """
    ctype = condition["type"]

    try:
        if ctype == "element_appears":
            return _eval_element_appears(condition, backend, ref_store)
        elif ctype == "element_disappears":
            return _eval_element_disappears(condition, backend, ref_store)
        elif ctype == "text_equals":
            return _eval_text_equals(condition, backend, ref_store)
        elif ctype == "state_change":
            return _eval_state_change(condition, backend, ref_store)
        elif ctype == "duration":
            return _eval_duration(condition)
        elif ctype == "window_appears":
            return _eval_window_appears(condition, backend, ref_store)
    except StaleElementReferenceError:
        return json.dumps(
            {
                "error": "stale_element_reference",
                "message": (f"Element reference '{condition.get('ref', '')}' is no longer valid"),
                "ref": condition.get("ref"),
            }
        )
    except ElementNotFoundError:
        # Element not found could be transient (not appeared yet) — keep polling
        return False

    return False


def _resolve_ref(
    ref: str,
    ref_store: "ElementRefStore",
    backend: "DesktopBackend",
) -> tuple[Any, str | None]:
    """Resolve a reference and check staleness.

    Returns (handle, error_json_or_None).
    """
    handle = ref_store.resolve(ref)
    if handle is None:
        return None, json.dumps(
            {
                "error": "element_not_found",
                "message": f"Reference '{ref}' not found in reference store",
                "ref": ref,
            }
        )
    return handle, None


def _eval_element_appears(
    condition: dict,
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
) -> bool | str:
    """element_appears: True when the element is valid in the accessibility tree."""
    ref = condition["ref"]
    handle, err = _resolve_ref(ref, ref_store, backend)
    if err is not None:
        return err
    return backend.is_valid(handle)


def _eval_element_disappears(
    condition: dict,
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
) -> bool | str:
    """element_disappears: True when the element is no longer valid.

    The element must have been valid at some point (resolved from the
    reference store) but is now gone from the accessibility tree.
    """
    ref = condition["ref"]
    handle, err = _resolve_ref(ref, ref_store, backend)
    if err is not None:
        # Ref doesn't exist in the store — that's a fatal error, not disappearance
        return err
    # Element resolved but is no longer valid → disappeared
    return not backend.is_valid(handle)


def _eval_text_equals(
    condition: dict,
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
) -> bool | str:
    """text_equals: True when element text matches the expected value.

    Calls ``perform_action(GET_TEXT)`` directly so that stale references
    are caught as ``StaleElementReferenceError`` rather than silently
    timing out.
    """
    ref = condition["ref"]
    handle, err = _resolve_ref(ref, ref_store, backend)
    if err is not None:
        return err

    text = backend.perform_action(handle, DesktopAction.GET_TEXT) or ""
    operator = condition.get("operator", "equals")
    expected = condition.get("value", "")

    if operator == "equals":
        return text == expected
    elif operator == "contains":
        return expected in text
    elif operator == "not_empty":
        return len(text) > 0

    return False


def _eval_state_change(
    condition: dict,
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
) -> bool | str:
    """state_change: True when element state matches expected value.

    Calls ``get_element_info()`` first so that stale references are
    caught as ``StaleElementReferenceError`` rather than silently
    returning False (which would cause a timeout instead of an error).
    """
    ref = condition["ref"]
    handle, err = _resolve_ref(ref, ref_store, backend)
    if err is not None:
        return err

    info = backend.get_element_info(handle)
    states = info.get("states", {})
    state_key = condition["state"]
    expected = condition.get("value", True)
    actual = states.get(state_key)
    return actual == expected


def _eval_duration(condition: dict) -> bool | str:
    """duration: always returns True.

    The actual waiting is handled by the caller which sleeps for
    ``duration_ms`` before the first evaluation.  Since the sleep has
    already happened by the time this is called, the condition is always met.
    """
    return True


def _eval_window_appears(
    condition: dict,
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
) -> bool | str:
    """window_appears: True when a window with the given title exists.

    Scans all visible windows via ``list_windows()`` + ``get_window_info()``
    and checks for a title match.  Supports two match modes:

    * ``substring`` (default): case-insensitive substring match.
    * ``regex``: full regex pattern match against the window title.

    When a match is found, the window handle is registered in *ref_store*
    with prefix ``w`` and the resulting ``window_ref`` is returned alongside
    the success payload.
    """
    expected_title = condition["title"]
    match_mode = condition.get("match", "substring")
    try:
        windows = backend.list_windows()
    except Exception:
        return False

    for win_handle in windows:
        try:
            info = backend.get_window_info(win_handle)
            title = info.get("title", "")
            if match_mode == "regex":
                if re.search(expected_title, title):
                    window_ref = ref_store.store(win_handle, prefix="w")
                    return _window_appears_success(window_ref, title)
            else:
                if expected_title.lower() in title.lower():
                    window_ref = ref_store.store(win_handle, prefix="w")
                    return _window_appears_success(window_ref, title)
        except Exception:
            continue

    return False


# -- Response helpers --------------------------------------------------------


def _window_appears_success(window_ref: str, matched_title: str) -> str:
    """Build a JSON success payload for window_appears with the window ref."""
    return json.dumps(
        {
            "__window_appears_match": True,
            "window_ref": window_ref,
            "matched_title": matched_title,
        }
    )


def _success_response(
    condition: dict,
    elapsed_ms: int,
    polls: int,
) -> str:
    """Build a success JSON response with safety metadata."""
    # wait_for is READ_ONLY per architecture decision — passive observation,
    # no UI modification.  Use a read-only role for accurate classification.
    element = NormalizedElement(
        ref="",
        backend_id="",
        role="window",
        states=ElementStates(enabled=True),
    )
    assessment = classify(element, "wait_for")

    return json.dumps(
        {
            "success": True,
            "condition": condition,
            "elapsed_ms": elapsed_ms,
            "polls": polls,
            "risk": assessment.risk_level.lower(),
            "target_summary": "wait for condition",
        }
    )

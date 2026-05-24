"""desktop.web_evaluate — execute JavaScript in a browser page context.

Evaluates a JavaScript expression in the context of a connected browser page
using the CDP ``Runtime.evaluate`` command.  Requires an active web connection
established via :func:`~guidewire.tools.web_connect` — the window reference
passed to this tool must be one of the ``w``-prefixed refs returned by
``desktop.web_connect``.

Safety classification: SENSITIVE — executing arbitrary JavaScript is the
highest-risk web tool in Phase 4.  Layered sandboxing includes:

1. SENSITIVE system-action classification (``SYSTEM_ACTION_RISK_MAP``)
2. Sliding-window rate limiting via :class:`~guidewire.safety.EvaluateRateLimiter`
3. Execution timeout passed to ``Runtime.evaluate``
4. Result sanitization via :func:`~guidewire.privacy.redact_web_content`

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~guidewire.backends.web.WebBackend` for session management and
:class:`~guidewire.cdp.domains.runtime.RuntimeDomain` for evaluation.
"""

import json
import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.backends.router import BackendRouter, _untag
from guidewire.cdp._types import CDPTarget
from guidewire.hints import hints_for
from guidewire.privacy import redact_web_content
from guidewire.safety import EvaluateRateLimiter, classify_system_action

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Default execution timeout in seconds.
_DEFAULT_TIMEOUT = 5.0

# Module-level rate limiter — shared across all calls within this process.
_rate_limiter = EvaluateRateLimiter()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_evaluate tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool evaluates a JavaScript expression in the
    browser context.  Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_evaluate")
    def web_evaluate(
        window_ref: str,
        expression: str,
        timeout: float = _DEFAULT_TIMEOUT,
        await_promise: bool = False,
    ) -> str:
        """Execute JavaScript in a connected browser page.

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page to evaluate in.
            expression: JavaScript expression to evaluate.
            timeout: Maximum seconds for the evaluation to complete
                (default 5).  Set to 0 to use the CDP default timeout.
            await_promise: If ``True``, wait for Promise resolution before
                returning.
            Returns:
            A JSON object with ``success``, ``result``, ``type``,
            ``risk``, ``confirmation_required``, and ``target_summary``
            on success, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "result": None,
                    "type": "undefined",
                    "expression": expression,
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

        if not expression or not expression.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "expression must be a non-empty string",
                    "hints": [],
                }
            )

        if timeout < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "timeout must be non-negative",
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("web_evaluate", target=expression[:80])

        # --- Rate limiting ---
        if not _rate_limiter.is_allowed():
            remaining = _rate_limiter.remaining
            return json.dumps(
                {
                    "error": "rate_limited",
                    "message": (
                        "web_evaluate rate limit exceeded — "
                        f"{remaining} calls remaining in current window"
                    ),
                    "hints": hints_for("web_evaluate_error"),
                }
            )

        # --- Resolve the BackendRouter ---
        if not isinstance(backend, BackendRouter):
            return json.dumps(
                {
                    "error": "web_evaluate_error",
                    "message": (
                        "web_evaluate requires a BackendRouter backend — "
                        "the server is not configured for web support"
                    ),
                    "hints": hints_for("web_evaluate_error"),
                }
            )

        web = backend.web
        if web is None:
            return json.dumps(
                {
                    "error": "web_evaluate_error",
                    "message": "No web connection — call desktop.web_connect first",
                    "hints": hints_for("web_evaluate_error"),
                }
            )

        # --- Resolve the window reference to a CDPTarget ---
        tagged_handle = ref_store.resolve(window_ref)
        if tagged_handle is None:
            return json.dumps(
                {
                    "error": "web_evaluate_error",
                    "message": f"Window reference '{window_ref}' not found in ref store",
                    "hints": hints_for("web_evaluate_error"),
                }
            )

        inner, backend_id = _untag(tagged_handle)
        if backend_id != "web":
            return json.dumps(
                {
                    "error": "web_evaluate_error",
                    "message": (
                        f"Window reference '{window_ref}' is not a web window "
                        f"(backend_id={backend_id!r})"
                    ),
                    "hints": hints_for("web_evaluate_error"),
                }
            )

        target = _extract_target(inner)
        if target is None:
            return json.dumps(
                {
                    "error": "web_evaluate_error",
                    "message": (
                        f"Could not resolve window reference '{window_ref}' "
                        "to a CDP target"
                    ),
                    "hints": hints_for("web_evaluate_error"),
                }
            )

        # --- Create session and evaluate ---
        try:
            session = web._get_or_create_session(target.id)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_evaluate_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_evaluate_error"),
                }
            )

        try:
            from guidewire.cdp.domains.runtime import RuntimeDomain

            runtime = RuntimeDomain(session)
            eval_timeout = timeout if timeout > 0 else None
            raw_result = runtime.evaluate(
                expression,
                return_by_value=True,
                await_promise=await_promise,
                timeout=eval_timeout,
            )
        except Exception as exc:
            exc_message = redact_web_content(str(exc))
            return json.dumps(
                {
                    "error": "web_evaluate_error",
                    "message": f"JavaScript evaluation failed: {exc_message}",
                    "hints": hints_for("web_evaluate_error"),
                }
            )

        # --- Sanitize result ---
        result_type = _classify_result_type(raw_result)
        sanitized = _sanitize_result(raw_result)

        return json.dumps(
            {
                "success": True,
                "result": sanitized,
                "type": result_type,
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
                "target_summary": f"evaluate: {expression[:60]}",
            }
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_target(handle: object) -> CDPTarget | None:
    """Extract a CDPTarget from a possibly-wrapped handle.

    Args:
        handle: The handle to extract from.

    Returns:
        A :class:`CDPTarget` instance, or ``None``.
    """
    if isinstance(handle, CDPTarget):
        return handle
    return None


def _classify_result_type(value: object) -> str:
    """Classify the JavaScript result into a type string.

    Args:
        value: The evaluated result value.

    Returns:
        A string type identifier (e.g. ``"string"``, ``"number"``,
        ``"object"``, ``"undefined"``).
    """
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _sanitize_result(value: object) -> object:
    """Sanitize a JavaScript evaluation result for safe return.

    Applies :func:`~guidewire.privacy.redact_web_content` to string results
    and recurses into containers (dicts/lists) to sanitize nested strings.

    Args:
        value: The raw evaluation result.

    Returns:
        The sanitized result.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return redact_web_content(value)
    if isinstance(value, list):
        return [_sanitize_result(item) for item in value]
    if isinstance(value, dict):
        return {k: _sanitize_result(v) for k, v in value.items()}
    return value

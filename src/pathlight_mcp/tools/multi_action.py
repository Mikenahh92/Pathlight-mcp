"""desktop.multi_action — execute a batch of desktop actions in a single call.

Accepts an ordered list of action descriptors, pre-classifies every action for
safety, rejects the entire batch if any action is SENSITIVE-tier (v1 safety
constraint per phase-3-architecture skill), then executes actions sequentially
through existing tool handler logic.

This is a tool-layer composition only — zero ABC changes. Each action in the
batch is dispatched through the existing backend.perform_action() path.

Batch execution semantics:
- Pre-classify ALL actions before executing any.
- Reject entire batch if any action is SENSITIVE-tier.
- Execute sequentially; stop on first failure.
- Return per-action results with a batch-level summary.

Design decisions (GW-065 → GW-066):
- Supported action types in v1: click, type, press_key, get_text, set_value,
  toggle, expand, collapse, select, select_item, deselect_item,
  add_to_selection, increment, decrement, scroll.
- press_key is special: it targets the focused window (no element_ref needed).
- Actions that are not element-based (window management, clipboard, app_launch,
  snapshot, find, scroll_to_item, get_table_info, get_tree_info) are excluded
  from v1 batching — they require dedicated dispatch paths.
"""

import json
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends.types import DesktopAction
from pathlight_mcp.errors import (
    ActionNotSupportedError,
    ElementNotFoundError,
    StaleElementReferenceError,
)
from pathlight_mcp.hints import hints_for
from pathlight_mcp.models import ElementStates, NormalizedElement
from pathlight_mcp.safety import RiskAssessment, RiskLevel, classify, classify_system_action

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore

# -- Supported v1 batch actions -----------------------------------------------

# Actions that target an element (require element_ref).
_ELEMENT_BATCH_ACTIONS: frozenset[DesktopAction] = frozenset(
    {
        DesktopAction.CLICK,
        DesktopAction.TYPE,
        DesktopAction.GET_TEXT,
        DesktopAction.SET_VALUE,
        DesktopAction.TOGGLE,
        DesktopAction.EXPAND,
        DesktopAction.COLLAPSE,
        DesktopAction.SELECT,
        DesktopAction.SELECT_ITEM,
        DesktopAction.DESELECT_ITEM,
        DesktopAction.ADD_TO_SELECTION,
        DesktopAction.INCREMENT,
        DesktopAction.DECREMENT,
        DesktopAction.SCROLL,
    }
)

# press_key targets the focused window, not an element.
_WINDOW_BATCH_ACTIONS: frozenset[DesktopAction] = frozenset(
    {
        DesktopAction.PRESS_KEY,
    }
)

SUPPORTED_BATCH_ACTIONS: frozenset[DesktopAction] = _ELEMENT_BATCH_ACTIONS | _WINDOW_BATCH_ACTIONS

# Minimum batch size per architecture §2.
MIN_BATCH_SIZE = 2

# Maximum batch size to prevent unbounded payloads.
MAX_BATCH_SIZE = 20


# -- Action descriptor validation ---------------------------------------------


def _validate_action_descriptor(
    action: dict[str, Any],
    index: int,
) -> tuple[DesktopAction, str | None, dict[str, Any], str | None]:
    """Validate and normalize a single action descriptor.

    Args:
        action: Raw action dict from the batch request.
        index: Zero-based position in the batch for error messages.

    Returns:
        Tuple of (desktop_action, element_ref, kwargs, error).
        If error is not None the descriptor is invalid.
    """
    action_name = action.get("action")
    if not action_name:
        return (DesktopAction.CLICK, None, {}, f"actions[{index}]: missing 'action' field")

    try:
        desktop_action = DesktopAction(action_name)
    except ValueError:
        return (
            DesktopAction.CLICK,
            None,
            {},
            f"actions[{index}]: unsupported action '{action_name}'",
        )

    if desktop_action not in SUPPORTED_BATCH_ACTIONS:
        return (
            desktop_action,
            None,
            {},
            f"actions[{index}]: action '{action_name}' is not batchable in v1",
        )

    element_ref: str | None = action.get("element_ref")
    kwargs: dict[str, Any] = {}

    # Element-based actions require an element_ref.
    if desktop_action in _ELEMENT_BATCH_ACTIONS:
        if not element_ref or not str(element_ref).strip():
            return (
                desktop_action,
                None,
                {},
                f"actions[{index}]: action '{action_name}' requires an element_ref",
            )
        element_ref = str(element_ref).strip()

        # Extract action-specific kwargs.
        if desktop_action == DesktopAction.TYPE:
            text = action.get("text")
            if text is None:
                return (
                    desktop_action,
                    element_ref,
                    {},
                    f"actions[{index}]: action 'type' requires a 'text' field",
                )
            kwargs["text"] = str(text)
        elif desktop_action == DesktopAction.SET_VALUE:
            value = action.get("value")
            if value is None:
                return (
                    desktop_action,
                    element_ref,
                    {},
                    f"actions[{index}]: action 'set_value' requires a 'value' field",
                )
            kwargs["value"] = value
    else:
        # Window-based actions (press_key).
        element_ref = None
        if desktop_action == DesktopAction.PRESS_KEY:
            keys = action.get("keys")
            if not keys or not str(keys).strip():
                return (
                    desktop_action,
                    None,
                    {},
                    f"actions[{index}]: action 'press_key' requires a 'keys' field",
                )
            kwargs["keys"] = str(keys).strip()

    return (desktop_action, element_ref, kwargs, None)


# -- Safety pre-classification ------------------------------------------------


def _pre_classify_batch(
    actions: list[tuple[DesktopAction, str | None, dict[str, Any]]],
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
) -> tuple[str | None, list[RiskAssessment]]:
    """Pre-classify all actions in the batch for safety.

    Resolves element refs to get real role/name data for meaningful
    classification. Returns a tuple of (error_message_or_None,
    assessments_list).

    For window-level actions (press_key), uses classify_system_action()
    per architecture §3.4. For element-based actions, uses classify().

    The returned assessments are reused for batch-level risk computation
    to avoid redundant re-classification.
    """
    assessments: list[RiskAssessment] = []

    for i, (desktop_action, element_ref, kwargs) in enumerate(actions):
        if desktop_action in _WINDOW_BATCH_ACTIONS:
            # Window-level actions use classify_system_action() per §3.4.
            assessment = classify_system_action(
                "window_manage",  # closest system-action category for key input
                target=f"press_key:{kwargs.get('keys', '')}",
            )
        else:
            # Resolve element to get real role/name for classification.
            handle = ref_store.resolve(element_ref)  # type: ignore[arg-type]
            if handle is not None:
                try:
                    info = backend.get_element_info(handle)
                    element = NormalizedElement(
                        ref=element_ref or "",
                        backend_id=str(handle),
                        role=info.get("role", "element"),
                        name=info.get("name"),
                        states=ElementStates(enabled=True),
                    )
                except Exception:
                    # If we can't get element info, use a generic element.
                    element = NormalizedElement(
                        ref=element_ref or "",
                        backend_id=str(handle),
                        role="element",
                        states=ElementStates(enabled=True),
                    )
            else:
                element = NormalizedElement(
                    ref=element_ref or "",
                    backend_id="",
                    role="element",
                    states=ElementStates(enabled=True),
                )

            assessment = classify(element, desktop_action)

        assessments.append(assessment)
        if assessment.risk_level == "SENSITIVE":
            return (
                f"actions[{i}]: action '{desktop_action.value}' classified as "
                f"SENSITIVE — batch rejected (v1 safety constraint)",
                assessments,
            )

    return (None, assessments)


# -- Single action executor ---------------------------------------------------


def _execute_single_action(
    desktop_action: DesktopAction,
    element_ref: str | None,
    kwargs: dict[str, Any],
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
) -> dict[str, Any]:
    """Execute a single action and return a result dict.

    Returns a dict with at least ``success`` (bool) and either the action
    result data or error information.

    Response schema per architecture §5:
    - ``tool``: the action name (replaces legacy ``action`` field)
    - ``arguments``: nested dict of action arguments (element_ref, text, etc.)
    """
    try:
        if desktop_action in _ELEMENT_BATCH_ACTIONS:
            # Resolve element reference.
            handle = ref_store.resolve(element_ref)  # type: ignore[arg-type]
            if handle is None:
                return {
                    "success": False,
                    "error": "element_not_found",
                    "message": f"Element reference '{element_ref}' not found",
                    "ref": element_ref,
                    "hints": hints_for("element_not_found"),
                }

            # Staleness check.
            if not backend.is_valid(handle):
                return {
                    "success": False,
                    "error": "stale_element_reference",
                    "message": f"Element reference '{element_ref}' is no longer valid",
                    "ref": element_ref,
                    "hints": hints_for("stale_element_reference"),
                }

            # Execute.
            result = backend.perform_action(handle, desktop_action, **kwargs)

            # Build per-action result per architecture §5 schema.
            arguments: dict[str, Any] = {"element_ref": element_ref}
            arguments.update(kwargs)
            action_result: dict[str, Any] = {
                "success": True,
                "tool": desktop_action.value,
                "arguments": arguments,
            }
            # GET_TEXT returns a string value.
            if desktop_action == DesktopAction.GET_TEXT and isinstance(result, str):
                action_result["text"] = result
            return action_result

        elif desktop_action in _WINDOW_BATCH_ACTIONS:
            # press_key: resolve focused window.
            windows = backend.list_windows()
            if not windows:
                return {
                    "success": False,
                    "error": "window_not_found",
                    "message": "No windows available for key press",
                    "hints": hints_for("window_not_found"),
                }
            target = windows[0]

            # Normalise key combo (lowercase, strip spaces).
            keys = kwargs.get("keys", "")
            normalised = "+".join(p.strip().lower() for p in keys.split("+"))

            backend.perform_action(target, desktop_action, keys=normalised)
            return {
                "success": True,
                "tool": desktop_action.value,
                "arguments": {"keys": normalised},
            }

        return {
            "success": False,
            "error": "action_not_supported",
            "message": f"Action '{desktop_action.value}' is not batchable",
            "hints": hints_for("action_not_supported"),
        }

    except ElementNotFoundError as exc:
        return {
            "success": False,
            "error": "element_not_found",
            "message": f"Element not found for action '{desktop_action.value}'",
            "ref": element_ref,
            "hints": exc.hints,
        }
    except StaleElementReferenceError as exc:
        return {
            "success": False,
            "error": "stale_element_reference",
            "message": f"Element reference '{element_ref}' is stale",
            "ref": element_ref,
            "hints": exc.hints,
        }
    except ActionNotSupportedError as exc:
        return {
            "success": False,
            "error": "action_not_supported",
            "message": f"Action '{desktop_action.value}' is not supported",
            "ref": element_ref,
            "hints": exc.hints,
        }


# -- Tool registration --------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.multi_action tool on *mcp*.

    When *backend* is provided the tool validates, pre-classifies, and
    sequentially executes a batch of actions.  Without a backend it returns
    a static stub response.
    """

    @mcp.tool(name="desktop.multi_action")
    def multi_action(actions: list[dict[str, Any]]) -> str:
        """Execute a batch of desktop actions in a single call.

        Accepts an ordered list of action descriptors. Pre-classifies every
        action for safety; rejects the entire batch if any action is
        SENSITIVE-tier. Executes actions sequentially, stopping on first
        failure.

        Supported actions (v1): click, type, press_key, get_text, set_value,
        toggle, expand, collapse, select, select_item, deselect_item,
        add_to_selection, increment, decrement, scroll.

        Args:
            actions: List of action dicts. Each dict must have an ``action``
                field (str) and may require ``element_ref`` (str), ``text``
                (str, for type), ``value`` (any, for set_value), or ``keys``
                (str, for press_key).

        Returns:
            A JSON object with ``success`` (bool), ``actions`` (list of
            per-action results), ``total``, ``completed``, ``failed``, and
            ``batch_aborted`` (index of first failure, or null).
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "actions": [
                        {
                            "success": True,
                            "tool": a.get("action", "unknown"),
                            "arguments": {k: v for k, v in a.items() if k != "action"},
                        }
                        for a in actions
                    ],
                    "total": len(actions),
                    "completed": len(actions),
                    "failed": 0,
                    "batch_aborted": None,
                    "mode": "stub",
                }
            )

        # --- Batch-level validation ---
        if not isinstance(actions, list):
            return json.dumps(
                {
                    "success": False,
                    "error": "validation_error",
                    "message": "'actions' must be a list",
                    "hints": [],
                }
            )

        if len(actions) == 0:
            return json.dumps(
                {
                    "success": False,
                    "error": "validation_error",
                    "message": "'actions' must not be empty",
                    "hints": [],
                }
            )

        if len(actions) < MIN_BATCH_SIZE:
            return json.dumps(
                {
                    "success": False,
                    "error": "validation_error",
                    "message": f"Batch size {len(actions)} is below minimum of {MIN_BATCH_SIZE}",
                    "hints": [],
                }
            )

        if len(actions) > MAX_BATCH_SIZE:
            return json.dumps(
                {
                    "success": False,
                    "error": "validation_error",
                    "message": f"Batch size {len(actions)} exceeds maximum of {MAX_BATCH_SIZE}",
                    "hints": [],
                }
            )

        # --- Per-action descriptor validation ---
        validated: list[tuple[DesktopAction, str | None, dict[str, Any]]] = []
        for i, action in enumerate(actions):
            desktop_action, element_ref, kwargs, error = _validate_action_descriptor(action, i)
            if error is not None:
                return json.dumps(
                    {
                        "success": False,
                        "error": "validation_error",
                        "message": error,
                        "hints": [],
                    }
                )
            validated.append((desktop_action, element_ref, kwargs))

        # --- Safety pre-classification ---
        sensitive_error, assessments = _pre_classify_batch(validated, backend, ref_store)
        if sensitive_error is not None:
            return json.dumps(
                {
                    "success": False,
                    "error": "sensitive_action_rejected",
                    "message": sensitive_error,
                    "hints": [],
                }
            )

        # --- Sequential execution ---
        results: list[dict[str, Any]] = []
        batch_aborted: int | None = None
        for i, (desktop_action, element_ref, kwargs) in enumerate(validated):
            result = _execute_single_action(desktop_action, element_ref, kwargs, backend, ref_store)
            results.append(result)
            if not result.get("success", False):
                batch_aborted = i
                break

        completed = sum(1 for r in results if r.get("success", False))
        failed = sum(1 for r in results if not r.get("success", False))

        # Batch-level risk is the highest individual action risk.
        # Reuse pre-classification assessments instead of recomputing.
        batch_risk: RiskLevel = "READ_ONLY"
        for assessment in assessments:
            if assessment.risk_level == "SENSITIVE":
                batch_risk = "SENSITIVE"
                break
            if assessment.risk_level == "INTERACTION":
                batch_risk = "INTERACTION"

        return json.dumps(
            {
                "success": failed == 0,
                "actions": results,
                "total": len(validated),
                "completed": completed,
                "failed": failed,
                "batch_aborted": batch_aborted,
                "risk": batch_risk.lower(),
            }
        )

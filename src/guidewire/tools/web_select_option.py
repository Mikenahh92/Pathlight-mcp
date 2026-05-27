"""desktop.web_select_option — select an option in a web dropdown element.

Selects an option within a ``<select>`` element on a connected browser page
using CDP commands.  Supports three selection modes:

- **value** — match by ``<option value="...">`` attribute
- **label** — match by visible option text
- **index** — match by zero-based positional index

Requires an active web connection established via
:func:`~guidewire.tools.web_connect`.  The tool locates the ``<select>``
element using either an element reference (``e``-prefixed ref from
``snapshot`` / ``find``) or a CSS selector, then uses
``DOM.resolveNode`` + ``Runtime.callFunctionOn`` to perform the
selection and dispatch the ``change`` event.

Safety classification: SENSITIVE — modifying form state on a web page
is a destructive action that requires explicit user opt-in
(``SYSTEM_ACTION_RISK_MAP`` in :mod:`guidewire.safety`).

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~guidewire.backends.web.WebBackend` for session management,
:class:`~guidewire.cdp.domains.dom.DOMDomain` for element lookup, and
:class:`~guidewire.cdp.domains.runtime.RuntimeDomain` for JavaScript
evaluation.
"""

import json
import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.backends.router import BackendRouter, _untag
from guidewire.cdp._types import CDPTarget
from guidewire.hints import hints_for
from guidewire.safety import classify_system_action

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.cdp.domains.dom import DOMDomain
    from guidewire.cdp.domains.runtime import RuntimeDomain
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_select_option tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool selects an option in a dropdown.  Without
    a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_select_option")
    def web_select_option(
        window_ref: str,
        element_ref: str | None = None,
        selector: str | None = None,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
    ) -> str:
        """Select an option in a web dropdown (``<select>``) element.

        Provide exactly one of ``value``, ``label``, or ``index`` to identify
        which option to select, and exactly one of ``element_ref`` or
        ``selector`` to identify the ``<select>`` element.

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page.
            element_ref: Element reference (``e``-prefixed) from
                ``desktop.snapshot`` or ``desktop.find`` identifying the
                ``<select>`` element.  Mutually exclusive with *selector*.
            selector: CSS selector to locate the ``<select>`` element.
                Mutually exclusive with *element_ref*.
            value: Select the option whose ``value`` attribute matches.
            label: Select the option whose visible text matches.
            index: Select the option at this zero-based position.

        Returns:
            A JSON object with ``success``, ``selected_value``,
            ``selected_label``, ``risk``, ``confirmation_required``, and
            ``target_summary`` on success, or a structured error payload
            on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "selected_value": value or "",
                    "selected_label": label or "",
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

        if element_ref is None and selector is None:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        "Must provide either element_ref or selector to identify the select element"
                    ),
                    "hints": [],
                }
            )

        if element_ref is not None and selector is not None:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        "element_ref and selector are mutually exclusive — provide only one"
                    ),
                    "hints": [],
                }
            )

        selection_modes = sum(1 for v in (value, label, index) if v is not None)
        if selection_modes == 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        "Must provide exactly one of value, label, or index to select an option"
                    ),
                    "hints": [],
                }
            )

        if selection_modes > 1:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        "value, label, and index are mutually exclusive — provide only one"
                    ),
                    "hints": [],
                }
            )

        if index is not None and index < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "index must be non-negative",
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        target_desc = value or label or str(index)
        assessment = classify_system_action(
            "web_select_option",
            target=target_desc,
        )

        # --- Resolve the BackendRouter ---
        if not isinstance(backend, BackendRouter):
            return json.dumps(
                {
                    "error": "web_select_option_error",
                    "message": (
                        "web_select_option requires a BackendRouter backend — "
                        "the server is not configured for web support"
                    ),
                    "hints": hints_for("web_select_option_error"),
                }
            )

        web = backend.web
        if web is None:
            return json.dumps(
                {
                    "error": "web_select_option_error",
                    "message": ("No web connection — call desktop.web_connect first"),
                    "hints": hints_for("web_select_option_error"),
                }
            )

        # --- Resolve the window reference to a CDPTarget ---
        tagged_handle = ref_store.resolve(window_ref)
        if tagged_handle is None:
            return json.dumps(
                {
                    "error": "web_select_option_error",
                    "message": (f"Window reference '{window_ref}' not found in ref store"),
                    "hints": hints_for("web_select_option_error"),
                }
            )

        inner, backend_id = _untag(tagged_handle)
        if backend_id != "web":
            return json.dumps(
                {
                    "error": "web_select_option_error",
                    "message": (
                        f"Window reference '{window_ref}' is not a web window "
                        f"(backend_id={backend_id!r})"
                    ),
                    "hints": hints_for("web_select_option_error"),
                }
            )

        target = _extract_target(inner)
        if target is None:
            return json.dumps(
                {
                    "error": "web_select_option_error",
                    "message": (
                        f"Could not resolve window reference '{window_ref}' to a CDP target"
                    ),
                    "hints": hints_for("web_select_option_error"),
                }
            )

        # --- Create session ---
        try:
            session = web._get_or_create_session(target.id)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_select_option_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_select_option_error"),
                }
            )

        # --- Locate and select ---
        try:
            from guidewire.cdp.domains.dom import DOMDomain
            from guidewire.cdp.domains.runtime import RuntimeDomain

            dom = DOMDomain(session)
            runtime = RuntimeDomain(session)

            # Find node_id via selector or element_ref
            node_id = _find_node_id(
                dom,
                selector,
                element_ref,
                ref_store,
            )
            if node_id is None:
                return json.dumps(
                    {
                        "error": "web_select_option_error",
                        "message": (
                            "Could not locate the select element"
                            + (
                                f" matching selector '{selector}'"
                                if selector
                                else f" for element_ref '{element_ref}'"
                            )
                        ),
                        "hints": hints_for("web_select_option_error"),
                    }
                )

            # Resolve DOM node to a JS remote object
            remote_obj = dom.resolve_node(node_id=node_id)
            object_id = remote_obj.get("objectId")
            if not object_id:
                return json.dumps(
                    {
                        "error": "web_select_option_error",
                        "message": "Failed to resolve DOM node to a remote object",
                        "hints": hints_for("web_select_option_error"),
                    }
                )

            # Perform selection via Runtime.callFunctionOn
            result = _select_option(
                runtime,
                object_id,
                value=value,
                label=label,
                index=index,
            )

        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_select_option_error",
                    "message": f"Select option failed: {exc}",
                    "hints": hints_for("web_select_option_error"),
                }
            )

        return json.dumps(
            {
                "success": True,
                "selected_value": result.get("value", ""),
                "selected_label": result.get("label", ""),
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
                "target_summary": (f"select option value={result.get('value', '')!r} in dropdown"),
            }
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_target(handle: object) -> CDPTarget | None:
    """Extract a CDPTarget from a possibly-wrapped handle."""
    if isinstance(handle, CDPTarget):
        return handle
    return None


def _find_node_id(
    dom: "DOMDomain",
    selector: str | None,
    element_ref: str | None,
    ref_store: "ElementRefStore",
) -> int | None:
    """Find a CDP DOM node ID via CSS selector or element reference.

    Args:
        dom: CDP DOM domain instance.
        selector: CSS selector string (if provided).
        element_ref: ``e``-prefixed element reference (if provided).
        ref_store: The reference store for resolving element refs.

    Returns:
        The CDP node ID, or ``None`` if not found.
    """
    if selector is not None:
        doc = dom.get_document(depth=0)
        node_id = dom.query_selector(doc.node_id, selector)
        return node_id

    # element_ref path
    if element_ref is None:
        return None

    handle = ref_store.resolve(element_ref)
    if handle is None:
        return None

    from guidewire.backends.router import TaggedHandle
    from guidewire.cdp._types import AXNode

    inner = handle
    if isinstance(inner, TaggedHandle):
        inner = inner.inner

    # AXNode has backend_dom_node_id which maps to a DOM node
    if isinstance(inner, AXNode) and inner.backend_dom_node_id:
        try:
            node = dom.describe_node(
                backend_node_id=inner.backend_dom_node_id,
            )
            return node.node_id
        except Exception:
            pass

    return None


def _select_option(
    runtime: "RuntimeDomain",
    object_id: str,
    *,
    value: str | None = None,
    label: str | None = None,
    index: int | None = None,
) -> dict[str, str]:
    """Execute the option selection via Runtime.callFunctionOn.

    Calls a JavaScript function on the remote object representing the
    ``<select>`` element, selects the target option, dispatches a
    ``change`` event, and returns the selected value and label.

    Args:
        runtime: CDP Runtime domain instance.
        object_id: Remote object ID of the ``<select>`` element.
        value: Option value to select (if provided).
        label: Option label to select (if provided).
        index: Option index to select (if provided).

    Returns:
        Dict with ``value`` and ``label`` of the selected option.
    """
    # Build selection mode and target for the JS function
    if value is not None:
        mode = "value"
        target_val = value
    elif label is not None:
        mode = "label"
        target_val = label
    else:
        mode = "index"
        target_val = str(index)

    js_fn = """(select) => {
        if (!select || select.tagName !== 'SELECT') {
            throw new Error('Element is not a <select>');
        }
        const mode = arguments[1];
        const target = arguments[2];
        let selectedOption = null;

        if (mode === 'value') {
            selectedOption = select.querySelector(
                'option[value="' + target + '"]'
            );
            if (selectedOption) select.value = target;
        } else if (mode === 'label') {
            for (const opt of select.options) {
                if (opt.text === target) {
                    selectedOption = opt;
                    select.value = opt.value;
                    break;
                }
            }
        } else if (mode === 'index') {
            const idx = parseInt(target, 10);
            if (idx >= 0 && idx < select.options.length) {
                selectedOption = select.options[idx];
                select.selectedIndex = idx;
            }
        }

        if (!selectedOption) {
            throw new Error('No matching option found');
        }

        select.dispatchEvent(new Event('change', { bubbles: true }));

        return {
            value: select.value,
            label: selectedOption.text
        };
    }"""

    result = runtime.call_function_on(
        js_fn,
        object_id=object_id,
        arguments=[
            {"value": mode},
            {"value": target_val},
        ],
        return_by_value=True,
        await_promise=False,
    )

    if isinstance(result, dict):
        return result
    return {"value": "", "label": ""}

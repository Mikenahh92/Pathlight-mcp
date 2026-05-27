"""desktop.web_upload_files — upload files to a web file input element.

Sets files on an ``<input type="file">`` element on a connected browser page
using the CDP ``DOM.setFileInputFiles`` command.  Supports single and
multiple file uploads.

Requires an active web connection established via
:func:`~guidewire.tools.web_connect`.  The tool locates the file input
element using either an element reference (``e``-prefixed ref from
``snapshot`` / ``find``) or a CSS selector, then sets the file paths
directly on the element via CDP.

Safety classification: SENSITIVE — file upload grants the browser access
to local file system paths, which is a high-risk operation that requires
explicit user opt-in (``SYSTEM_ACTION_RISK_MAP`` in :mod:`guidewire.safety`).

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~guidewire.backends.web.WebBackend` for session management,
:class:`~guidewire.cdp.domains.dom.DOMDomain` for element lookup and
``DOM.setFileInputFiles``, and ``DOM.resolveNode`` for backend node ID
resolution.
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
    """Register the desktop.web_upload_files tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool uploads files to a file input element.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_upload_files")
    def web_upload_files(
        window_ref: str,
        file_paths: list[str],
        element_ref: str | None = None,
        selector: str | None = None,
    ) -> str:
        """Upload files to a web file input (``<input type="file">``) element.

        Provide exactly one of ``element_ref`` or ``selector`` to identify
        the file input element.  Multiple file paths can be provided when
        the input element has the ``multiple`` attribute.

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page.
            file_paths: List of absolute file paths to upload.  At least
                one path is required.
            element_ref: Element reference (``e``-prefixed) from
                ``desktop.snapshot`` or ``desktop.find`` identifying the
                file input element.  Mutually exclusive with *selector*.
            selector: CSS selector to locate the file input element.
                Mutually exclusive with *element_ref*.

        Returns:
            A JSON object with ``success``, ``files_uploaded``,
            ``risk``, ``confirmation_required``, and ``target_summary``
            on success, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "files_uploaded": len(file_paths),
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

        if not file_paths:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "file_paths must be a non-empty list of file paths",
                    "hints": [],
                }
            )

        if element_ref is None and selector is None:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        "Must provide either element_ref or selector "
                        "to identify the file input element"
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

        # --- Safety metadata ---
        target_desc = ", ".join(file_paths[:3])
        if len(file_paths) > 3:
            target_desc += f", ... ({len(file_paths)} files)"
        assessment = classify_system_action(
            "web_upload_files",
            target=target_desc,
        )

        # --- Resolve the BackendRouter ---
        if not isinstance(backend, BackendRouter):
            return json.dumps(
                {
                    "error": "web_upload_files_error",
                    "message": (
                        "web_upload_files requires a BackendRouter backend — "
                        "the server is not configured for web support"
                    ),
                    "hints": hints_for("web_upload_files_error"),
                }
            )

        web = backend.web
        if web is None:
            return json.dumps(
                {
                    "error": "web_upload_files_error",
                    "message": ("No web connection — call desktop.web_connect first"),
                    "hints": hints_for("web_upload_files_error"),
                }
            )

        # --- Resolve the window reference to a CDPTarget ---
        tagged_handle = ref_store.resolve(window_ref)
        if tagged_handle is None:
            return json.dumps(
                {
                    "error": "web_upload_files_error",
                    "message": (f"Window reference '{window_ref}' not found in ref store"),
                    "hints": hints_for("web_upload_files_error"),
                }
            )

        inner, backend_id = _untag(tagged_handle)
        if backend_id != "web":
            return json.dumps(
                {
                    "error": "web_upload_files_error",
                    "message": (
                        f"Window reference '{window_ref}' is not a web window "
                        f"(backend_id={backend_id!r})"
                    ),
                    "hints": hints_for("web_upload_files_error"),
                }
            )

        target = _extract_target(inner)
        if target is None:
            return json.dumps(
                {
                    "error": "web_upload_files_error",
                    "message": (
                        f"Could not resolve window reference '{window_ref}' to a CDP target"
                    ),
                    "hints": hints_for("web_upload_files_error"),
                }
            )

        # --- Create session ---
        try:
            session = web._get_or_create_session(target.id)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_upload_files_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_upload_files_error"),
                }
            )

        # --- Locate and upload ---
        try:
            from guidewire.cdp.domains.dom import DOMDomain

            dom = DOMDomain(session)

            # Find backend_node_id via selector or element_ref
            backend_node_id = _find_backend_node_id(
                dom,
                selector,
                element_ref,
                ref_store,
            )
            if backend_node_id is None:
                return json.dumps(
                    {
                        "error": "web_upload_files_error",
                        "message": (
                            "Could not locate the file input element"
                            + (
                                f" matching selector '{selector}'"
                                if selector
                                else f" for element_ref '{element_ref}'"
                            )
                        ),
                        "hints": hints_for("web_upload_files_error"),
                    }
                )

            # Set files via DOM.setFileInputFiles
            dom.set_file_input_files(
                backend_node_id=backend_node_id,
                files=file_paths,
            )

        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_upload_files_error",
                    "message": f"File upload failed: {exc}",
                    "hints": hints_for("web_upload_files_error"),
                }
            )

        return json.dumps(
            {
                "success": True,
                "files_uploaded": len(file_paths),
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
                "target_summary": (f"upload {len(file_paths)} file(s)"),
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


def _find_backend_node_id(
    dom: "DOMDomain",
    selector: str | None,
    element_ref: str | None,
    ref_store: "ElementRefStore",
) -> int | None:
    """Find a CDP backend DOM node ID via CSS selector or element reference.

    Args:
        dom: CDP DOM domain instance.
        selector: CSS selector string (if provided).
        element_ref: ``e``-prefixed element reference (if provided).
        ref_store: The reference store for resolving element refs.

    Returns:
        The CDP backend DOM node ID, or ``None`` if not found.
    """
    if selector is not None:
        doc = dom.get_document(depth=0)
        node_id = dom.query_selector(doc.node_id, selector)
        if node_id is None:
            return None
        # Get backend_node_id from the found node
        try:
            node = dom.describe_node(node_id=node_id, depth=0)
            return node.backend_node_id or None
        except Exception:
            return None

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

    # AXNode has backend_dom_node_id which maps directly to backendNodeId
    if isinstance(inner, AXNode) and inner.backend_dom_node_id:
        return inner.backend_dom_node_id

    return None

"""desktop.find — find accessibility elements matching criteria within a window.

Resolves a ``w``-prefixed short reference to a native window handle via the
:class:`~pathlight_mcp.refs.ElementRefStore`, validates it through the backend,
calls :meth:`~pathlight_mcp.backends.base.DesktopBackend.find_elements`, normalizes
matches, assigns ``e``-prefixed refs, and returns a structured result with
safety metadata (PRD R5, architecture v2 §6.5).
"""

import json
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.errors import StaleElementReferenceError, WindowNotFoundError
from pathlight_mcp.hints import hints_for
from pathlight_mcp.safety import classify

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.find tool on *mcp*.

    When *backend* is provided the tool resolves *window_ref* through
    *ref_store*, validates the handle, and delegates to
    ``backend.find_elements()``.  Without a backend it returns a static
    stub response.
    """

    @mcp.tool(name="desktop.find")
    def find(
        window_ref: str,
        role: str | None = None,
        name: str | None = None,
    ) -> str:
        """Find accessibility elements matching criteria within a window.

        Args:
            window_ref: Short reference handle for the target window
                (e.g. ``"w1"``).
            role: Normalized role to match (e.g. ``"button"``,
                ``"text_input"``).
            name: Accessible name to match (case-insensitive substring).

        Returns:
            A JSON object with ``elements``, ``count``, ``risk``, and
            ``target_summary`` on success, or a structured error payload
            on failure.
        """
        if backend is None or ref_store is None:
            return "[]"

        # --- Input validation ---
        if not window_ref or not window_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "window_ref must be a non-empty string",
                    "ref": window_ref,
                    "hints": [],
                }
            )

        if not window_ref.startswith("w"):
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (f"window_ref must start with 'w', got '{window_ref}'"),
                    "ref": window_ref,
                    "hints": [],
                }
            )

        # --- Resolve reference ---
        handle = ref_store.resolve(window_ref)

        if handle is None:
            return json.dumps(
                {
                    "error": "window_not_found",
                    "message": (f"Window reference '{window_ref}' not found in reference store"),
                    "ref": window_ref,
                    "hints": hints_for("window_not_found"),
                }
            )

        # --- Staleness check ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Window reference '{window_ref}' is no longer valid"),
                    "ref": window_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Find elements via backend ---
        try:
            handles = backend.find_elements(handle, role=role, name=name)
        except WindowNotFoundError as exc:
            return json.dumps(
                {
                    "error": "window_not_found",
                    "message": (f"Window reference '{window_ref}' is no longer valid"),
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )
        except StaleElementReferenceError as exc:
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Window reference '{window_ref}' is stale"),
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )

        # --- Build element info lookup from snapshot tree ---
        # snapshot() returns a tree whose leaf refs match the handles from
        # find_elements(), so we walk it once to extract name/role per handle.
        info_map: dict[str, dict[str, str]] = {}
        try:
            tree = backend.snapshot(handle, max_depth=10, max_nodes=10000)
            _walk_tree(tree, info_map)
        except (WindowNotFoundError, StaleElementReferenceError):
            pass  # fall back to filter-level role and no name

        # --- Build response with e-prefixed refs and safety metadata ---
        from pathlight_mcp.models import ElementStates, NormalizedElement

        elements: list[dict[str, object]] = []
        max_risk = "read_only"
        role_counts: dict[str, int] = {}
        for h in handles:
            ref = ref_store.store(h, prefix="e")
            info = info_map.get(str(h), {})
            elem_role = info.get("role", role or "unknown")
            elem_name = info.get("name")

            # Build a NormalizedElement for safety classification
            element = NormalizedElement(
                ref=ref,
                backend_id=str(h),
                role=elem_role,
                name=elem_name,
                states=ElementStates(enabled=True),
            )
            assessment = classify(element, "find")
            risk = assessment.risk_level.lower()

            if risk == "sensitive":
                max_risk = "sensitive"
            elif risk == "interaction" and max_risk != "sensitive":
                max_risk = "interaction"

            entry: dict[str, object] = {"ref": ref, "role": elem_role, "risk": risk}
            if elem_name is not None:
                entry["name"] = elem_name
            elements.append(entry)
            role_counts[elem_role] = role_counts.get(elem_role, 0) + 1

        # --- Build dynamic target_summary (architecture §5.4) ---
        target_summary = _build_target_summary(role_counts, len(elements))

        return json.dumps(
            {
                "success": True,
                "elements": elements,
                "count": len(elements),
                "risk": max_risk,
                "target_summary": target_summary,
            }
        )


# -- Helpers -----------------------------------------------------------------


def _walk_tree(
    node: dict[str, Any],
    info_map: dict[str, dict[str, str]],
) -> None:
    """Recursively walk a snapshot tree and collect handle → {role, name}."""
    ref = node.get("ref")
    if ref is not None:
        key = str(ref)
        info_map[key] = {
            "role": node.get("role", "unknown"),
            "name": node.get("name") or None,
        }
    for child in node.get("children", []):
        _walk_tree(child, info_map)


def _pluralize(role: str) -> str:
    """Return a simple plural form for common accessibility roles."""
    if role.endswith("y") and role not in ("display", "directory"):
        return role[:-1] + "ies"
    if role in ("button",):
        return role + "s"
    return role + "s"


def _build_target_summary(role_counts: dict[str, int], total: int) -> str:
    """Build a dynamic target_summary string (architecture §5.4).

    Format: ``"{count} {role_plural} in window"``.
    When multiple roles are present, the dominant role is used with
    the total count.
    """
    if total == 0:
        return "no elements in window"
    # Pick the role with the highest count
    dominant_role = max(role_counts, key=role_counts.get)  # type: ignore[arg-type]
    return f"{total} {_pluralize(dominant_role)} in window"

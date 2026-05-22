"""desktop.get_table_info — read table/grid data from a desktop element.

Resolves an ``e``-prefixed short reference to a native handle via the
:class:`~guidewire.refs.ElementRefStore`, validates it through the backend,
and delegates to ``backend.perform_action(handle, DesktopAction.GET_TABLE_INFO,
table_action=..., **kwargs)`` to retrieve structured table data (dimensions,
headers, cell contents) via UIA GridPattern (Windows) or AT-SPI Table
interface (Linux).

This is a read-only operation — no modifications are made to the target
element or its contents (GW-049).
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.backends.types import DesktopAction
from guidewire.errors import (
    ActionNotSupportedError,
    ElementNotFoundError,
    StaleElementReferenceError,
)
from guidewire.hints import hints_for
from guidewire.models import ElementStates, NormalizedElement
from guidewire.safety import classify

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

_VALID_TABLE_ACTIONS = frozenset({"info", "read_cell", "read_row", "read_column"})


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.get_table_info tool on *mcp*.

    When *backend* is provided the tool resolves *element_ref* through
    *ref_store*, validates the handle, and delegates to
    ``backend.perform_action(handle, DesktopAction.GET_TABLE_INFO, ...)`` for
    structured table/grid data.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.get_table_info")
    def get_table_info(
        element_ref: str,
        action: str = "info",
        max_rows: int = 100,
        max_columns: int = 50,
        row: int = 0,
        column: int = 0,
    ) -> str:
        """Read table/grid data (dimensions, headers, cells) from a desktop element.

        Retrieves structured table data from elements that support a
        grid/table accessibility pattern. Supports four sub-commands via
        the *action* parameter:

        - ``info`` (default): Returns row/column counts, headers, and cell data.
        - ``read_cell``: Returns a single cell at the given *row* and *column*.
        - ``read_row``: Returns all cells in the given *row*.
        - ``read_column``: Returns all cells in the given *column*.

        Read-only — does not modify the table.

        Args:
            element_ref: Short reference handle for the target table/grid
                element (e.g. ``"e1"``).
            action: Sub-command to perform. One of ``info``, ``read_cell``,
                ``read_row``, ``read_column`` (default ``info``).
            max_rows: Maximum number of data rows to return (default 100).
                Only used with ``info`` action.
            max_columns: Maximum number of columns to return (default 50).
                Only used with ``info`` action.
            row: Zero-based row index for ``read_cell`` / ``read_row``
                (default 0).
            column: Zero-based column index for ``read_cell`` / ``read_column``
                (default 0).

        Returns:
            A JSON object with ``success``, ``ref``, and action-specific
            data fields, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "element_ref": element_ref,
                    "row_count": 0,
                    "column_count": 0,
                    "headers": [],
                    "rows": [],
                }
            )

        # --- Input validation ---
        if not element_ref or not element_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "element_ref must be a non-empty string",
                    "ref": element_ref,
                    "hints": [],
                }
            )

        if action not in _VALID_TABLE_ACTIONS:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        f"action must be one of {sorted(_VALID_TABLE_ACTIONS)}, got '{action}'"
                    ),
                    "ref": element_ref,
                    "hints": [],
                }
            )

        if max_rows < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "max_rows must be a non-negative integer",
                    "ref": element_ref,
                    "hints": [],
                }
            )

        if max_columns < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "max_columns must be a non-negative integer",
                    "ref": element_ref,
                    "hints": [],
                }
            )

        if row < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "row must be a non-negative integer",
                    "ref": element_ref,
                    "hints": [],
                }
            )

        if column < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "column must be a non-negative integer",
                    "ref": element_ref,
                    "hints": [],
                }
            )

        # --- Resolve reference ---
        handle = ref_store.resolve(element_ref)

        if handle is None:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (f"Element reference '{element_ref}' not found in reference store"),
                    "ref": element_ref,
                    "hints": hints_for("element_not_found"),
                }
            )

        # --- Staleness check ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Element reference '{element_ref}' is no longer valid"),
                    "ref": element_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Dispatch through perform_action ---
        try:
            result = backend.perform_action(
                handle,
                DesktopAction.GET_TABLE_INFO,
                table_action=action,
                max_rows=max_rows,
                max_columns=max_columns,
                row=row,
                column=column,
            )
        except ElementNotFoundError as exc:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (
                        f"Element reference '{element_ref}' not found in accessibility tree"
                    ),
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )
        except StaleElementReferenceError as exc:
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": f"Element reference '{element_ref}' is stale",
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )
        except ActionNotSupportedError as exc:
            return json.dumps(
                {
                    "error": "action_not_supported",
                    "message": (f"Element '{element_ref}' does not support table/grid access"),
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )

        # --- Retrieve element metadata for safety classification ---
        try:
            info = backend.get_element_info(handle)
            element_role = info["role"]
            element_name = info.get("name")
            backend_states = info.get("states", {})
        except Exception:
            element_role = "table"
            element_name = None
            backend_states = {}

        element = NormalizedElement(
            ref=element_ref,
            backend_id=str(handle),
            role=element_role,
            name=element_name,
            states=ElementStates(
                enabled=backend_states.get("enabled", True),
            ),
        )

        # --- Safety metadata ---
        assessment = classify(element, "get_table_info")

        # --- Build response ---
        response: dict[str, object] = {
            "success": True,
            "ref": element_ref,
            "action": action,
            "risk": assessment.risk_level.lower(),
            "target_summary": "table data retrieval",
        }

        if isinstance(result, dict):
            response.update(result)
        elif result is not None:
            response["data"] = result

        if element_name is not None:
            response["name"] = element_name

        return json.dumps(response)

"""desktop.list_windows — discovers visible windows and assigns w-prefixed refs.

Maps to PRD R2 / architecture v2 §3.3 — the entry-point tool that lets agents
begin desktop workflows by listing all visible top-level windows.

Each window receives a compact ``w``-prefixed reference (e.g. ``"w1"``, ``"w2"``)
via :class:`~pathlight_mcp.refs.ElementRefStore` so that downstream tools can address
windows by short string refs instead of opaque native handles.

Returns a wrapped dict response (architecture v2 §3.3):

    { "windows": [...], "count": N, "risk": "read", "target_summary": "desktop windows" }
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.errors import BackendUnavailableError

logger = logging.getLogger(__name__)


def register(mcp: FastMCP, **kwargs: object) -> None:
    """Register the desktop.list_windows tool on *mcp*.

    Accepts ``backend`` and ``ref_store`` via ``**kwargs`` for use with the
    ``register_all`` dispatch helper.
    """

    backend = kwargs.get("backend")
    ref_store = kwargs.get("ref_store")

    if backend is None:
        # Stub mode — no backend wired yet.
        @mcp.tool(name="desktop.list_windows")
        def list_windows() -> dict[str, Any]:
            """List all visible top-level desktop windows.

            Returns:
                A wrapped dict with ``windows``, ``count``, ``risk``, and
                ``target_summary`` fields.
            """
            return _build_response([])

        return

    # Wired mode — backend available.
    @mcp.tool(name="desktop.list_windows")
    def list_windows() -> dict[str, Any]:
        """List all visible top-level desktop windows.

        Returns:
            A wrapped dict with ``windows``, ``count``, ``risk``, and
            ``target_summary`` fields.  Each window entry includes ``ref``,
            ``title``, ``app_name``, ``focused``, and optionally ``bounds``.
        """
        try:
            handles = backend.list_windows()
        except BackendUnavailableError:
            raise

        store = ref_store
        windows: list[dict[str, Any]] = []

        for handle in handles:
            try:
                info = backend.get_window_info(handle)
            except Exception:
                # Stale window — skip gracefully, continue with remaining.
                logger.debug("Skipping stale window handle %r", handle)
                continue

            ref = store.store(handle, prefix="w")
            entry: dict[str, Any] = {
                "ref": ref,
                "title": info["title"],
                "app_name": info["app_name"],
                "focused": info["focused"],
            }

            bounds = info.get("bounds")
            if bounds is not None:
                entry["bounds"] = bounds

            windows.append(entry)

        return _build_response(windows)


def _build_response(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the wrapped response dict (architecture v2 §3.3, Epic §9.4)."""
    return {
        "windows": windows,
        "count": len(windows),
        "risk": "read",
        "target_summary": "desktop windows",
    }

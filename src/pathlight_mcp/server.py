"""Pathlight MCP server — wires the FastMCP server with all tool stubs.

Provides :class:`PathlightMCPServer` which wraps :class:`~mcp.server.fastmcp.FastMCP`
with :meth:`register_tools`, :meth:`register_resources`, and :meth:`run` methods
(architecture v2 §2.2).

PRD R1: agents can discover Pathlight MCP's tools via ``tools/list`` and
invoke them through stdio transport.  GW-113: agents can discover contextual
documentation via ``resources/list`` and ``resources/read``.
"""

import asyncio

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends.base import DesktopBackend
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.resources import register_all as register_all_resources
from pathlight_mcp.tools import register_all

__all__ = ["PathlightMCPServer"]


class PathlightMCPServer:
    """Pathlight MCP server wrapping FastMCP with tool registration and run.

    Architecture v2 §2.2: ``PathlightMCPServer`` is the single entry-point for
    creating, configuring, and launching the Pathlight MCP server.
    """

    def __init__(
        self,
        backend: DesktopBackend | None = None,
        ref_store: ElementRefStore | None = None,
    ) -> None:
        self._mcp = FastMCP(
            name="pathlight_mcp",
            instructions=(
                "Pathlight MCP Desktop Accessibility MCP — provides tools for "
                "inspecting and interacting with desktop application UIs via "
                "OS accessibility APIs."
            ),
        )
        self._backend = backend
        self._ref_store = ref_store or ElementRefStore()

    @property
    def mcp(self) -> FastMCP:
        """The underlying FastMCP instance."""
        return self._mcp

    @property
    def backend(self) -> DesktopBackend | None:
        """The platform backend (``None`` when running in stub mode)."""
        return self._backend

    @property
    def ref_store(self) -> ElementRefStore:
        """The element reference store for resolving short references."""
        return self._ref_store

    def register_tools(self) -> None:
        """Register all tools on the MCP server."""
        register_all(self._mcp, backend=self._backend, ref_store=self._ref_store)

    def register_resources(self) -> None:
        """Register all resources on the MCP server.

        Resources provide contextual documentation that agents can discover
        and read at runtime via the MCP ``resources/list`` and
        ``resources/read`` endpoints (GW-113).
        """
        register_all_resources(self._mcp)

    def run(self) -> None:
        """Run the server with stdio transport (blocking)."""
        asyncio.run(self._mcp.run_stdio_async())

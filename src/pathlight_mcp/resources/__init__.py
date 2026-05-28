"""MCP resource handlers for the Pathlight MCP server.

Provides contextual documentation that agents can discover and read at
runtime via the MCP ``resources/list`` and ``resources/read`` endpoints.

Each sub-module registers one or more resources on a
:class:`~mcp.server.fastmcp.FastMCP` instance.

Resource set:

    pathlight-mcp://browser-limitations  — browser/web tool limitations and caveats
    pathlight-mcp://tool-usage           — tool usage guide with tips per category
    pathlight-mcp://error-recovery       — error codes and recovery strategies
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

__all__ = ["register_all"]

# Each resource module lives in its own file; add new resources here.
_RESOURCE_MODULES = [
    ".browser_limitations",
    ".tool_usage",
    ".error_recovery",
]


def register_all(mcp: "FastMCP") -> None:
    """Register every resource on *mcp*.

    Args:
        mcp: The FastMCP instance to register resources on.
    """
    for module_name in _RESOURCE_MODULES:
        mod = importlib.import_module(module_name, package="pathlight_mcp.resources")
        mod.register(mcp)

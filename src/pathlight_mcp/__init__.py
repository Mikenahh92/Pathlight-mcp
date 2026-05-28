"""Pathlight MCP — Desktop Accessibility MCP server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pathlight-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0"

"""Guidewire — Desktop Accessibility MCP server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("guidewire")
except PackageNotFoundError:
    __version__ = "0.0.0"

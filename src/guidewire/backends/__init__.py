"""Backend abstraction layer for the Guidewire Desktop Accessibility MCP server.

This package defines the :class:`DesktopBackend` abstract base class that every
platform backend must implement.  A :class:`MockBackend` test double is provided
for unit testing without a real platform backend.

Public API re-exports:
    DesktopBackend  — ABC with 16 canonical synchronous methods (§4.1)
    MockBackend     — in-memory test double with fluent builder API (§5)
    BackendRouter   — transparent routing layer for multi-backend setups (GW-097)
    TaggedHandle    — handle wrapper that carries backend origin (GW-097)
    NativeHandle    — opaque platform handle alias (§3)
    ElementState    — 9 boolean state flags (§3.2)
    ElementBounds   — bounding rectangle dataclass (§3)
    DesktopAction   — StrEnum of 16 supported actions (§4.3)
"""

import sys

from guidewire.backends.base import DesktopBackend
from guidewire.backends.linux import LinuxBackend
from guidewire.backends.mock import MockBackend
from guidewire.backends.router import BackendRouter, TaggedHandle
from guidewire.backends.types import (
    DesktopAction,
    ElementBounds,
    ElementState,
    NativeHandle,
)
from guidewire.backends.web import WebBackend

if sys.platform == "win32":
    from guidewire.backends.windows import WindowsBackend
else:
    WindowsBackend = None  # type: ignore[assignment,misc]

__all__ = [
    "BackendRouter",
    "DesktopAction",
    "DesktopBackend",
    "ElementBounds",
    "ElementState",
    "LinuxBackend",
    "MockBackend",
    "NativeHandle",
    "TaggedHandle",
    "WebBackend",
    "WindowsBackend",
]

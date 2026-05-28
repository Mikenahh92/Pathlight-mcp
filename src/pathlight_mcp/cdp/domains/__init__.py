"""CDP domain wrappers — typed Python methods for CDP domains.

Provides high-level, typed wrappers for the critical CDP domains:

- :class:`AccessibilityDomain` — query the browser accessibility tree
- :class:`DOMDomain` — query and manipulate the DOM tree
- :class:`RuntimeDomain` — evaluate JavaScript and inspect objects
- :class:`InputDomain` — dispatch mouse and keyboard events
- :class:`PageDomain` — control page navigation and lifecycle
- :class:`TargetDomain` — discover and manage browser targets

Each domain wrapper holds a :class:`~pathlight_mcp.cdp.session.CDPSession`
reference and translates raw CDP JSON into Pathlight MCP's typed model.
"""

from pathlight_mcp.cdp.domains._base import CDPDomain
from pathlight_mcp.cdp.domains.accessibility import AccessibilityDomain
from pathlight_mcp.cdp.domains.dom import DOMDomain
from pathlight_mcp.cdp.domains.input import InputDomain
from pathlight_mcp.cdp.domains.page import PageDomain
from pathlight_mcp.cdp.domains.runtime import RuntimeDomain
from pathlight_mcp.cdp.domains.target import TargetDomain

__all__ = [
    "AccessibilityDomain",
    "CDPDomain",
    "DOMDomain",
    "InputDomain",
    "PageDomain",
    "RuntimeDomain",
    "TargetDomain",
]

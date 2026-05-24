"""CDP domain wrappers — typed Python methods for CDP domains.

Provides high-level, typed wrappers for the critical CDP domains:

- :class:`AccessibilityDomain` — query the browser accessibility tree
- :class:`DOMDomain` — query and manipulate the DOM tree
- :class:`RuntimeDomain` — evaluate JavaScript and inspect objects
- :class:`InputDomain` — dispatch mouse and keyboard events
- :class:`PageDomain` — control page navigation and lifecycle
- :class:`TargetDomain` — discover and manage browser targets

Each domain wrapper holds a :class:`~guidewire.cdp.session.CDPSession`
reference and translates raw CDP JSON into Guidewire's typed model.
"""

from guidewire.cdp.domains._base import CDPDomain
from guidewire.cdp.domains.accessibility import AccessibilityDomain
from guidewire.cdp.domains.dom import DOMDomain
from guidewire.cdp.domains.input import InputDomain
from guidewire.cdp.domains.page import PageDomain
from guidewire.cdp.domains.runtime import RuntimeDomain
from guidewire.cdp.domains.target import TargetDomain

__all__ = [
    "AccessibilityDomain",
    "CDPDomain",
    "DOMDomain",
    "InputDomain",
    "PageDomain",
    "RuntimeDomain",
    "TargetDomain",
]

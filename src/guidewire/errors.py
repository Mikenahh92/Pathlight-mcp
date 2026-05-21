"""Structured error codes for the Guidewire Desktop Accessibility MCP server.

Every failure mode exposed by the MCP layer maps to a distinct, catchable
exception class carrying a machine-readable ``error_code`` string.  This allows
callers (and MCP clients) to programmatically distinguish error categories
without parsing free-text messages.

The six codes below are drawn from PRD §25 (Error Model).

Error hints
-----------
Every error instance carries a ``hints`` list of actionable recovery
suggestions that are **auto-populated** from the hint registry at
construction time.  Additional hints can be added via the
:meth:`with_hints` builder.  The :func:`~guidewire.hints.hints_for`
function looks up registered default hints from the standalone
:mod:`guidewire.hints` module.
"""

from guidewire.hints import hints_for, register_hints  # noqa: F401 — re-export


class GuidewireError(Exception):
    """Base exception for all Guidewire errors.

    Attributes:
        error_code: Machine-readable error identifier (e.g. ``"backend_unavailable"``).
        message: Human-readable description of the failure.
        hints: List of actionable recovery suggestions for the caller,
               auto-populated from the hint registry at construction time.
    """

    error_code: str = "guidewire_error"

    def __init__(self, message: str = "") -> None:
        self.message = message or self.__doc__ or ""
        self.hints: list[str] = hints_for(self.error_code)
        super().__init__(self.message)

    def with_hints(self, *hints: str) -> "GuidewireError":
        """Return *self* with additional hints appended.

        This is a mutating builder — it modifies ``self.hints`` in-place and
        returns ``self`` so callers can chain or re-raise in one expression::

            raise ElementNotFoundError("button").with_hints(
                "Try snapshot first to discover available elements",
            )
        """
        self.hints.extend(hints)
        return self


class BackendUnavailableError(GuidewireError):
    """The platform accessibility backend could not be initialized or is unreachable."""

    error_code = "backend_unavailable"


class ElementNotFoundError(GuidewireError):
    """The requested UI element could not be located."""

    error_code = "element_not_found"


class StaleElementReferenceError(GuidewireError):
    """The referenced element no longer exists in the accessibility tree."""

    error_code = "stale_element_reference"


class ActionNotSupportedError(GuidewireError):
    """The requested action is not supported by the target element."""

    error_code = "action_not_supported"


class PermissionRequiredError(GuidewireError):
    """OS-level accessibility permission is required but has not been granted."""

    error_code = "permission_required"


class AmbiguousSelectorError(GuidewireError):
    """The selector matched multiple elements instead of a single target."""

    error_code = "ambiguous_selector"


class WindowNotFoundError(GuidewireError):
    """The specified window could not be found or no longer exists."""

    error_code = "window_not_found"


__all__ = [
    "ActionNotSupportedError",
    "AmbiguousSelectorError",
    "BackendUnavailableError",
    "ElementNotFoundError",
    "GuidewireError",
    "PermissionRequiredError",
    "StaleElementReferenceError",
    "WindowNotFoundError",
    "hints_for",
    "register_hints",
]

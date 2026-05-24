"""CDP error mapping — translates CDP protocol errors to Guidewire exceptions.

Provides :func:`map_cdp_error` which maps CDP error codes and messages to
the appropriate :class:`~guidewire.errors.GuidewireError` subclass, used by
all CDP domain wrappers via :class:`~guidewire.cdp.domains._base.CDPDomain`.

Error mappings:

- ``-32000`` + ``"not found"`` → :class:`ElementNotFoundError`
- ``-32000`` + ``"not attached"`` → :class:`StaleElementReferenceError`
- ``-32000`` + ``"unsupported"`` → :class:`ActionNotSupportedError`
- ``-32000`` (other) → :class:`GuidewireError`
- ``-32601`` (any) → :class:`BackendUnavailableError`
- ``-32602`` (any) → :class:`GuidewireError`
- (other) (any) → :class:`GuidewireError`
"""

from __future__ import annotations

from guidewire.cdp.protocol import CDPError
from guidewire.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
    ElementNotFoundError,
    GuidewireError,
    StaleElementReferenceError,
)

__all__ = ["map_cdp_error"]


def map_cdp_error(exc: CDPError) -> GuidewireError:
    """Map a :class:`CDPError` to the appropriate :class:`GuidewireError`.

    Args:
        exc: The raw CDP protocol error.

    Returns:
        A :class:`~guidewire.errors.GuidewireError` subclass instance.
    """
    code = exc.code
    message = exc.message.lower()

    if code == -32000:
        if "not found" in message:
            return ElementNotFoundError(exc.message)
        if "not attached" in message:
            return StaleElementReferenceError(exc.message)
        if "unsupported" in message or "not supported" in message:
            return ActionNotSupportedError(exc.message)
        return GuidewireError(f"CDP error {code}: {exc.message}")

    if code == -32601:
        return BackendUnavailableError(exc.message)

    if code == -32602:
        return GuidewireError(f"CDP error {code}: {exc.message}")

    return GuidewireError(f"CDP error {code}: {exc.message}")

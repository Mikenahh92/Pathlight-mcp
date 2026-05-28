"""CDP error mapping — translates CDP protocol errors to Pathlight MCP exceptions.

Provides :func:`map_cdp_error` which maps CDP error codes and messages to
the appropriate :class:`~pathlight_mcp.errors.PathlightMCPError` subclass, used by
all CDP domain wrappers via :class:`~pathlight_mcp.cdp.domains._base.CDPDomain`.

Error mappings:

- ``-32000`` + ``"not found"`` → :class:`ElementNotFoundError`
- ``-32000`` + ``"not attached"`` → :class:`StaleElementReferenceError`
- ``-32000`` + ``"unsupported"`` → :class:`ActionNotSupportedError`
- ``-32000`` (other) → :class:`PathlightMCPError`
- ``-32601`` (any) → :class:`BackendUnavailableError`
- ``-32602`` (any) → :class:`PathlightMCPError`
- (other) (any) → :class:`PathlightMCPError`
"""

from __future__ import annotations

from pathlight_mcp.cdp.protocol import CDPError
from pathlight_mcp.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
    ElementNotFoundError,
    PathlightMCPError,
    StaleElementReferenceError,
)

__all__ = ["map_cdp_error"]


def map_cdp_error(exc: CDPError) -> PathlightMCPError:
    """Map a :class:`CDPError` to the appropriate :class:`PathlightMCPError`.

    Args:
        exc: The raw CDP protocol error.

    Returns:
        A :class:`~pathlight_mcp.errors.PathlightMCPError` subclass instance.
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
        return PathlightMCPError(f"CDP error {code}: {exc.message}")

    if code == -32601:
        return BackendUnavailableError(exc.message)

    if code == -32602:
        return PathlightMCPError(f"CDP error {code}: {exc.message}")

    return PathlightMCPError(f"CDP error {code}: {exc.message}")

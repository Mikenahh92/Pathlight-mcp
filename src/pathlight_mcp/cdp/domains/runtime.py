"""CDP Runtime domain wrapper.

Provides :class:`RuntimeDomain` — typed methods for the CDP ``Runtime``
domain that evaluate JavaScript expressions, call functions on remote
objects, and inspect properties.

Key methods:
    - :meth:`evaluate` — evaluate a JavaScript expression
    - :meth:`call_function_on` — call a function on a remote object
    - :meth:`get_properties` — inspect an object's properties
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pathlight_mcp.cdp.domains._base import CDPDomain
from pathlight_mcp.errors import PathlightMCPError

if TYPE_CHECKING:
    from pathlight_mcp.cdp.session import CDPSession

__all__ = ["RuntimeDomain"]

logger = logging.getLogger(__name__)


class RuntimeDomain(CDPDomain):
    """Typed wrapper for the CDP ``Runtime`` domain.

    Evaluates JavaScript in the browser context and returns typed results.

    Args:
        session: The active CDP session to send commands through.
    """

    domain = "Runtime"

    def __init__(self, session: CDPSession) -> None:
        super().__init__(session)
        self._enabled: bool = False

    def evaluate(
        self,
        expression: str,
        *,
        return_by_value: bool = True,
        await_promise: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """Evaluate a JavaScript expression.

        Sends ``Runtime.evaluate``.

        Args:
            expression: JavaScript expression to evaluate.
            return_by_value: If ``True`` (default), return the value
                directly rather than a remote object handle.
            await_promise: If ``True``, wait for the result to resolve
                if it is a Promise.
            timeout: Per-command timeout in seconds.

        Returns:
            The evaluated value (type depends on the expression).

        Raises:
            PathlightMCPError: If the expression throws an exception.
        """
        # Ensure Runtime.enable has been called before evaluating (GW-115).
        # Without this, auto-launched browsers may hang on Runtime.evaluate
        # because the browser hasn't started the execution context listener.
        if not self._enabled:
            self.enable()

        params: dict[str, Any] = {
            "expression": expression,
            "returnByValue": return_by_value,
            "awaitPromise": await_promise,
        }

        result = self._send(self._method("evaluate"), params, timeout=timeout)

        # Check for exception details
        exception_details = result.get("exceptionDetails")
        if exception_details:
            exc_text = exception_details.get("text", "Unknown JS exception")
            exc_descr = exception_details.get("exception", {}).get("description", "")
            msg = f"{exc_text}: {exc_descr}" if exc_descr else exc_text
            raise PathlightMCPError(msg)

        remote_object = result.get("result", {})
        if return_by_value:
            return remote_object.get("value")
        return remote_object

    def call_function_on(
        self,
        function_declaration: str,
        *,
        object_id: str | None = None,
        arguments: list[dict[str, Any]] | None = None,
        return_by_value: bool = True,
        await_promise: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """Call a function on a remote object or global scope.

        Sends ``Runtime.callFunctionOn``.

        Args:
            function_declaration: JavaScript function declaration.
            object_id: Remote object ID to call on (``None`` for global).
            arguments: Arguments to pass to the function.
            return_by_value: If ``True``, return the value directly.
            await_promise: If ``True``, await Promise resolution.
            timeout: Per-command timeout in seconds.

        Returns:
            The function's return value.

        Raises:
            PathlightMCPError: If the function throws.
            ValueError: If neither object_id nor execution_context_id is set.
        """
        params: dict[str, Any] = {
            "functionDeclaration": function_declaration,
            "returnByValue": return_by_value,
            "awaitPromise": await_promise,
        }
        if object_id is not None:
            params["objectId"] = object_id
        if arguments is not None:
            params["arguments"] = arguments

        result = self._send(self._method("callFunctionOn"), params, timeout=timeout)

        exception_details = result.get("exceptionDetails")
        if exception_details:
            exc_text = exception_details.get("text", "Unknown JS exception")
            exc_descr = exception_details.get("exception", {}).get("description", "")
            msg = f"{exc_text}: {exc_descr}" if exc_descr else exc_text
            raise PathlightMCPError(msg)

        remote_object = result.get("result", {})
        if return_by_value:
            return remote_object.get("value")
        return remote_object

    def get_properties(
        self,
        object_id: str,
        *,
        own_properties: bool = True,
    ) -> dict[str, Any]:
        """Get the properties of a remote object.

        Sends ``Runtime.getProperties``.

        Args:
            object_id: Remote object ID to inspect.
            own_properties: If ``True`` (default), return only own
                properties (not inherited).

        Returns:
            Dict mapping property names to their remote object descriptors.
        """
        params: dict[str, Any] = {
            "objectId": object_id,
            "ownProperties": own_properties,
        }

        result = self._send(self._method("getProperties"), params)
        properties = {}
        for prop in result.get("result", []):
            name = prop.get("name", "")
            if name and not name.startswith("__"):
                properties[name] = prop.get("value", {})
        return properties

    def enable(self) -> None:
        """Enable the Runtime domain.

        Sends ``Runtime.enable`` to start receiving execution context
        and console API events.
        """
        self._send(self._method("enable"))
        self._enabled = True

    def disable(self) -> None:
        """Disable the Runtime domain.

        Sends ``Runtime.disable``.
        """
        self._send(self._method("disable"))
        self._enabled = False

"""Pathlight MCP server subprocess manager.

Spawns ``python -m pathlight_mcp`` as a child process, connects an MCP
``ClientSession`` over stdio transport, and provides clean shutdown.

Usage::

    async with PathlightMCPServerProcess() as server:
        tools = await server.list_tools()
        assert len(tools) == 9
"""

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, ListToolsResult, Tool

__all__ = ["PathlightMCPServerProcess"]

# Resolve the project source root relative to this file (tests/harness/server.py
# → tests/ → project root → src/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SRC_DIR = str(_PROJECT_ROOT / "src")


@dataclass
class PathlightMCPServerProcess:
    """Manages a Pathlight MCP server subprocess lifecycle.

    Spawns the server via ``python -m pathlight_mcp``, connects an MCP
    client session over stdio, and exposes helper methods for
    listing/calling tools.

    The server subprocess automatically receives a ``PYTHONPATH`` that
    includes the project ``src/`` directory so that ``pathlight_mcp`` is
    importable even without a prior ``pip install -e``.

    Attributes:
        command: Python executable used to launch the server.
        cwd: Working directory for the subprocess (default: project root).
        env: Extra environment variables merged into the subprocess env.
        src_dir: Path to prepend to ``PYTHONPATH`` for the subprocess.
        startup_timeout: Seconds to wait for the server to be ready.
        backend: Backend selection passed to the server CLI (``"auto"`` or
            ``"mock"``).  When ``"mock"``, the server boots with MockBackend.
    """

    command: str = sys.executable
    cwd: str | None = None
    env: dict[str, str] | None = None
    src_dir: str = _DEFAULT_SRC_DIR
    startup_timeout: float = 10.0
    backend: str = "auto"

    _session: ClientSession | None = field(default=None, init=False, repr=False)
    _stdio_context: Any = field(default=None, init=False, repr=False)
    _session_context: Any = field(default=None, init=False, repr=False)

    @property
    def session(self) -> ClientSession:
        """The active MCP client session."""
        if self._session is None:
            raise RuntimeError("Server is not running; use 'async with' context")
        return self._session

    def _build_env(self) -> dict[str, str]:
        """Build the subprocess environment with PYTHONPATH set."""
        # Inherit the full parent environment so the child process has
        # PATH, DISPLAY, HOME, etc.  Previously this started from an empty
        # dict which stripped all parent env vars (GW-075).
        env: dict[str, str] = dict(os.environ)
        if self.env:
            env.update(self.env)
        # Prepend src_dir to PYTHONPATH so the subprocess can import pathlight_mcp.
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = self.src_dir + ((os.pathsep + existing) if existing else "")
        # Ensure PYTHONIOENCODING is set to utf-8 for stdio transport.
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    async def start(self) -> None:
        """Spawn the server subprocess and connect the MCP client session."""
        server_params = StdioServerParameters(
            command=self.command,
            args=["-m", "pathlight_mcp", "--backend", self.backend],
            env=self._build_env(),
            cwd=self.cwd or str(_PROJECT_ROOT),
        )

        self._stdio_context = stdio_client(server_params)
        stdio_transport = await self._stdio_context.__aenter__()
        read_stream, write_stream = stdio_transport

        self._session = ClientSession(read_stream, write_stream)
        self._session_context = self._session

        await self._session.__aenter__()
        await asyncio.wait_for(
            self._session.initialize(),
            timeout=self.startup_timeout,
        )

    async def stop(self) -> None:
        """Shut down the MCP client session and terminate the subprocess."""
        try:
            if self._session_context is not None:
                await self._session_context.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            if self._stdio_context is not None:
                await self._stdio_context.__aexit__(None, None, None)
        except Exception:
            pass
        self._session = None
        self._session_context = None
        self._stdio_context = None

    async def list_tools(self) -> list[Tool]:
        """List all tools exposed by the Pathlight MCP server.

        Returns:
            List of :class:`mcp.types.Tool` objects.
        """
        result: ListToolsResult = await self.session.list_tools()
        return result.tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        """Call a tool by name and return the result.

        Returns:
            A :class:`mcp.types.CallToolResult` with ``content`` and
            ``structuredContent`` fields.
        """
        result: CallToolResult = await self.session.call_tool(
            name,
            arguments=arguments or {},
        )
        return result

    async def __aenter__(self) -> "PathlightMCPServerProcess":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

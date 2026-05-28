"""Server robustness integration tests (TC-H05, TC-H06).

Tests crash detection and startup timeout behavior without requiring
an Anthropic API key.
"""

import asyncio

import pytest

import tests.harness.server as server_mod
from tests.harness.server import PathlightMCPServerProcess


@pytest.mark.integration
class TestServerCrashDetection:
    """TC-H05: Server crash detection."""

    async def test_crash_on_nonexistent_module(self) -> None:
        """Server subprocess using a non-existent module should fail to start."""
        server = PathlightMCPServerProcess(
            startup_timeout=5.0,
        )

        # Use a non-existent module so the subprocess exits immediately.
        async def _crashing_start():
            from mcp.client.stdio import StdioServerParameters, stdio_client

            server_params = StdioServerParameters(
                command=server.command,
                args=["-m", "nonexistent_module_that_does_not_exist_xyz"],
                env=server._build_env(),
                cwd=server.cwd or str(server_mod._PROJECT_ROOT),
            )
            server._stdio_context = stdio_client(server_params)
            try:
                stdio_transport = await server._stdio_context.__aenter__()
                read_stream, write_stream = stdio_transport
                from mcp.client.session import ClientSession

                server._session = ClientSession(read_stream, write_stream)
                server._session_context = server._session
                await server._session.__aenter__()
                await asyncio.wait_for(
                    server._session.initialize(),
                    timeout=server.startup_timeout,
                )
            finally:
                await server.stop()

        with pytest.raises(Exception, match=r"(?i)closed|error|failed"):
            await _crashing_start()


@pytest.mark.integration
class TestStartupTimeout:
    """TC-H06: Startup timeout."""

    async def test_startup_timeout_triggers(self) -> None:
        """Server that doesn't initialize within timeout should raise."""
        server = PathlightMCPServerProcess(startup_timeout=0.001)

        async def _hanging_start():
            from mcp.client.stdio import StdioServerParameters, stdio_client

            server_params = StdioServerParameters(
                command=server.command,
                args=["-c", "import time; time.sleep(10)"],
                env={},
            )
            server._stdio_context = stdio_client(server_params)
            try:
                stdio_transport = await server._stdio_context.__aenter__()
                read_stream, write_stream = stdio_transport
                from mcp.client.session import ClientSession

                server._session = ClientSession(read_stream, write_stream)
                server._session_context = server._session
                await server._session.__aenter__()
                await asyncio.wait_for(
                    server._session.initialize(),
                    timeout=server.startup_timeout,
                )
            finally:
                await server.stop()

        with pytest.raises((asyncio.TimeoutError, TimeoutError, OSError)):
            await _hanging_start()

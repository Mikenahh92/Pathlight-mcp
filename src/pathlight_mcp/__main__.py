"""Entry point for the Pathlight MCP server (PRD R1).

Wires stdio transport so that MCP clients can communicate with the
server over the standard input/output streams::

    python -m pathlight_mcp
    python -m pathlight_mcp --backend=mock
    python -m pathlight_mcp --version
"""

import argparse

from pathlight_mcp.server import PathlightMCPServer

__all__ = ["main"]

_VERSION_FALLBACK = "0.0.0.dev0"


def _get_version() -> str:
    """Return the installed package version, with a safe fallback.

    Resolves the version from installed distribution metadata via
    ``importlib.metadata`` so it matches the installed build (including
    editable installs). Falls back to ``_VERSION_FALLBACK`` when metadata
    is unavailable (e.g. a raw source checkout) so ``--version`` never
    crashes.

    Returns:
        The package version string, or the fallback when metadata is
        missing.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("pathlight-mcp")
    except PackageNotFoundError:
        return _VERSION_FALLBACK


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(prog="pathlight_mcp")
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"pathlight-mcp {_get_version()}\n",
        help="Print the package version and exit.",
    )
    parser.add_argument(
        "--backend",
        choices=["mock", "auto"],
        default="auto",
        help="Backend to use. 'mock' uses MockBackend; 'auto' selects "
        "platform backend (default: auto).",
    )
    return parser.parse_args(argv)


def _create_backend(backend_name: str):
    """Create the backend instance for the given name.

    Args:
        backend_name: ``"mock"`` for MockBackend, ``"auto"`` for platform.

    Returns:
        A DesktopBackend instance, or ``None`` for stub mode (when the
        platform backend is not available).
    """
    if backend_name == "mock":
        from pathlight_mcp.backends import MockBackend

        return MockBackend()

    # "auto" — try the platform-specific backend; fall back to stub mode
    # when the platform backend is unavailable (wrong OS, missing deps).
    import sys

    if sys.platform == "linux":
        try:
            from pathlight_mcp.backends import LinuxBackend

            return LinuxBackend()
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            from pathlight_mcp.backends import WindowsBackend

            return WindowsBackend()
        except Exception:
            pass

    return None


def main(argv: list[str] | None = None) -> None:
    """Run the Pathlight MCP server with stdio transport.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).
    """
    args = _parse_args(argv)
    backend = _create_backend(args.backend)

    # Wrap the platform backend in a BackendRouter so that web tools
    # (web_connect, web_navigate, web_evaluate) pass their isinstance guard.
    # When no backend is available (stub mode), pass None through unchanged.
    if backend is not None:
        from pathlight_mcp.backends import BackendRouter

        backend = BackendRouter(native=backend)

    server = PathlightMCPServer(backend=backend)
    server.register_tools()
    server.register_resources()
    server.run()


if __name__ == "__main__":
    main()

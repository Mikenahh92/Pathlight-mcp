"""Entry point for the Guidewire MCP server (PRD R1).

Wires stdio transport so that MCP clients can communicate with the
server over the standard input/output streams::

    python -m guidewire
    python -m guidewire --backend=mock
"""

import argparse

from guidewire.server import GuidewireServer

__all__ = ["main"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(prog="guidewire")
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
        from guidewire.backends import MockBackend

        return MockBackend()

    # "auto" — try the platform-specific backend; fall back to stub mode
    # when the platform backend is unavailable (wrong OS, missing deps).
    import sys

    if sys.platform == "linux":
        try:
            from guidewire.backends import LinuxBackend

            return LinuxBackend()
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            from guidewire.backends import WindowsBackend

            return WindowsBackend()
        except Exception:
            pass

    return None


def main(argv: list[str] | None = None) -> None:
    """Run the Guidewire MCP server with stdio transport.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).
    """
    args = _parse_args(argv)
    backend = _create_backend(args.backend)

    # Wrap the platform backend in a BackendRouter so that web tools
    # (web_connect, web_navigate, web_evaluate) pass their isinstance guard.
    # When no backend is available (stub mode), pass None through unchanged.
    if backend is not None:
        from guidewire.backends import BackendRouter

        backend = BackendRouter(native=backend)

    server = GuidewireServer(backend=backend)
    server.register_tools()
    server.register_resources()
    server.run()


if __name__ == "__main__":
    main()

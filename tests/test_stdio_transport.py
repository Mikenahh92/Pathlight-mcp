"""Tests for stdio transport wiring (GW-008).

Validates that:
- ``__main__.main`` wires stdio transport correctly.
- The PathlightMCPServer can be instantiated and tools registered without errors.
- The ``run()`` method exists and delegates to FastMCP's stdio transport.
"""

import inspect

from pathlight_mcp.server import PathlightMCPServer


class TestStdioTransport:
    """Tests for the stdio transport wiring."""

    def test_pathlight_mcp_server_has_run_method(self):
        """PathlightMCPServer should have a ``run`` method."""
        server = PathlightMCPServer()
        assert callable(getattr(server, "run", None))

    def test_pathlight_mcp_server_has_register_tools_method(self):
        """PathlightMCPServer should have a ``register_tools`` method."""
        server = PathlightMCPServer()
        assert callable(getattr(server, "register_tools", None))

    def test_pathlight_mcp_server_register_tools_idempotent(self):
        """Calling register_tools multiple times should not raise."""
        server = PathlightMCPServer()
        server.register_tools()
        server.register_tools()  # second call should be safe

    def test_main_entry_point_importable(self):
        """``pathlight_mcp.__main__`` should be importable and expose ``main``."""
        from pathlight_mcp.__main__ import main

        assert callable(main)

    def test_main_uses_pathlight_mcp_server(self):
        """The ``main`` function should use PathlightMCPServer."""
        import pathlight_mcp.__main__ as mod

        source = inspect.getsource(mod.main)
        assert "PathlightMCPServer" in source
        assert "register_tools" in source
        assert "run" in source

    async def test_server_tools_callable_via_mcp(self):
        """Registered tools should be callable through the MCP layer."""
        import json

        server = PathlightMCPServer()
        server.register_tools()
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert isinstance(data, dict)
        assert data["windows"] == []


class TestVersionFlag:
    """Tests for the ``--version``/``-V`` CLI flag (GW-192)."""

    def _invoke_and_capture(self, argv: list[str], capsys) -> tuple[object, str, str]:
        """Invoke ``main`` with ``argv`` and return (exit code, stdout, stderr)."""
        import pytest

        from pathlight_mcp.__main__ import main

        with pytest.raises(SystemExit) as excinfo:
            main(argv)
        captured = capsys.readouterr()
        return excinfo.value.code, captured.out, captured.err

    def test_both_spellings_print_version_and_exit_zero(self, capsys):
        """``--version`` and ``-V`` print ``pathlight-mcp <version>`` and exit 0 (TC-01)."""
        import re

        for flag in ("--version", "-V"):
            code, out, err = self._invoke_and_capture([flag], capsys)
            assert code == 0, f"{flag}: expected exit code 0, got {code}"
            assert out.startswith("pathlight-mcp "), f"{flag}: unexpected stdout: {out!r}"
            token = out.removeprefix("pathlight-mcp ")
            assert token.strip(), f"{flag}: version token must be non-empty"
            assert token.endswith("\n"), f"{flag}: expected single trailing newline"
            assert token.count("\n") == 1, f"{flag}: expected exactly one trailing newline"
            assert re.fullmatch(r"[^\s]+", token.strip()), f"{flag}: version token {token!r}"
            assert err == "", f"{flag}: stderr must stay empty"

    def test_version_goes_to_stdout_not_stderr(self, capsys):
        """Version output goes to stdout; stderr stays empty (TC-02)."""
        import pytest

        from pathlight_mcp.__main__ import _parse_args

        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--version"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("pathlight-mcp ")
        assert captured.err == ""

    def test_version_short_circuits_before_backend_or_server(self, capsys, monkeypatch):
        """``--version`` exits before backend creation or server startup (TC-03)."""
        from pathlight_mcp import __main__ as mod

        def _fail(*_args, **_kwargs):
            raise AssertionError("version flag must not create a backend or server")

        monkeypatch.setattr(mod, "_create_backend", _fail, raising=True)
        monkeypatch.setattr(mod, "PathlightMCPServer", _fail, raising=True)
        code, out, _err = self._invoke_and_capture(["--version"], capsys)
        assert code == 0
        assert out.startswith("pathlight-mcp ")

    def test_version_falls_back_when_metadata_missing(self, capsys, monkeypatch):
        """Missing metadata prints the fallback version instead of crashing (TC-04)."""
        import importlib.metadata

        def _raise(_dist_name: str) -> str:
            raise importlib.metadata.PackageNotFoundError("pathlight-mcp")

        monkeypatch.setattr(importlib.metadata, "version", _raise, raising=True)
        code, out, err = self._invoke_and_capture(["--version"], capsys)
        assert code == 0
        assert out == "pathlight-mcp 0.0.0.dev0\n"
        assert err == ""

    def test_printed_version_matches_installed_metadata(self, capsys):
        """Printed token equals the installed distribution metadata version (TC-05)."""
        from importlib.metadata import version

        code, out, _err = self._invoke_and_capture(["--version"], capsys)
        assert code == 0
        assert out == f"pathlight-mcp {version('pathlight-mcp')}\n"

    def test_help_includes_version_and_backend_unchanged(self, capsys):
        """``--help`` lists ``--version`` with the ``--backend`` text unchanged (TC-06)."""
        import pytest

        from pathlight_mcp.__main__ import _parse_args

        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--help"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "--version" in captured.out
        assert "-V" in captured.out
        assert "--backend {mock,auto}" in captured.out
        # Help text is line-wrapped by argparse — compare with whitespace
        # normalized so the check is independent of terminal width.
        flat = " ".join(captured.out.split())
        assert (
            "Backend to use. 'mock' uses MockBackend; 'auto' selects "
            "platform backend (default: auto)." in flat
        )

    def test_existing_parser_behavior_unchanged(self):
        """Default backend, explicit backend, and invalid choices stay intact (TC-07)."""
        import pytest

        from pathlight_mcp.__main__ import _parse_args

        assert _parse_args([]).backend == "auto"
        assert _parse_args(["--backend", "mock"]).backend == "mock"
        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--backend", "bogus"])
        assert excinfo.value.code != 0

    def test_printed_version_is_pep440(self, capsys):
        """Printed version token conforms to PEP 440 (TC-08)."""
        import packaging.version

        code, out, _err = self._invoke_and_capture(["--version"], capsys)
        assert code == 0
        token = out.removeprefix("pathlight-mcp ").strip()
        packaging.version.Version(token)  # raises InvalidVersion if not PEP 440

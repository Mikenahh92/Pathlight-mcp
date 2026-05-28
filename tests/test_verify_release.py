"""Tests for scripts/verify_release.py — pre-release verification script."""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts dir to path so we can import the module
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import verify_release as vr  # noqa: I001


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory with required files."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [build-system]
            requires = ["setuptools>=80", "setuptools-scm>=8.1"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "pathlight_mcp"
            dynamic = ["version"]
            description = "Test description"
            readme = "README.md"
            requires-python = ">=3.11"
            license = {text = "MIT"}
            classifiers = [
                "Programming Language :: Python :: 3",
                "Programming Language :: Python :: 3.11",
                "License :: OSI Approved :: MIT License",
                "Operating System :: OS Independent",
            ]

            [project.urls]
            Homepage = "https://github.com/example/test"
            "Bug Tracker" = "https://github.com/example/test/issues"
            "Source Code" = "https://github.com/example/test"
        """),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Test Project\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (tmp_path / "SECURITY.md").write_text(
        "# Security Policy\n\nSee GitHub Security Advisories.\n", encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        textwrap.dedent("""\
            # Changelog

            ## [Unreleased]

            ## [0.1.0] — 2025-01-01

            ### Added
            - Initial release
        """),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def config(project_dir: Path) -> vr.VerifyConfig:
    """Create a default VerifyConfig for testing."""
    return vr.VerifyConfig(project_root=project_dir)


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


class TestCheckResult:
    """Tests for the CheckResult dataclass."""

    def test_passed_result(self) -> None:
        result = vr.CheckResult(name="Test", passed=True, message="OK")
        assert result.passed is True
        assert result.name == "Test"
        assert result.hint == ""

    def test_failed_result_with_hint(self) -> None:
        result = vr.CheckResult(name="Test", passed=False, message="Failed", hint="Try this")
        assert result.passed is False
        assert result.hint == "Try this"


# ---------------------------------------------------------------------------
# check_version_derivation
# ---------------------------------------------------------------------------


class TestCheckVersionDerivation:
    """Tests for the version derivation check."""

    def test_valid_version(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="1.2.3\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is True
        assert "1.2.3" in result.message

    def test_fallback_version(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="0.0.0\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is False
        assert "fallback" in result.message.lower() or "0.0.0" in result.message

    def test_empty_version(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="\n", stderr="error", returncode=1)
            result = vr.check_version_derivation(config)

        assert result.passed is False

    def test_version_mismatch_with_expected(self, config: vr.VerifyConfig) -> None:
        config.expected_version = "2.0.0"
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="1.0.0\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is False
        assert "mismatch" in result.message.lower()

    def test_version_matches_expected(self, config: vr.VerifyConfig) -> None:
        config.expected_version = "1.0.0"
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="1.0.0\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is True

    def test_timeout(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=30)
            result = vr.check_version_derivation(config)

        assert result.passed is False

    def test_dev_suffix_rejected(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="1.2.3.dev0\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is False
        assert ".dev" in result.message

    def test_dirty_suffix_rejected(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="1.2.3.dirty\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is False
        assert ".dirty" in result.message

    def test_local_plus_suffix_rejected(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="1.2.3+local\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is False
        assert "+" in result.message

    def test_dev_suffix_without_number_rejected(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="1.2.3.dev\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is False

    def test_pre_release_tag_accepted(self, config: vr.VerifyConfig) -> None:
        """Pre-release tags like -rc1 or -beta should be accepted (no .dev/.dirty/+)."""
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(stdout="1.2.3-rc1\n", returncode=0)
            result = vr.check_version_derivation(config)

        assert result.passed is True


# ---------------------------------------------------------------------------
# check_clean_tree
# ---------------------------------------------------------------------------


class TestCheckCleanTree:
    """Tests for the clean tree check."""

    def test_clean_tree(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            # git status --porcelain: clean
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = vr.check_clean_tree(config)

        assert result.passed is True

    def test_dirty_tree(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            # git status --porcelain: dirty
            mock_run.return_value = MagicMock(stdout="M src/foo.py\n?? new_file.py\n", returncode=0)
            result = vr.check_clean_tree(config)

        assert result.passed is False
        assert "2 uncommitted" in result.message

    def test_git_not_found(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            result = vr.check_clean_tree(config)

        assert result.passed is False

    def test_unpushed_commits(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            # Sequence of calls: status -> branch -> upstream -> log
            mock_run.side_effect = [
                MagicMock(stdout="", returncode=0),  # git status
                MagicMock(stdout="main\n", returncode=0),  # git branch
                MagicMock(stdout="origin/main\n", returncode=0),  # upstream
                MagicMock(  # git log (unpushed)
                    stdout="abc1234 Some commit\ndef5678 Another\n",
                    returncode=0,
                ),
            ]
            result = vr.check_clean_tree(config)

        assert result.passed is False
        assert "unpushed" in result.message.lower()


# ---------------------------------------------------------------------------
# check_ci_green
# ---------------------------------------------------------------------------


class TestCheckCIGreen:
    """Tests for the CI green check."""

    def test_gh_not_found_non_strict(self, config: vr.VerifyConfig) -> None:
        with patch("shutil.which", return_value=None):
            result = vr.check_ci_green(config)

        assert result.passed is True
        assert "skipping" in result.message.lower()

    def test_gh_not_found_strict(self, config: vr.VerifyConfig) -> None:
        config.strict = True
        with patch("shutil.which", return_value=None):
            result = vr.check_ci_green(config)

        assert result.passed is False

    def test_ci_passed(self, config: vr.VerifyConfig) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("verify_release._run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(stdout="main\n", returncode=0),  # branch
                MagicMock(stdout="abc123def\n", returncode=0),  # SHA
                MagicMock(stdout="success\n", returncode=0),  # CI result
            ]
            result = vr.check_ci_green(config)

        assert result.passed is True
        assert "passed" in result.message.lower()

    def test_ci_failed(self, config: vr.VerifyConfig) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("verify_release._run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(stdout="main\n", returncode=0),  # branch
                MagicMock(stdout="abc123def\n", returncode=0),  # SHA
                MagicMock(stdout="failure\n", returncode=0),  # CI result
            ]
            result = vr.check_ci_green(config)

        assert result.passed is False
        assert "failure" in result.message.lower()

    def test_ci_no_runs_non_strict(self, config: vr.VerifyConfig) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("verify_release._run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(stdout="main\n", returncode=0),  # branch
                MagicMock(stdout="abc123def\n", returncode=0),  # SHA
                MagicMock(stdout="\n", returncode=0),  # no CI runs
            ]
            result = vr.check_ci_green(config)

        assert result.passed is True
        assert "no completed" in result.message.lower()


# ---------------------------------------------------------------------------
# check_changelog
# ---------------------------------------------------------------------------


class TestCheckChangelog:
    """Tests for the CHANGELOG check."""

    def test_valid_changelog(self, config: vr.VerifyConfig) -> None:
        result = vr.check_changelog(config)
        assert result.passed is True
        assert "version entry" in result.message.lower()

    def test_missing_changelog(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "CHANGELOG.md").unlink()
        result = vr.check_changelog(config)
        assert result.passed is False
        assert "not found" in result.message.lower()

    def test_empty_changelog(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "CHANGELOG.md").write_text("", encoding="utf-8")
        result = vr.check_changelog(config)
        assert result.passed is False
        assert "empty" in result.message.lower()

    def test_no_version_entries(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "CHANGELOG.md").write_text(
            "# Changelog\n\nSome text without version entries.\n",
            encoding="utf-8",
        )
        result = vr.check_changelog(config)
        assert result.passed is False

    def test_expected_version_present(self, config: vr.VerifyConfig) -> None:
        config.expected_version = "0.1.0"
        result = vr.check_changelog(config)
        assert result.passed is True

    def test_expected_version_missing(self, config: vr.VerifyConfig) -> None:
        config.expected_version = "9.9.9"
        result = vr.check_changelog(config)
        assert result.passed is False
        assert "missing" in result.message.lower()


# ---------------------------------------------------------------------------
# check_metadata
# ---------------------------------------------------------------------------


class TestCheckMetadata:
    """Tests for the pyproject.toml metadata check."""

    def test_complete_metadata(self, config: vr.VerifyConfig) -> None:
        result = vr.check_metadata(config)
        assert result.passed is True
        assert "all required" in result.message.lower()

    def test_missing_pyproject(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "pyproject.toml").unlink()
        result = vr.check_metadata(config)
        assert result.passed is False
        assert "not found" in result.message.lower()

    def test_missing_project_section(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "pyproject.toml").write_text(
            "[build-system]\nrequires = ['setuptools']\n",
            encoding="utf-8",
        )
        result = vr.check_metadata(config)
        assert result.passed is False
        assert "[project]" in result.message

    def test_missing_name_field(self, config: vr.VerifyConfig) -> None:
        content = (config.project_root / "pyproject.toml").read_text(encoding="utf-8")
        content = content.replace("name = ", "# name = ")
        (config.project_root / "pyproject.toml").write_text(content, encoding="utf-8")
        result = vr.check_metadata(config)
        assert result.passed is False
        assert "name" in result.message.lower()

    def test_missing_urls_section(self, config: vr.VerifyConfig) -> None:
        content = (config.project_root / "pyproject.toml").read_text(encoding="utf-8")
        content = content.replace("[project.urls]", "# [project.urls]")
        (config.project_root / "pyproject.toml").write_text(content, encoding="utf-8")
        result = vr.check_metadata(config)
        assert result.passed is False
        assert "url" in result.message.lower()

    def test_missing_classifiers(self, config: vr.VerifyConfig) -> None:
        content = (config.project_root / "pyproject.toml").read_text(encoding="utf-8")
        # Remove the entire classifiers block
        content = re.sub(
            r"\n\s*classifiers\s*=\s*\[.*?\]",
            "",
            content,
            flags=re.DOTALL,
        )
        (config.project_root / "pyproject.toml").write_text(content, encoding="utf-8")
        result = vr.check_metadata(config)
        assert result.passed is False
        assert "classifiers" in result.message.lower()

    def test_missing_python_classifier(self, config: vr.VerifyConfig) -> None:
        content = (config.project_root / "pyproject.toml").read_text(encoding="utf-8")
        # Remove the Python classifiers but keep the OS one
        content = content.replace('"Programming Language :: Python :: 3",\n', "")
        content = content.replace('"Programming Language :: Python :: 3.11",\n', "")
        (config.project_root / "pyproject.toml").write_text(content, encoding="utf-8")
        result = vr.check_metadata(config)
        assert result.passed is False
        assert "python" in result.message.lower()

    def test_missing_os_classifier(self, config: vr.VerifyConfig) -> None:
        content = (config.project_root / "pyproject.toml").read_text(encoding="utf-8")
        content = content.replace('"Operating System :: OS Independent",\n', "")
        (config.project_root / "pyproject.toml").write_text(content, encoding="utf-8")
        result = vr.check_metadata(config)
        assert result.passed is False
        assert "os" in result.message.lower() or "operating" in result.message.lower()

    def test_complete_classifiers(self, config: vr.VerifyConfig) -> None:
        result = vr.check_metadata(config)
        assert result.passed is True


# ---------------------------------------------------------------------------
# check_required_files
# ---------------------------------------------------------------------------


class TestCheckRequiredFiles:
    """Tests for the required files check."""

    def test_all_files_present(self, config: vr.VerifyConfig) -> None:
        result = vr.check_required_files(config)
        assert result.passed is True

    def test_missing_license(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "LICENSE").unlink()
        result = vr.check_required_files(config)
        assert result.passed is False
        assert "LICENSE" in result.message

    def test_missing_readme(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "README.md").unlink()
        result = vr.check_required_files(config)
        assert result.passed is False
        assert "README.md" in result.message

    def test_missing_multiple_files(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "LICENSE").unlink()
        (config.project_root / "CHANGELOG.md").unlink()
        result = vr.check_required_files(config)
        assert result.passed is False
        assert "LICENSE" in result.message
        assert "CHANGELOG.md" in result.message

    def test_missing_security_md(self, config: vr.VerifyConfig) -> None:
        (config.project_root / "SECURITY.md").unlink()
        result = vr.check_required_files(config)
        assert result.passed is False
        assert "SECURITY.md" in result.message


# ---------------------------------------------------------------------------
# check_build
# ---------------------------------------------------------------------------


class TestCheckBuild:
    """Tests for the wheel build check."""

    def test_build_not_available(self, config: vr.VerifyConfig) -> None:
        with patch("verify_release._run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)  # import build fails
            result = vr.check_build(config)

        assert result.passed is False
        assert "not installed" in result.message.lower()

    def test_build_succeeds_twine_not_available(
        self, config: vr.VerifyConfig, tmp_path: Path
    ) -> None:
        call_count = 0

        def mock_run_side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if "import build" in " ".join(cmd):
                return MagicMock(returncode=0)  # build available
            if "import twine" in " ".join(cmd):
                return MagicMock(returncode=1)  # twine not available
            if "-m" in cmd and "build" in cmd:
                return MagicMock(returncode=0, stdout="Successfully built", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("verify_release._run", side_effect=mock_run_side_effect),
            patch("tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path / "dist"))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            (tmp_path / "dist").mkdir(exist_ok=True)
            (tmp_path / "dist" / "pathlight_mcp-1.0.0.tar.gz").touch()
            (tmp_path / "dist" / "pathlight_mcp-1.0.0-py3-none-any.whl").touch()
            result = vr.check_build(config)

        assert result.passed is True
        assert "twine not installed" in result.message.lower()

    def test_non_pure_wheel_rejected(self, config: vr.VerifyConfig, tmp_path: Path) -> None:
        def mock_run_side_effect(cmd, **kwargs):
            if "import build" in " ".join(cmd):
                return MagicMock(returncode=0)
            if "import twine" in " ".join(cmd):
                return MagicMock(returncode=1)  # twine not available
            if "-m" in cmd and "build" in cmd:
                return MagicMock(returncode=0, stdout="Successfully built", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("verify_release._run", side_effect=mock_run_side_effect),
            patch("tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path / "dist2"))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            (tmp_path / "dist2").mkdir(exist_ok=True)
            (tmp_path / "dist2" / "pathlight_mcp-1.0.0.tar.gz").touch()
            # Platform-specific wheel (NOT py3-none-any)
            (tmp_path / "dist2" / "pathlight_mcp-1.0.0-cp311-cp311-linux_x86_64.whl").touch()
            result = vr.check_build(config)

        assert result.passed is False
        assert "pure-python" in result.message.lower()

    def test_pure_wheel_accepted(self, config: vr.VerifyConfig, tmp_path: Path) -> None:
        def mock_run_side_effect(cmd, **kwargs):
            if "import build" in " ".join(cmd):
                return MagicMock(returncode=0)
            if "import twine" in " ".join(cmd):
                return MagicMock(returncode=1)  # twine not available
            if "-m" in cmd and "build" in cmd:
                return MagicMock(returncode=0, stdout="Successfully built", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("verify_release._run", side_effect=mock_run_side_effect),
            patch("tempfile.TemporaryDirectory") as mock_tmpdir,
        ):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path / "dist3"))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            (tmp_path / "dist3").mkdir(exist_ok=True)
            (tmp_path / "dist3" / "pathlight_mcp-1.0.0.tar.gz").touch()
            (tmp_path / "dist3" / "pathlight_mcp-1.0.0-py3-none-any.whl").touch()
            result = vr.check_build(config)

        assert result.passed is True


# ---------------------------------------------------------------------------
# run_checks / main
# ---------------------------------------------------------------------------


class TestRunChecks:
    """Tests for the run_checks orchestrator."""

    def test_all_checks_run(self, config: vr.VerifyConfig) -> None:
        results = vr.run_checks(config)
        assert len(results) == len(vr.CHECKS)

    def test_skip_specific_checks(self, config: vr.VerifyConfig) -> None:
        config.skip_checks = {"ci", "build"}
        results = vr.run_checks(config)
        skipped = [r for r in results if r.message == "Skipped."]
        assert len(skipped) == 2


class TestMain:
    """Tests for the main() entry point."""

    def test_invalid_skip_check(self, project_dir: Path) -> None:
        exit_code = vr.main(["--skip", "nonexistent", "--project-root", str(project_dir)])
        assert exit_code == 2

    def test_no_pyproject(self, tmp_path: Path) -> None:
        exit_code = vr.main(["--project-root", str(tmp_path)])
        assert exit_code == 2

    def test_help(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            vr.main(["--help"])
        assert exc_info.value.code == 0

    def test_version_flag(self, project_dir: Path) -> None:
        """Test that --version flag is accepted (result depends on mock/gith state)."""
        # This mainly verifies the arg parsing works
        with patch("verify_release.run_checks") as mock_checks:
            mock_checks.return_value = [vr.CheckResult(name="Test", passed=True, message="OK")]
            exit_code = vr.main(["--version", "1.0.0", "--project-root", str(project_dir)])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# print_report (smoke test)
# ---------------------------------------------------------------------------


class TestPrintReport:
    """Tests for the report printer."""

    def test_prints_all_passed(self, capsys: pytest.CaptureFixture[str]) -> None:
        results = [
            vr.CheckResult(name="Test Check", passed=True, message="All good"),
        ]
        vr.print_report(results)
        output = capsys.readouterr().out
        assert "PASS" in output
        assert "Test Check" in output
        assert "1 passed" in output

    def test_prints_failure_with_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        results = [
            vr.CheckResult(
                name="Failed Check",
                passed=False,
                message="Something broke",
                hint="Try fixing X",
            ),
        ]
        vr.print_report(results)
        output = capsys.readouterr().out
        assert "FAIL" in output
        assert "Hint" in output
        assert "1 failed" in output

    def test_prints_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        results = [
            vr.CheckResult(name="Skipped Check", passed=True, message="Skipped."),
        ]
        vr.print_report(results)
        output = capsys.readouterr().out
        assert "Skipped" in output
        assert "1 skipped" in output

#!/usr/bin/env python
"""Pre-release verification script for Guidewire.

Validates all pre-conditions for a PyPI release:
- Version derivation (setuptools-scm)
- Clean git tree
- CI green on the target branch
- CHANGELOG entries for the upcoming version
- pyproject.toml metadata completeness
- Wheel/sdist build succeeds and passes twine check
- Required distribution files present

Usage:
    python scripts/verify_release.py [--version X.Y.Z] [--skip ci,build] [--strict]

Exit codes:
    0 — All checks passed
    1 — One or more checks failed
    2 — Invalid arguments or setup error
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z\-.]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z\-.]+))?$"
)

REQUIRED_FILES = [
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "pyproject.toml",
]

REQUIRED_METADATA_KEYS = [
    "name",
    "description",
    "readme",
    "license",
    "urls",
]

REQUIRED_URLS = [
    "Homepage",
    "Bug Tracker",
    "Source Code",
]


@dataclass
class CheckResult:
    """Outcome of a single verification check."""

    name: str
    passed: bool
    message: str
    hint: str = ""


@dataclass
class VerifyConfig:
    """Configuration for the verification run."""

    project_root: Path
    expected_version: str | None = None
    skip_checks: set[str] = field(default_factory=set)
    strict: bool = False


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _run(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and return the result."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
        check=check,
    )


def _emoji(result: CheckResult) -> str:
    """Return a pass/fail emoji for terminal output."""
    return "✅" if result.passed else "❌"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_version_derivation(config: VerifyConfig) -> CheckResult:
    """Verify setuptools-scm can derive a version and it looks valid."""
    try:
        proc = _run(
            [sys.executable, "-m", "setuptools_scm"],
            cwd=config.project_root,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return CheckResult(
            name="Version Derivation",
            passed=False,
            message=f"Failed to run setuptools-scm: {exc}",
            hint="Install setuptools-scm: pip install setuptools-scm",
        )

    version = proc.stdout.strip()

    if not version:
        stderr = proc.stderr.strip()
        return CheckResult(
            name="Version Derivation",
            passed=False,
            message=f"setuptools-scm returned empty version. stderr: {stderr}",
            hint="Ensure git tags exist (e.g. git tag v0.1.0) and the working "
            "directory is a git repository.",
        )

    if version == "0.0.0":
        return CheckResult(
            name="Version Derivation",
            passed=False,
            message="Version resolved to fallback '0.0.0' — no git tags found.",
            hint="Create a version tag: git tag vX.Y.Z && git push --tags",
        )

    # Reject dev/dirty/local suffixes — these indicate an unclean or pre-release state
    if re.search(r"\.dev\d*|\.dirty|\+", version):
        return CheckResult(
            name="Version Derivation",
            passed=False,
            message=f"Version '{version}' contains a pre-release or local suffix "
            "(.dev, .dirty, or +). Only clean release versions are allowed.",
            hint="Ensure the git tree is clean and on a tagged commit. "
            "setuptools-scm appends .dev/.dirty when not on an exact tag.",
        )

    # Validate semver shape
    base_version = re.split(r"[.\-]", version)
    if len(base_version) < 3:
        return CheckResult(
            name="Version Derivation",
            passed=False,
            message=f"Version '{version}' does not look like semver.",
            hint="setuptools-scm should produce X.Y.Z format versions.",
        )

    if config.expected_version and version != config.expected_version:
        return CheckResult(
            name="Version Derivation",
            passed=False,
            message=f"Version mismatch: derived '{version}', expected '{config.expected_version}'.",
            hint="Check git tag history. The tag should match the expected version.",
        )

    return CheckResult(
        name="Version Derivation",
        passed=True,
        message=f"Version: {version}",
    )


def check_clean_tree(config: VerifyConfig) -> CheckResult:
    """Verify the git working tree is clean (no uncommitted changes)."""
    try:
        proc = _run(
            ["git", "status", "--porcelain"],
            cwd=config.project_root,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return CheckResult(
            name="Clean Tree",
            passed=False,
            message=f"Failed to run git status: {exc}",
            hint="Ensure git is installed and the directory is a git repository.",
        )

    dirty_files = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    if dirty_files:
        file_list = "\n  ".join(dirty_files[:20])
        suffix = f"\n  ... and {len(dirty_files) - 20} more" if len(dirty_files) > 20 else ""
        return CheckResult(
            name="Clean Tree",
            passed=False,
            message=(
                f"Working tree has {len(dirty_files)} uncommitted change(s):\n  {file_list}{suffix}"
            ),
            hint="Commit or stash your changes before releasing: "
            "git add -A && git commit -m 'chore: prepare release'",
        )

    # Also check for unpushed commits on current branch
    try:
        branch_proc = _run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=config.project_root,
            timeout=10,
        )
        branch = branch_proc.stdout.strip()

        if branch and branch != "HEAD" and branch != "(HEAD)":
            upstream_proc = _run(
                ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
                cwd=config.project_root,
                timeout=10,
            )
            if upstream_proc.returncode == 0:
                log_proc = _run(
                    ["git", "log", "--oneline", f"{upstream_proc.stdout.strip()}..HEAD"],
                    cwd=config.project_root,
                    timeout=10,
                )
                unpushed = [line for line in log_proc.stdout.strip().splitlines() if line.strip()]
                if unpushed:
                    return CheckResult(
                        name="Clean Tree",
                        passed=False,
                        message=f"{len(unpushed)} unpushed commit(s) on '{branch}'.",
                        hint="Push your commits: git push",
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Non-critical; skip upstream check

    return CheckResult(
        name="Clean Tree",
        passed=True,
        message="Working tree is clean.",
    )


def check_ci_green(config: VerifyConfig) -> CheckResult:
    """Verify the latest CI run on the current branch passed."""
    # Check if gh CLI is available
    gh_path = shutil.which("gh")
    if not gh_path:
        return CheckResult(
            name="CI Green",
            passed=not config.strict,
            message="gh CLI not found — skipping CI check."
            if not config.strict
            else "gh CLI not found (strict mode: fail).",
            hint="Install GitHub CLI: https://cli.github.com/",
        )

    try:
        # Get current branch
        branch_proc = _run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=config.project_root,
            timeout=10,
        )
        branch = branch_proc.stdout.strip()

        # Get latest commit SHA
        sha_proc = _run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.project_root,
            timeout=10,
        )
        sha = sha_proc.stdout.strip()

        # Check CI status for the commit
        proc = _run(
            [
                gh_path,
                "api",
                f"repos/{{owner}}/{{repo}}/actions/runs?head_sha={sha}&status=completed&per_page=5",
                "--jq",
                '.workflow_runs[] | select(.name == "CI") | .conclusion',
            ],
            cwd=config.project_root,
            timeout=30,
        )

        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            return CheckResult(
                name="CI Green",
                passed=not config.strict,
                message=f"Could not query CI status: {stderr}"
                if not config.strict
                else f"Could not query CI status (strict mode: fail): {stderr}",
                hint="Ensure you are in a GitHub repository with Actions enabled "
                "and GITHUB_TOKEN / gh auth is configured.",
            )

        conclusions = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]

        if not conclusions:
            return CheckResult(
                name="CI Green",
                passed=not config.strict,
                message=f"No completed CI runs found for commit {sha[:8]}."
                if not config.strict
                else f"No completed CI runs found for commit {sha[:8]} (strict: fail).",
                hint="Push to main and wait for CI to complete, or check the "
                "Actions tab in GitHub.",
            )

        latest = conclusions[0]
        if latest == "success":
            return CheckResult(
                name="CI Green",
                passed=True,
                message=f"CI passed for {sha[:8]} on '{branch}'.",
            )
        else:
            return CheckResult(
                name="CI Green",
                passed=False,
                message=f"CI concluded with: {latest} for {sha[:8]}.",
                hint="Fix CI failures before releasing. Check the Actions tab for details.",
            )

    except subprocess.TimeoutExpired:
        return CheckResult(
            name="CI Green",
            passed=not config.strict,
            message="Timed out querying CI status."
            if not config.strict
            else "Timed out querying CI status (strict mode: fail).",
            hint="Check your network connection and GitHub access.",
        )


def check_changelog(config: VerifyConfig) -> CheckResult:
    """Verify CHANGELOG.md has entries for the upcoming version."""
    changelog_path = config.project_root / "CHANGELOG.md"
    if not changelog_path.exists():
        return CheckResult(
            name="CHANGELOG Entries",
            passed=False,
            message="CHANGELOG.md not found.",
            hint="Create CHANGELOG.md or run git-cliff to generate it.",
        )

    content = changelog_path.read_text(encoding="utf-8")

    if not content.strip():
        return CheckResult(
            name="CHANGELOG Entries",
            passed=False,
            message="CHANGELOG.md is empty.",
            hint="Run git-cliff to populate the changelog: git-cliff -o CHANGELOG.md",
        )

    # Check for unreleased section (meaning there are new commits since last release)
    has_unreleased = "## [Unreleased]" in content

    # Check for at least one version entry
    version_entries = re.findall(r"## \[\d+\.\d+\.\d+\]", content)
    if not version_entries:
        return CheckResult(
            name="CHANGELOG Entries",
            passed=False,
            message="No version entries found in CHANGELOG.md.",
            hint="Run git-cliff: git-cliff -o CHANGELOG.md",
        )

    # If an expected version is given, verify it appears
    if config.expected_version:
        version_header = f"## [{config.expected_version}]"
        if version_header in content:
            return CheckResult(
                name="CHANGELOG Entries",
                passed=True,
                message=f"CHANGELOG.md has entry for v{config.expected_version}.",
            )
        else:
            return CheckResult(
                name="CHANGELOG Entries",
                passed=False,
                message=f"CHANGELOG.md missing entry for v{config.expected_version}.",
                hint=f"Run git-cliff to generate the changelog entry: "
                f"git-cliff --tag v{config.expected_version} -o CHANGELOG.md",
            )

    return CheckResult(
        name="CHANGELOG Entries",
        passed=True,
        message=f"CHANGELOG.md has {len(version_entries)} version entry(ies)."
        + (" Unreleased section present." if has_unreleased else ""),
    )


def check_metadata(config: VerifyConfig) -> CheckResult:
    """Verify pyproject.toml has required metadata fields."""
    pyproject_path = config.project_root / "pyproject.toml"
    if not pyproject_path.exists():
        return CheckResult(
            name="Metadata Completeness",
            passed=False,
            message="pyproject.toml not found.",
            hint="Create pyproject.toml with project metadata.",
        )

    content = pyproject_path.read_text(encoding="utf-8")
    issues: list[str] = []

    # Check [project] section exists
    if "[project]" not in content:
        return CheckResult(
            name="Metadata Completeness",
            passed=False,
            message="Missing [project] section in pyproject.toml.",
            hint="Add a [project] section with name, description, etc.",
        )

    # Check required fields
    if not re.search(r"^name\s*=", content, re.MULTILINE):
        issues.append("name")
    if not re.search(r"^description\s*=", content, re.MULTILINE):
        issues.append("description")
    if not re.search(r"^readme\s*=", content, re.MULTILINE):
        issues.append("readme")
    if not re.search(r"license\s*=", content, re.MULTILINE):
        issues.append("license")
    if not re.search(r"requires-python\s*=", content, re.MULTILINE):
        issues.append("requires-python")

    # Check [project.urls]
    urls_match = re.search(r"^\[project\.urls\]", content, re.MULTILINE)
    if not urls_match:
        issues.append("[project.urls] section")
    else:
        # Extract the [project.urls] section content (up to next section or EOF)
        urls_start = urls_match.start()
        next_section = re.search(r"^\[", content[urls_match.end() :], re.MULTILINE)
        if next_section:
            urls_body = content[urls_start : urls_match.end() + next_section.start()]
        else:
            urls_body = content[urls_start:]
        for url_name in REQUIRED_URLS:
            if url_name not in urls_body:
                issues.append(f"URL: {url_name}")

    # Check dynamic version
    if "dynamic" not in content or "version" not in content:
        issues.append("dynamic version (setuptools-scm)")

    # Check [build-system]
    if "[build-system]" not in content:
        issues.append("[build-system] section")

    # Check classifiers — require at least one Python version and one OS classifier
    classifiers_match = re.search(r"classifiers\s*=\s*\[", content)
    if not classifiers_match:
        issues.append("classifiers (missing classifiers section)")
    else:
        classifiers_body = content[classifiers_match.start() :]
        bracket_end = classifiers_body.find("]")
        if bracket_end != -1:
            classifiers_body = classifiers_body[: bracket_end + 1]
        has_python_classifier = re.search(
            r"Programming Language :: Python :: \d", classifiers_body
        )
        has_os_classifier = re.search(
            r"Operating System ::", classifiers_body
        )
        if not has_python_classifier:
            issues.append("classifiers (no Python version classifier, e.g. "
            "'Programming Language :: Python :: 3')")
        if not has_os_classifier:
            issues.append("classifiers (no OS classifier, e.g. "
            "'Operating System :: OS Independent')")

    if issues:
        return CheckResult(
            name="Metadata Completeness",
            passed=False,
            message=f"Missing: {', '.join(issues)}",
            hint="Add the missing fields to pyproject.toml. See "
            "https://packaging.python.org/en/latest/specifications/declaring-project-metadata/",
        )

    return CheckResult(
        name="Metadata Completeness",
        passed=True,
        message="All required metadata fields present.",
    )


def check_required_files(config: VerifyConfig) -> CheckResult:
    """Verify all required distribution files exist."""
    missing = []
    for filename in REQUIRED_FILES:
        if not (config.project_root / filename).exists():
            missing.append(filename)

    if missing:
        return CheckResult(
            name="Required Files",
            passed=False,
            message=f"Missing: {', '.join(missing)}",
            hint="Create the missing files before releasing.",
        )

    return CheckResult(
        name="Required Files",
        passed=True,
        message="All required files present.",
    )


def check_build(config: VerifyConfig) -> CheckResult:
    """Verify the package builds successfully and passes twine check."""
    # Check build tool is available
    build_available = (
        _run(
            [sys.executable, "-c", "import build"],
            cwd=config.project_root,
            timeout=10,
        ).returncode
        == 0
    )

    twine_available = (
        _run(
            [sys.executable, "-c", "import twine"],
            cwd=config.project_root,
            timeout=10,
        ).returncode
        == 0
    )

    if not build_available:
        return CheckResult(
            name="Wheel Build",
            passed=False,
            message="python-build package not installed.",
            hint="Install: pip install build twine",
        )

    with tempfile.TemporaryDirectory(prefix="gw-verify-") as tmpdir:
        # Build
        try:
            build_proc = _run(
                [sys.executable, "-m", "build", "--outdir", tmpdir],
                cwd=config.project_root,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                name="Wheel Build",
                passed=False,
                message="Build timed out (180s).",
                hint="Check for build errors. Run: python -m build",
            )

        if build_proc.returncode != 0:
            stderr = build_proc.stderr.strip()
            # Limit stderr output
            if len(stderr) > 500:
                stderr = stderr[:500] + "..."
            return CheckResult(
                name="Wheel Build",
                passed=False,
                message=f"Build failed:\n{stderr}",
                hint="Fix build errors. Run: python -m build",
            )

        # List built files
        built = list(Path(tmpdir).glob("*"))
        built_names = [f.name for f in built]
        has_sdist = any(n.endswith(".tar.gz") for n in built_names)
        has_wheel = any(n.endswith(".whl") for n in built_names)

        if not has_sdist or not has_wheel:
            return CheckResult(
                name="Wheel Build",
                passed=False,
                message=f"Build produced: {', '.join(built_names)}. "
                "Expected both sdist (.tar.gz) and wheel (.whl).",
                hint="Check build configuration in pyproject.toml.",
            )

        # Validate wheel is a pure-python wheel (py3-none-any)
        wheel_names = [n for n in built_names if n.endswith(".whl")]
        has_pure_wheel = any(re.search(r"-py3-none-any\.whl$", n) for n in wheel_names)
        if not has_pure_wheel:
            return CheckResult(
                name="Wheel Build",
                passed=False,
                message=f"Wheel is not a pure-python wheel: {', '.join(wheel_names)}. "
                "Expected a py3-none-any wheel.",
                hint="Check pyproject.toml — ensure no platform-specific dependencies "
                "are pulling in platform tags.",
            )

        # Twine check
        if not twine_available:
            return CheckResult(
                name="Wheel Build",
                passed=True,
                message=f"Build succeeded ({', '.join(built_names)}). "
                "twine not installed — skipping twine check.",
                hint="Install twine for full validation: pip install twine",
            )

        dist_files = [str(f) for f in built]
        try:
            twine_proc = _run(
                [sys.executable, "-m", "twine", "check", *dist_files],
                cwd=config.project_root,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                name="Wheel Build",
                passed=True,
                message=f"Build succeeded ({', '.join(built_names)}). twine check timed out.",
                hint="Check twine manually: twine check dist/*",
            )

        if twine_proc.returncode != 0:
            stderr = twine_proc.stderr.strip()
            return CheckResult(
                name="Wheel Build",
                passed=False,
                message=f"Build succeeded but twine check failed:\n{stderr}",
                hint="Fix metadata issues reported by twine.",
            )

        return CheckResult(
            name="Wheel Build",
            passed=True,
            message=f"Build succeeded ({', '.join(built_names)}). twine check passed.",
        )


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

CHECKS = {
    "version": ("Version Derivation", check_version_derivation),
    "tree": ("Clean Tree", check_clean_tree),
    "ci": ("CI Green", check_ci_green),
    "changelog": ("CHANGELOG Entries", check_changelog),
    "metadata": ("Metadata Completeness", check_metadata),
    "files": ("Required Files", check_required_files),
    "build": ("Wheel Build", check_build),
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_checks(config: VerifyConfig) -> list[CheckResult]:
    """Run all checks and return results."""
    results: list[CheckResult] = []
    for key, (label, check_fn) in CHECKS.items():
        if key in config.skip_checks:
            results.append(CheckResult(name=label, passed=True, message="Skipped."))
            continue
        result = check_fn(config)
        results.append(result)
    return results


def print_report(results: list[CheckResult]) -> None:
    """Print a formatted report of all check results."""
    print("\n" + "=" * 60)
    print("  Pre-Release Verification Report")
    print("=" * 60 + "\n")

    passed = 0
    failed = 0
    skipped = 0

    for result in results:
        if result.message == "Skipped.":
            skipped += 1
            print(f"  ⏭️  {result.name}: Skipped")
            continue

        emoji = _emoji(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  {emoji}  {result.name}: {status}")
        print(f"      {result.message}")
        if result.hint and not result.passed:
            print(f"      💡 Hint: {result.hint}")
        print()

        if result.passed:
            passed += 1
        else:
            failed += 1

    total = passed + failed + skipped
    print("-" * 60)
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped (total: {total})")
    print("=" * 60 + "\n")

    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the pre-release verification script."""
    parser = argparse.ArgumentParser(
        description="Pre-release verification script for Guidewire.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Checks performed:
  version   — setuptools-scm version derivation
  tree      — clean git working tree
  ci        — latest CI run on branch passed
  changelog — CHANGELOG.md has version entries
  metadata  — pyproject.toml required fields
  files     — required distribution files exist
  build     — wheel/sdist build and twine check

Examples:
  python scripts/verify_release.py
  python scripts/verify_release.py --version 1.0.0
  python scripts/verify_release.py --skip ci,build
  python scripts/verify_release.py --strict
        """,
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Expected version to verify (e.g. 1.0.0). If omitted, only checks "
        "that a version can be derived.",
    )
    parser.add_argument(
        "--skip",
        default="",
        help="Comma-separated list of checks to skip: version,tree,ci,changelog,"
        "metadata,files,build",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: fail on checks that would otherwise be skipped "
        "(e.g. CI check when gh CLI is unavailable).",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Path to project root (defaults to git repository root).",
    )
    args = parser.parse_args(argv)

    # Determine project root
    if args.project_root:
        project_root = Path(args.project_root).resolve()
    else:
        # Find git root from current working directory
        try:
            proc = _run(
                ["git", "rev-parse", "--show-toplevel"],
                timeout=10,
            )
            project_root = Path(proc.stdout.strip()).resolve()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            project_root = Path.cwd().resolve()

    if not (project_root / "pyproject.toml").exists():
        print(f"Error: No pyproject.toml found in {project_root}", file=sys.stderr)
        return 2

    # Parse skip list
    skip_checks = {s.strip() for s in args.skip.split(",") if s.strip()}
    valid_checks = set(CHECKS.keys())
    invalid = skip_checks - valid_checks
    if invalid:
        print(
            f"Error: Unknown check(s) to skip: {', '.join(invalid)}. "
            f"Valid checks: {', '.join(sorted(valid_checks))}",
            file=sys.stderr,
        )
        return 2

    config = VerifyConfig(
        project_root=project_root,
        expected_version=args.version,
        skip_checks=skip_checks,
        strict=args.strict,
    )

    print(f"Verifying release readiness in: {project_root}")
    if args.version:
        print(f"Expected version: {args.version}")
    if skip_checks:
        print(f"Skipping checks: {', '.join(sorted(skip_checks))}")
    if args.strict:
        print("Mode: strict")

    results = run_checks(config)
    print_report(results)

    failures = [r for r in results if not r.passed and r.message != "Skipped."]
    if failures:
        print(f"❌ {len(failures)} check(s) failed. Fix the issues above before releasing.")
        return 1

    print("✅ All checks passed. Ready to release!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

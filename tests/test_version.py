"""Sanity checks for the pathlight_mcp package."""

from importlib.metadata import PackageNotFoundError, version

import packaging.version


def _get_version() -> str:
    """Return the package version, with fallback for uninstalled development."""
    try:
        return version("pathlight_mcp")
    except PackageNotFoundError:
        return "0.0.0"


def test_version_is_string() -> None:
    """Version should be a non-empty string."""
    v = _get_version()
    assert isinstance(v, str)
    assert len(v) > 0


def test_version_is_pep440() -> None:
    """Version should follow PEP 440 format."""
    v = _get_version()
    packaging.version.Version(v)  # raises InvalidVersion if not PEP 440


def test_package_importable() -> None:
    """The pathlight_mcp package should be importable."""
    import pathlight_mcp  # noqa: F401

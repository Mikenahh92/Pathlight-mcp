"""Shared scaffolding for the auto-mode v2 E2E suite (``tests/e2e``).

Registers the ``e2e_automode`` marker (selection/deselection only - the
suite is pure static data and runs in the default CI job by design; no
environment-variable skip gate) and exposes the shared
``automode_matrix_loader`` fixture used by every matrix-driven test
module in this suite.

GRP-3 ownership rule (architecture doc 44bb351e, AD-2): sibling stories
extend this file additively only - append new fixtures or markers, never
edit or remove another story's entries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "automode_v2"


def pytest_configure(config: pytest.Config) -> None:
    """Register the e2e_automode marker (no skip gate by design)."""
    config.addinivalue_line(
        "markers",
        "e2e_automode: auto-mode v2 E2E suite (pure static data; runs in "
        "the default CI job; select/deselect with -m)",
    )


@pytest.fixture(scope="session")
def automode_matrix_loader():
    """Load a declarative automode_v2 matrix by file name (cached)."""
    cache: dict[str, dict[str, Any]] = {}

    def _load(filename: str) -> dict[str, Any]:
        if filename not in cache:
            path = FIXTURES_DIR / filename
            with path.open(encoding="utf-8") as handle:
                cache[filename] = json.load(handle)
        return cache[filename]

    return _load

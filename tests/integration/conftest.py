"""Shared fixtures and markers for integration tests.

Registers the ``integration`` and ``live`` pytest markers with skip
conditions based on the ``GUIDEWARE_RUN_INTEGRATION`` and
``GUIDEWARE_RUN_LIVE`` environment variables.
"""

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the integration and live markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test (deselected unless "
        "GUIDEWARE_RUN_INTEGRATION=1)",
    )
    config.addinivalue_line(
        "markers",
        "live: mark test as requiring a live Anthropic API connection "
        "(deselected unless GUIDEWARE_RUN_LIVE=1)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip integration and live tests unless their env vars are set."""
    run_integration = os.environ.get("GUIDEWARE_RUN_INTEGRATION", "") in ("1", "true")
    run_live = os.environ.get("GUIDEWARE_RUN_LIVE", "") in ("1", "true")
    for item in items:
        if "integration" in item.keywords and not run_integration:
            item.add_marker(
                pytest.mark.skip(
                    reason="integration tests skipped (set GUIDEWARE_RUN_INTEGRATION=1)",
                )
            )
        if "live" in item.keywords and not run_live:
            item.add_marker(
                pytest.mark.skip(
                    reason="live agent tests skipped (set GUIDEWARE_RUN_LIVE=1)",
                )
            )

"""Shared test fixtures for Pathlight MCP test suite."""

import pytest

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.models import Bounds, ElementStates, NormalizedElement
from pathlight_mcp.refs import ElementRefStore

# ---------------------------------------------------------------------------
# Backend fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_backend() -> MockBackend:
    """Return a fresh MockBackend instance."""
    return MockBackend()


# ---------------------------------------------------------------------------
# Reference store fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def ref_store() -> ElementRefStore:
    """Return a fresh ElementRefStore instance."""
    return ElementRefStore()


# ---------------------------------------------------------------------------
# Native handle fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_native_window() -> NativeHandle:
    """Return a sample NativeHandle representing a window."""
    return NativeHandle("native-window-0")


@pytest.fixture
def sample_native_element() -> NativeHandle:
    """Return a sample NativeHandle representing an element."""
    return NativeHandle("native-element-0")


# ---------------------------------------------------------------------------
# NormalizedElement fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_normalized_element() -> NormalizedElement:
    """Return a sample NormalizedElement with common fields populated."""
    return NormalizedElement(
        ref="e1",
        backend_id="native-0",
        role="button",
        name="Submit",
        description="Submit the form",
        value="Submit",
        text="Submit",
        states=ElementStates(enabled=True, focused=False),
        bounds=Bounds(x=100.0, y=200.0, width=80.0, height=32.0),
        actions=["click", "invoke"],
    )


@pytest.fixture
def password_element() -> NormalizedElement:
    """Return a NormalizedElement representing a password input field."""
    return NormalizedElement(
        ref="e2",
        backend_id="native-1",
        role="text_input",
        name="Password",
        value="super_secret",
        text="super_secret",
        states=ElementStates(enabled=True, is_password=True),
        actions=["type", "set_value"],
    )


@pytest.fixture
def sample_window_element() -> NormalizedElement:
    """Return a NormalizedElement representing a window with children."""
    return NormalizedElement(
        ref="w1",
        backend_id="win-0",
        role="window",
        name="Login Form",
        bounds=Bounds(x=0.0, y=0.0, width=800.0, height=600.0),
        children=[
            NormalizedElement(
                ref="e1",
                backend_id="native-0",
                role="text_input",
                name="Username",
                value="admin",
                text="admin",
                actions=["type"],
            ),
            NormalizedElement(
                ref="e2",
                backend_id="native-1",
                role="text_input",
                name="Password",
                value="secret123",
                text="secret123",
                actions=["type"],
            ),
            NormalizedElement(
                ref="e3",
                backend_id="native-2",
                role="button",
                name="Login",
                actions=["click"],
            ),
        ],
    )

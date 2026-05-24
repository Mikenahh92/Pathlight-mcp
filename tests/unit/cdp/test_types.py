"""Tests for CDP type definitions (_types.py).

Validates the ConnectionState StrEnum.
"""

from guidewire.cdp._types import ConnectionState


class TestConnectionState:
    """Tests for :class:`ConnectionState`."""

    def test_values(self) -> None:
        assert ConnectionState.DISCONNECTED == "disconnected"
        assert ConnectionState.CONNECTING == "connecting"
        assert ConnectionState.CONNECTED == "connected"
        assert ConnectionState.CLOSING == "closing"
        assert ConnectionState.CLOSED == "closed"

    def test_is_str(self) -> None:
        for state in ConnectionState:
            assert isinstance(state, str)

    def test_all_states_present(self) -> None:
        assert len(ConnectionState) == 5

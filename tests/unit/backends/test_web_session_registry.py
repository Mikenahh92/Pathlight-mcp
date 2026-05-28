"""Tests for WebSessionRegistry session lifecycle improvements (GW-130).

Validates that the session registry:
- Supports lazy invalidation via ``invalidate()``
- Supports full removal via ``remove()``
- Detects stale domain caches in ``get_domains()`` and refreshes them
- Properly handles long sequential operations without session staleness
"""

from unittest.mock import MagicMock

from guidewire.backends.web.web_session import WebSessionRegistry
from guidewire.cdp._types import CDPTarget, SessionState
from guidewire.cdp.session import CDPSession

# -- Fixtures ----------------------------------------------------------------


def _make_target(target_id: str = "target-1") -> CDPTarget:
    """Create a CDPTarget for testing."""
    return CDPTarget(
        id=target_id,
        type="page",
        title="Test Page",
        url="https://example.com",
    )


def _make_session(target_id: str = "target-1", attached: bool = True) -> CDPSession:
    """Create a mock CDPSession for testing.

    Args:
        target_id: The target ID for the session.
        attached: Whether the session should appear attached.
    """
    target = _make_target(target_id)
    conn = MagicMock()
    session = CDPSession(connection=conn, target=target)
    if attached:
        session._state = SessionState.ATTACHED
        session._session_id = f"session-{target_id}"
    return session


def _make_registry() -> WebSessionRegistry:
    """Create a WebSessionRegistry with a mock browser getter."""
    mock_browser = MagicMock()
    return WebSessionRegistry(lambda: mock_browser)


# -- Tests for invalidate() --------------------------------------------------


class TestInvalidate:
    """Tests for WebSessionRegistry.invalidate() (GW-130)."""

    def test_invalidate_marks_session_detached(self) -> None:
        """invalidate() should mark the session as detached."""
        registry = _make_registry()
        session = _make_session("t-1")
        registry._sessions["t-1"] = session
        assert session.is_attached

        registry.invalidate("t-1")

        assert not session.is_attached
        assert session.state == SessionState.DETACHED

    def test_invalidate_removes_domain_cache(self) -> None:
        """invalidate() should remove cached domain wrappers."""
        registry = _make_registry()
        mock_domains = (MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
        registry._domains["t-1"] = mock_domains

        registry.invalidate("t-1")

        assert "t-1" not in registry._domains

    def test_invalidate_keeps_session_in_cache(self) -> None:
        """invalidate() should keep the session entry (lazy re-attach on next access)."""
        registry = _make_registry()
        session = _make_session("t-1")
        registry._sessions["t-1"] = session

        registry.invalidate("t-1")

        # Session is still in the cache but marked detached
        assert "t-1" in registry._sessions
        assert not session.is_attached

    def test_invalidate_nonexistent_target_is_safe(self) -> None:
        """invalidate() on a nonexistent target should not raise."""
        registry = _make_registry()
        registry.invalidate("nonexistent")  # should not raise

    def test_invalidate_clears_both_session_and_domains(self) -> None:
        """invalidate() should clear domains AND mark session detached."""
        registry = _make_registry()
        session = _make_session("t-1")
        registry._sessions["t-1"] = session
        registry._domains["t-1"] = (MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())

        registry.invalidate("t-1")

        assert not session.is_attached
        assert "t-1" not in registry._domains


# -- Tests for remove() ------------------------------------------------------


class TestRemove:
    """Tests for WebSessionRegistry.remove() (GW-130)."""

    def test_remove_removes_session_from_cache(self) -> None:
        """remove() should completely remove the session entry."""
        registry = _make_registry()
        session = _make_session("t-1")
        registry._sessions["t-1"] = session

        registry.remove("t-1")

        assert "t-1" not in registry._sessions

    def test_remove_removes_domain_cache(self) -> None:
        """remove() should remove cached domain wrappers."""
        registry = _make_registry()
        registry._domains["t-1"] = (MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())

        registry.remove("t-1")

        assert "t-1" not in registry._domains

    def test_remove_closes_session(self) -> None:
        """remove() should close the session."""
        registry = _make_registry()
        session = _make_session("t-1")
        registry._sessions["t-1"] = session

        registry.remove("t-1")

        # Session should have been closed (mark_detached or detach)
        assert not session.is_attached

    def test_remove_nonexistent_target_is_safe(self) -> None:
        """remove() on a nonexistent target should not raise."""
        registry = _make_registry()
        registry.remove("nonexistent")  # should not raise


# -- Tests for get_domains() staleness guard ---------------------------------


class TestGetDomainsStalenessGuard:
    """Tests for get_domains() staleness detection (GW-130)."""

    def test_get_domains_returns_cached_when_session_attached(self) -> None:
        """get_domains() should return cached domains when session is attached."""
        registry = _make_registry()
        session = _make_session("t-1", attached=True)
        registry._sessions["t-1"] = session

        mock_domains = (MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
        registry._domains["t-1"] = mock_domains

        result = registry.get_domains("t-1")
        assert result is mock_domains

    def test_get_domains_refreshes_when_session_detached(self) -> None:
        """get_domains() should discard cached domains when session is detached."""
        registry = _make_registry()
        session = _make_session("t-1", attached=False)
        registry._sessions["t-1"] = session

        # Put stale domains in cache
        stale_domains = (MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
        registry._domains["t-1"] = stale_domains

        # Mock get_or_create to return a fresh attached session
        fresh_session = _make_session("t-1", attached=True)
        registry.get_or_create = MagicMock(return_value=fresh_session)

        result = registry.get_domains("t-1")

        # Should NOT return stale domains
        assert result is not stale_domains
        # Should have called get_or_create to get a fresh session
        registry.get_or_create.assert_called_once_with("t-1")

    def test_get_domains_refreshes_when_no_session(self) -> None:
        """get_domains() should create fresh domains when no session exists."""
        registry = _make_registry()

        # Put stale domains in cache without any session
        stale_domains = (MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
        registry._domains["t-1"] = stale_domains

        # Mock get_or_create
        fresh_session = _make_session("t-1", attached=True)
        registry.get_or_create = MagicMock(return_value=fresh_session)

        result = registry.get_domains("t-1")

        # Should NOT return stale domains
        assert result is not stale_domains
        registry.get_or_create.assert_called_once_with("t-1")

    def test_get_domains_creates_fresh_after_invalidation(self) -> None:
        """After invalidate(), get_domains() should create fresh domains."""
        registry = _make_registry()
        session = _make_session("t-1", attached=True)
        registry._sessions["t-1"] = session

        # Create initial domains
        mock_domains = (MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
        registry._domains["t-1"] = mock_domains

        # Invalidate
        registry.invalidate("t-1")

        # Now get_domains should create fresh ones
        fresh_session = _make_session("t-1", attached=True)
        registry.get_or_create = MagicMock(return_value=fresh_session)

        result = registry.get_domains("t-1")
        assert result is not mock_domains


# -- Tests for sequential operations (long browsing session) -----------------


class TestSequentialOperations:
    """Tests simulating long sequential browsing operations (GW-130)."""

    def test_repeated_invalidate_and_get_or_create(self) -> None:
        """Simulate 20+ sequential operations: invalidate → get_or_create → invalidate."""
        registry = _make_registry()

        for _i in range(25):
            target_id = "t-1"

            # Simulate: session exists from prior operation
            session = _make_session(target_id, attached=True)
            registry._sessions[target_id] = session

            # Simulate: navigation invalidates the session
            registry.invalidate(target_id)
            assert not session.is_attached
            assert target_id not in registry._domains

            # Simulate: next operation re-creates the session
            fresh_session = _make_session(target_id, attached=True)
            registry._sessions[target_id] = fresh_session
            assert fresh_session.is_attached

    def test_invalidate_and_remove_interleaved(self) -> None:
        """Simulate tab operations: invalidate on activate, remove on close."""
        registry = _make_registry()

        # Tab 1: activate (invalidate)
        session1 = _make_session("tab-1", attached=True)
        registry._sessions["tab-1"] = session1
        registry.invalidate("tab-1")
        assert not session1.is_attached

        # Tab 2: open and use
        session2 = _make_session("tab-2", attached=True)
        registry._sessions["tab-2"] = session2

        # Tab 1: close (remove)
        registry.remove("tab-1")
        assert "tab-1" not in registry._sessions

        # Tab 2: still active
        assert "tab-2" in registry._sessions
        assert session2.is_attached

    def test_domains_always_fresh_after_navigation(self) -> None:
        """After navigation invalidation, get_domains returns fresh domain wrappers."""
        registry = _make_registry()

        # Setup: session with cached domains
        session = _make_session("t-1", attached=True)
        registry._sessions["t-1"] = session
        old_domains = (MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())
        registry._domains["t-1"] = old_domains

        # Navigation invalidates
        registry.invalidate("t-1")

        # Next get_domains call should create fresh domains
        fresh_session = _make_session("t-1", attached=True)
        registry.get_or_create = MagicMock(return_value=fresh_session)

        result = registry.get_domains("t-1")
        assert result is not old_domains


# -- Tests for WebBackend convenience methods --------------------------------


class TestWebBackendSessionMethods:
    """Tests for WebBackend._invalidate_session and _remove_session (GW-130)."""

    def test_invalidate_session_clears_all_caches(self) -> None:
        """_invalidate_session should invalidate registry, AX, and bounds caches."""
        from guidewire.backends.web import WebBackend

        mock_browser = MagicMock()
        backend = WebBackend(browser=mock_browser)
        backend._connected = True

        # Populate caches
        backend._ax_cache["node-1"] = MagicMock()
        backend._bounds_cache["node-1"] = {"x": 0, "y": 0, "width": 100, "height": 50}

        # Mock the registry invalidate
        backend._session_registry.invalidate = MagicMock()

        backend._invalidate_session("target-1")

        backend._session_registry.invalidate.assert_called_once_with("target-1")
        assert len(backend._ax_cache) == 0
        assert len(backend._bounds_cache) == 0

    def test_remove_session_clears_all_caches(self) -> None:
        """_remove_session should remove from registry and clear AX/bounds caches."""
        from guidewire.backends.web import WebBackend

        mock_browser = MagicMock()
        backend = WebBackend(browser=mock_browser)
        backend._connected = True

        # Populate caches
        backend._ax_cache["node-1"] = MagicMock()
        backend._bounds_cache["node-1"] = {"x": 0, "y": 0, "width": 100, "height": 50}

        # Mock the registry remove
        backend._session_registry.remove = MagicMock()

        backend._remove_session("target-1")

        backend._session_registry.remove.assert_called_once_with("target-1")
        assert len(backend._ax_cache) == 0
        assert len(backend._bounds_cache) == 0

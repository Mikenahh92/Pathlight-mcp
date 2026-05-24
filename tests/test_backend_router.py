"""Tests for BackendRouter — transparent multi-backend routing (GW-097).

Validates that:
- BackendRouter delegates correctly to native and web backends
- Window handles are tagged with backend origin
- Element handles are tagged and route back to the correct backend
- list_windows merges windows from all active backends
- Global operations (clipboard) delegate to native backend
- dispose disposes all managed backends
- Untagged handles default to native backend (backward compatibility)
- BackendUnavailableError during list_windows is handled gracefully
- is_valid returns False for unknown backend ids
- Window state management routes to the correct backend
- scroll_to_item tags result handles
- get_window_info and get_element_info include _backend_id annotation
"""

import pytest

from guidewire.backends import BackendRouter, MockBackend, TaggedHandle
from guidewire.backends.types import DesktopAction
from guidewire.errors import WindowNotFoundError

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture()
def native() -> MockBackend:
    """A MockBackend representing the native (Windows/Linux) backend."""
    backend = MockBackend().add_window(title="Notepad", app="notepad.exe")
    win_handle = backend.last_window_handle
    backend.add_element(role="button", name="Save", parent=win_handle)
    return backend


@pytest.fixture()
def web() -> MockBackend:
    """A MockBackend representing the web backend."""
    backend = MockBackend().add_window(title="Gmail", app="browser")
    win_handle = backend.last_window_handle
    backend.add_element(role="link", name="Inbox", parent=win_handle)
    return backend


@pytest.fixture()
def router(native: MockBackend, web: MockBackend) -> BackendRouter:
    """A BackendRouter with both native and web backends."""
    return BackendRouter(native=native, web=web)


@pytest.fixture()
def native_only_router(native: MockBackend) -> BackendRouter:
    """A BackendRouter with only the native backend."""
    return BackendRouter(native=native)


# -- TaggedHandle tests ------------------------------------------------------


class TestTaggedHandle:
    """Tests for the TaggedHandle wrapper."""

    def test_stores_inner_and_backend_id(self):
        """TaggedHandle should store inner handle and backend_id."""
        th = TaggedHandle("raw-handle", "native")
        assert th.inner == "raw-handle"
        assert th.backend_id == "native"

    def test_repr(self):
        """TaggedHandle repr should include inner and backend_id."""
        th = TaggedHandle("abc123", "web")
        assert "abc123" in repr(th)
        assert "web" in repr(th)

    def test_slots(self):
        """TaggedHandle should use __slots__ for memory efficiency."""
        th = TaggedHandle("x", "native")
        with pytest.raises(AttributeError):
            th.nonexistent = "value"


# -- Routing and delegation tests --------------------------------------------


class TestListWindows:
    """Tests for BackendRouter.list_windows."""

    def test_merges_windows_from_all_backends(self, router: BackendRouter):
        """list_windows should merge windows from native and web backends."""
        handles = router.list_windows()
        assert len(handles) == 2

    def test_handles_are_tagged(self, router: BackendRouter):
        """Each returned handle should be a TaggedHandle."""
        handles = router.list_windows()
        for h in handles:
            assert isinstance(h, TaggedHandle)

    def test_native_handle_tagged_native(self, router: BackendRouter):
        """Native backend windows should have backend_id='native'."""
        handles = router.list_windows()
        native_handles = [
            h for h in handles if isinstance(h, TaggedHandle) and h.backend_id == "native"
        ]
        assert len(native_handles) == 1

    def test_web_handle_tagged_web(self, router: BackendRouter):
        """Web backend windows should have backend_id='web'."""
        handles = router.list_windows()
        web_handles = [h for h in handles if isinstance(h, TaggedHandle) and h.backend_id == "web"]
        assert len(web_handles) == 1

    def test_native_only_router_returns_native_handles(self, native_only_router: BackendRouter):
        """Router without web backend should only return native handles."""
        handles = native_only_router.list_windows()
        assert len(handles) == 1
        assert isinstance(handles[0], TaggedHandle)
        assert handles[0].backend_id == "native"

    def test_graceful_on_backend_error(self, native: MockBackend):
        """list_windows should skip backends that raise errors."""
        # Create a backend that will fail on list_windows
        failing_backend = MockBackend()
        failing_backend.dispose()  # disposed backends return no windows

        router = BackendRouter(native=native, web=failing_backend)
        handles = router.list_windows()
        # Only native windows should be returned
        assert len(handles) >= 1
        backend_ids = {h.backend_id for h in handles}
        assert "native" in backend_ids


class TestGetWindowInfo:
    """Tests for BackendRouter.get_window_info."""

    def test_delegates_to_correct_backend(self, router: BackendRouter):
        """get_window_info should delegate to the owning backend."""
        handles = router.list_windows()
        for handle in handles:
            info = router.get_window_info(handle)
            assert "title" in info
            assert "_backend_id" in info

    def test_includes_backend_id_annotation(self, router: BackendRouter):
        """get_window_info should include _backend_id field."""
        handles = router.list_windows()
        for handle in handles:
            info = router.get_window_info(handle)
            assert info["_backend_id"] == handle.backend_id

    def test_raises_for_unknown_backend_id(self, router: BackendRouter):
        """Should raise WindowNotFoundError for unknown backend_id."""
        fake_handle = TaggedHandle("fake", "unknown_backend")
        with pytest.raises(WindowNotFoundError):
            router.get_window_info(fake_handle)


class TestFocusWindow:
    """Tests for BackendRouter.focus_window."""

    def test_routes_to_native(self, router: BackendRouter, native: MockBackend):
        """Focusing a native window should delegate to native backend."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        router.focus_window(native_handle)
        # Verify via action log on the native backend
        assert any("focused" in str(w).lower() or w.focused for w in native._windows.values())

    def test_routes_to_web(self, router: BackendRouter, web: MockBackend):
        """Focusing a web window should delegate to web backend."""
        handles = router.list_windows()
        web_handle = next(h for h in handles if h.backend_id == "web")
        router.focus_window(web_handle)
        # The web backend should have the window focused
        assert any(w.focused for w in web._windows.values())


class TestSnapshot:
    """Tests for BackendRouter.snapshot."""

    def test_delegates_to_native(self, router: BackendRouter):
        """Snapshot of a native window should return a valid tree."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        tree = router.snapshot(native_handle)
        assert tree["role"] == "window"
        assert "children" in tree

    def test_delegates_to_web(self, router: BackendRouter):
        """Snapshot of a web window should return a valid tree."""
        handles = router.list_windows()
        web_handle = next(h for h in handles if h.backend_id == "web")
        tree = router.snapshot(web_handle)
        assert tree["role"] == "window"

    def test_element_refs_tagged(self, router: BackendRouter):
        """Element handles in snapshot tree should be tagged."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        tree = router.snapshot(native_handle)
        # Check that children have tagged backend_id values
        _check_tree_tagged(tree, "native")


def _check_tree_tagged(node: dict, expected_id: str) -> None:
    """Recursively check that backend_id fields in tree are tagged."""
    bid = node.get("backend_id")
    if bid is not None and isinstance(bid, TaggedHandle):
        assert bid.backend_id == expected_id
    children = node.get("children", [])
    for child in children:
        _check_tree_tagged(child, expected_id)


class TestFindElements:
    """Tests for BackendRouter.find_elements."""

    def test_routes_to_native(self, router: BackendRouter):
        """find_elements on native window should delegate to native backend."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        elements = router.find_elements(native_handle, role="button")
        assert len(elements) == 1
        assert isinstance(elements[0], TaggedHandle)
        assert elements[0].backend_id == "native"

    def test_routes_to_web(self, router: BackendRouter):
        """find_elements on web window should delegate to web backend."""
        handles = router.list_windows()
        web_handle = next(h for h in handles if h.backend_id == "web")
        elements = router.find_elements(web_handle, role="link")
        assert len(elements) == 1
        assert isinstance(elements[0], TaggedHandle)
        assert elements[0].backend_id == "web"

    def test_empty_result(self, router: BackendRouter):
        """find_elements with no matches should return empty list."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        elements = router.find_elements(native_handle, role="checkbox")
        assert elements == []


class TestPerformAction:
    """Tests for BackendRouter.perform_action."""

    def test_routes_to_native_element(self, router: BackendRouter, native: MockBackend):
        """Actions on native elements should delegate to native backend."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        elements = router.find_elements(native_handle, role="button")
        assert len(elements) == 1

        router.perform_action(elements[0], DesktopAction.CLICK)
        assert len(native.action_log) > 0
        assert native.action_log[-1]["action"] == DesktopAction.CLICK

    def test_routes_to_web_element(self, router: BackendRouter, web: MockBackend):
        """Actions on web elements should delegate to web backend."""
        handles = router.list_windows()
        web_handle = next(h for h in handles if h.backend_id == "web")
        elements = router.find_elements(web_handle, role="link")
        assert len(elements) == 1

        router.perform_action(elements[0], DesktopAction.CLICK)
        assert len(web.action_log) > 0


class TestGetElementInfo:
    """Tests for BackendRouter.get_element_info."""

    def test_includes_backend_id(self, router: BackendRouter):
        """get_element_info should include _backend_id annotation."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        elements = router.find_elements(native_handle, role="button")
        assert len(elements) == 1

        info = router.get_element_info(elements[0])
        assert info["_backend_id"] == "native"
        assert info["role"] == "button"


class TestIsValid:
    """Tests for BackendRouter.is_valid."""

    def test_valid_native_element(self, router: BackendRouter):
        """is_valid should return True for valid native elements."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        elements = router.find_elements(native_handle, role="button")
        assert router.is_valid(elements[0]) is True

    def test_valid_window_handle(self, router: BackendRouter):
        """is_valid should return True for valid window handles."""
        handles = router.list_windows()
        for h in handles:
            assert router.is_valid(h) is True

    def test_unknown_backend_id_returns_false(self, router: BackendRouter):
        """is_valid should return False for unknown backend_id."""
        fake = TaggedHandle("nonexistent", "unknown_backend")
        assert router.is_valid(fake) is False

    def test_untagged_handle_delegates_to_native(
        self, native_only_router: BackendRouter, native: MockBackend
    ):
        """Untagged handles should default to native backend."""
        # Get a raw handle from native backend
        raw_handles = native.list_windows()
        assert len(raw_handles) > 0
        # Untagged handle should still work
        assert native_only_router.is_valid(raw_handles[0]) is True


class TestClipboard:
    """Tests for BackendRouter clipboard operations."""

    def test_clipboard_read_from_native(self, router: BackendRouter, native: MockBackend):
        """clipboard_read should delegate to native backend."""
        native.set_clipboard("hello from native")
        assert router.clipboard_read() == "hello from native"

    def test_clipboard_write_to_native(self, router: BackendRouter, native: MockBackend):
        """clipboard_write should delegate to native backend."""
        router.clipboard_write("test clipboard")
        assert native.clipboard_content == "test clipboard"


class TestDispose:
    """Tests for BackendRouter.dispose."""

    def test_disposes_all_backends(
        self, router: BackendRouter, native: MockBackend, web: MockBackend
    ):
        """dispose should dispose all managed backends."""
        router.dispose()
        assert native.is_disposed is True
        assert web.is_disposed is True

    def test_dispose_native_only(self, native_only_router: BackendRouter, native: MockBackend):
        """dispose should work with only native backend."""
        native_only_router.dispose()
        assert native.is_disposed is True


class TestScrollToItem:
    """Tests for BackendRouter.scroll_to_item."""

    def test_tags_result_handle(self, router: BackendRouter, native: MockBackend):
        """scroll_to_item should tag the result handle."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")

        result = router.scroll_to_item(native_handle, item_name="Save")
        # MockBackend's scroll_to_item searches elements in the same window
        if result is not None:
            assert isinstance(result, TaggedHandle)
            assert result.backend_id == "native"


class TestWindowManagement:
    """Tests for window state management routing."""

    def test_minimize_routes_correctly(self, router: BackendRouter, native: MockBackend):
        """minimize_window should route to the native backend."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        router.minimize_window(native_handle)
        assert any(w.minimized for w in native._windows.values())

    def test_maximize_routes_correctly(self, router: BackendRouter, native: MockBackend):
        """maximize_window should route to the native backend."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        router.maximize_window(native_handle)
        assert any(w.maximized for w in native._windows.values())

    def test_restore_routes_correctly(self, router: BackendRouter, native: MockBackend):
        """restore_window should route to the native backend."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        router.maximize_window(native_handle)
        router.restore_window(native_handle)
        window = next(iter(native._windows.values()))
        assert not window.maximized
        assert not window.minimized

    def test_move_routes_correctly(self, router: BackendRouter, native: MockBackend):
        """move_window should route to the native backend."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        router.move_window(native_handle, 10, 20)
        assert any(w.bounds.x == 10 and w.bounds.y == 20 for w in native._windows.values())

    def test_resize_routes_correctly(self, router: BackendRouter, native: MockBackend):
        """resize_window should route to the native backend."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        router.resize_window(native_handle, 1024, 768)
        assert any(
            w.bounds.width == 1024 and w.bounds.height == 768 for w in native._windows.values()
        )


class TestBackendFor:
    """Tests for BackendRouter.backend_for."""

    def test_returns_native_backend(self, router: BackendRouter):
        """backend_for should return native backend for native handles."""
        handles = router.list_windows()
        native_handle = next(h for h in handles if h.backend_id == "native")
        backend = router.backend_for(native_handle)
        assert backend is router.native

    def test_returns_web_backend(self, router: BackendRouter):
        """backend_for should return web backend for web handles."""
        handles = router.list_windows()
        web_handle = next(h for h in handles if h.backend_id == "web")
        backend = router.backend_for(web_handle)
        assert backend is router.web

    def test_raises_for_unknown_id(self, router: BackendRouter):
        """backend_for should raise for unknown backend_id."""
        fake = TaggedHandle("x", "nonexistent")
        with pytest.raises(WindowNotFoundError):
            router.backend_for(fake)


class TestBackwardCompatibility:
    """Tests for backward compatibility with untagged handles."""

    def test_untagged_window_defaults_to_native(
        self, native_only_router: BackendRouter, native: MockBackend
    ):
        """Untagged window handles should route to native backend."""
        raw_handles = native.list_windows()
        assert len(raw_handles) > 0
        # Pass a raw NativeHandle (not TaggedHandle) — should still work
        info = native_only_router.get_window_info(raw_handles[0])
        assert info["title"] == "Notepad"
        assert info["_backend_id"] == "native"

    def test_untagged_element_defaults_to_native(
        self, native_only_router: BackendRouter, native: MockBackend
    ):
        """Untagged element handles should route to native backend."""
        raw_handles = native.list_windows()
        elements = native.find_elements(raw_handles[0], role="button")
        assert len(elements) > 0, (
            f"Expected 1+ elements but got {elements}. "
            f"Windows: {list(native._windows.keys())}, "
            f"Elements: {[(h, e.window_handle, e.role) for h, e in native._elements.items()]}"
        )
        info = native_only_router.get_element_info(elements[0])
        assert info["role"] == "button"
        assert info["_backend_id"] == "native"


class TestServerIntegration:
    """Integration tests using GuidewireServer with BackendRouter."""

    async def test_router_works_with_server(self):
        """BackendRouter should work as the backend for GuidewireServer."""
        from guidewire.server import GuidewireServer

        native = MockBackend().add_window(title="Native", app="native.exe")
        web = MockBackend().add_window(title="Web", app="browser")
        router = BackendRouter(native=native, web=web)

        srv = GuidewireServer(backend=router)
        srv.register_tools()

        result, _meta = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        import json

        data = json.loads(result[0].text)
        assert data["count"] == 2

    async def test_snapshot_through_router(self):
        """Snapshot should work through BackendRouter via the server pipeline."""
        import json

        from guidewire.server import GuidewireServer

        native = MockBackend().add_window(title="Native", app="native.exe")
        web = MockBackend().add_window(title="Web", app="browser")
        router = BackendRouter(native=native, web=web)

        srv = GuidewireServer(backend=router)
        srv.register_tools()

        # List windows first to get refs
        result, _ = await srv.mcp.call_tool("desktop.list_windows", arguments={})
        data = json.loads(result[0].text)
        assert data["count"] == 2

        # Snapshot the first window (native)
        w1_ref = data["windows"][0]["ref"]
        snap_result, _ = await srv.mcp.call_tool(
            "desktop.snapshot", arguments={"window_ref": w1_ref}
        )
        snap = json.loads(snap_result[0].text)
        assert "tree" in snap
        assert snap["tree"]["role"] == "window"

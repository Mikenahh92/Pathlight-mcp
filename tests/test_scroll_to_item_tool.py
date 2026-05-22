"""Tests for the desktop.scroll_to_item tool handler (GW-052).

Validates that the wired scroll_to_item tool:
- Resolves a container e-prefixed reference via ElementRefStore.
- Validates existence and staleness via backend.is_valid().
- Delegates to backend.scroll_to_item(container, ...) for item lookup.
- Applies safety classification via classify().
- Returns a structured JSON success response with item_ref.
- Returns structured JSON error for unknown refs.
- Returns structured JSON error for stale refs.
- Validates input (empty string, missing name/index, negative index).
- Falls back to static stub response when no backend is provided.
- Supports both item_name and item_index matching.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.types import NativeHandle
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window, a list container, and list items."""
    b = MockBackend().add_window(title="Test Window", app="TestApp", focused=True)
    window_handle = b.list_windows()[0]
    b.add_element(role="list", name="FileList", parent=window_handle)
    b.add_element(role="list_item", name="Document.txt", parent=window_handle)
    b.add_element(role="list_item", name="Image.png", parent=window_handle)
    b.add_element(role="list_item", name="Spreadsheet.xlsx", parent=window_handle)
    b.add_element(role="list_item", name="Presentation.pptx", parent=window_handle)
    return b


@pytest.fixture()
def ref_store(backend: MockBackend) -> ElementRefStore:
    """Return an ElementRefStore with e-prefixed refs for all elements."""
    store = ElementRefStore()
    window_handle = backend.list_windows()[0]
    elements = backend.find_elements(window_handle)
    for handle in elements:
        store.store(handle, prefix="e")
    return store


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-scroll-to-item")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-scroll-to-item-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestScrollToItemStub:
    """scroll_to_item returns static stub response when no backend is provided."""

    async def test_stub_returns_not_available(self, stub_mcp: FastMCP) -> None:
        """Without a backend, scroll_to_item returns a not-available message."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": "e0", "item_name": "test"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "no backend" in data["message"].lower()


# -- Validation tests ---------------------------------------------------------


class TestScrollToItemValidation:
    """scroll_to_item validates input parameters."""

    async def test_empty_container_ref_returns_error(self, mcp: FastMCP) -> None:
        """Empty container_ref returns a validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": "", "item_name": "test"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "container_ref" in data["message"].lower()

    async def test_no_name_or_index_returns_error(self, mcp: FastMCP) -> None:
        """Missing both item_name and item_index returns a validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": "e0"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "item_name" in data["message"] or "item_index" in data["message"]

    async def test_negative_index_returns_error(self, mcp: FastMCP) -> None:
        """Negative item_index returns a validation_error."""
        result, _meta = await mcp.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": "e0", "item_index": -1},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-negative" in data["message"].lower()


# -- Not found / stale tests --------------------------------------------------


class TestScrollToItemErrors:
    """scroll_to_item returns structured errors for missing/stale refs."""

    async def test_unknown_ref_returns_error(self, mcp: FastMCP) -> None:
        """Unknown container_ref returns element_not_found."""
        result, _meta = await mcp.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": "e999", "item_name": "test"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "element_not_found"

    async def test_stale_ref_returns_error(
        self, backend: MockBackend, ref_store: ElementRefStore
    ) -> None:
        """Invalidated container ref returns stale_element_reference."""
        mcp_local = FastMCP(name="test-stale")
        register_all(mcp_local, backend=backend, ref_store=ref_store)

        # Find the list container element and invalidate it
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")
        assert len(elements) >= 1

        container = elements[0]
        backend.invalidate(container)

        # Find the ref for this container
        container_ref = None
        for ref, handle in ref_store._ref_to_handle.items():
            if handle == container:
                container_ref = ref
                break

        if container_ref is not None:
            result, _meta = await mcp_local.call_tool(
                "desktop.scroll_to_item",
                arguments={"container_ref": container_ref, "item_name": "test"},
            )
            data = json.loads(result[0].text)
            assert data["error"] == "stale_element_reference"


# -- Success tests ------------------------------------------------------------


class TestScrollToItemSuccess:
    """scroll_to_item wired to backend — success path."""

    async def test_scroll_to_item_by_name(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Scrolling to an item by name returns success with item_ref."""
        # Find the list container ref
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")
        assert len(elements) >= 1

        # Store the container handle if not already stored
        ref_store_fixture = ElementRefStore()
        container_ref = ref_store_fixture.store(elements[0])

        # Re-register with our store
        mcp_local = FastMCP(name="test-by-name")
        register_all(mcp_local, backend=backend, ref_store=ref_store_fixture)

        result, _meta = await mcp_local.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": container_ref, "item_name": "Image.png"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "item_ref" in data
        assert "risk" in data

    async def test_scroll_to_item_by_index(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Scrolling to an item by index returns success with item_ref."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")
        assert len(elements) >= 1

        ref_store_local = ElementRefStore()
        container_ref = ref_store_local.store(elements[0])

        mcp_local = FastMCP(name="test-by-index")
        register_all(mcp_local, backend=backend, ref_store=ref_store_local)

        result, _meta = await mcp_local.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": container_ref, "item_index": 0},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert "item_ref" in data

    async def test_item_not_found_returns_failure(self, mcp: FastMCP, backend: MockBackend) -> None:
        """When the target item doesn't exist, returns success=False."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")
        assert len(elements) >= 1

        ref_store_local = ElementRefStore()
        container_ref = ref_store_local.store(elements[0])

        mcp_local = FastMCP(name="test-not-found")
        register_all(mcp_local, backend=backend, ref_store=ref_store_local)

        result, _meta = await mcp_local.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": container_ref, "item_name": "NonexistentFile.xyz"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is False
        assert "not found" in data["message"].lower()

    async def test_returns_item_name_in_response(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Success response includes the item name."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")
        assert len(elements) >= 1

        ref_store_local = ElementRefStore()
        container_ref = ref_store_local.store(elements[0])

        mcp_local = FastMCP(name="test-item-name")
        register_all(mcp_local, backend=backend, ref_store=ref_store_local)

        result, _meta = await mcp_local.call_tool(
            "desktop.scroll_to_item",
            arguments={"container_ref": container_ref, "item_name": "Spreadsheet"},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["item_name"] == "Spreadsheet.xlsx"


# -- Backend-level tests (MockBackend.scroll_to_item) -------------------------


class TestMockBackendScrollToItem:
    """MockBackend.scroll_to_item unit tests."""

    def test_scroll_to_item_by_name(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item finds items by name."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")
        assert len(elements) == 1

        result = backend.scroll_to_item(elements[0], item_name="Image.png")
        assert result is not None

        info = backend.get_element_info(result)
        assert info["name"] == "Image.png"

    def test_scroll_to_item_by_index(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item finds items by index."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")
        assert len(elements) == 1

        result = backend.scroll_to_item(elements[0], item_index=0)
        assert result is not None

        info = backend.get_element_info(result)
        assert info["name"] == "Document.txt"

    def test_scroll_to_item_not_found(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item returns None for nonexistent items."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")

        result = backend.scroll_to_item(elements[0], item_name="NonexistentFile.xyz")
        assert result is None

    def test_scroll_to_item_requires_name_or_index(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item raises when neither name nor index is given."""
        from guidewire.errors import ActionNotSupportedError

        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")

        with pytest.raises(ActionNotSupportedError):
            backend.scroll_to_item(elements[0])

    def test_scroll_to_item_invalid_container(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item raises for invalid container handle."""
        from guidewire.errors import ElementNotFoundError

        with pytest.raises(ElementNotFoundError):
            backend.scroll_to_item(NativeHandle("nonexistent"), item_name="test")

    def test_scroll_to_item_case_insensitive(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item matches names case-insensitively."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")

        result = backend.scroll_to_item(elements[0], item_name="image.png")
        assert result is not None

        info = backend.get_element_info(result)
        assert info["name"] == "Image.png"

    def test_scroll_to_item_substring_match(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item matches name as a substring."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")

        result = backend.scroll_to_item(elements[0], item_name="Spread")
        assert result is not None

        info = backend.get_element_info(result)
        assert "Spread" in info["name"]

    def test_scroll_to_item_logs_action(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item records in action log."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")

        backend.scroll_to_item(elements[0], item_name="Image.png")
        assert any(entry.get("action") == "scroll_to_item" for entry in backend.action_log)

    def test_scroll_to_item_index_out_of_range(self, backend: MockBackend) -> None:
        """MockBackend.scroll_to_item returns None for index beyond items."""
        window = backend.list_windows()[0]
        elements = backend.find_elements(window, role="list")

        result = backend.scroll_to_item(elements[0], item_index=999)
        assert result is None


# -- Model tests --------------------------------------------------------------


class TestNormalizedElementIsVirtualized:
    """NormalizedElement is_virtualized field tests."""

    def test_is_virtualized_field(self) -> None:
        """NormalizedElement supports is_virtualized field."""
        from guidewire.models import NormalizedElement

        elem = NormalizedElement(
            ref="e0",
            backend_id="test",
            role="list_item",
            is_virtualized=True,
        )
        assert elem.is_virtualized is True

        elem2 = NormalizedElement(
            ref="e1",
            backend_id="test",
            role="button",
        )
        assert elem2.is_virtualized is None

    def test_to_dict_includes_is_virtualized(self) -> None:
        """to_dict() includes is_virtualized when set."""
        from guidewire.models import NormalizedElement

        elem = NormalizedElement(
            ref="e0",
            backend_id="test",
            role="list_item",
            is_virtualized=True,
        )
        d = elem.to_dict()
        assert d["is_virtualized"] is True

        elem2 = NormalizedElement(
            ref="e1",
            backend_id="test",
            role="button",
        )
        d2 = elem2.to_dict()
        assert "is_virtualized" not in d2


# -- Normalize tests ----------------------------------------------------------


class TestNormalizeIsVirtualized:
    """normalize_element() passes is_virtualized through."""

    def test_passes_true(self) -> None:
        from guidewire.backends.normalize import normalize_element

        elem = normalize_element(
            platform="windows",
            ref="e0",
            backend_id="test",
            role="ListItem",
            is_virtualized=True,
        )
        assert elem.is_virtualized is True

    def test_passes_false(self) -> None:
        from guidewire.backends.normalize import normalize_element

        elem = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="test",
            role="list item",
            is_virtualized=False,
        )
        assert elem.is_virtualized is False

    def test_passes_none_default(self) -> None:
        from guidewire.backends.normalize import normalize_element

        elem = normalize_element(
            platform="windows",
            ref="e2",
            backend_id="test",
            role="button",
        )
        assert elem.is_virtualized is None


# -- DesktopAction enum test --------------------------------------------------


class TestDesktopActionScrollToItem:
    """DesktopAction enum includes SCROLL_TO_ITEM."""

    def test_has_scroll_to_item(self) -> None:
        from guidewire.backends.types import DesktopAction

        assert hasattr(DesktopAction, "SCROLL_TO_ITEM")
        assert DesktopAction.SCROLL_TO_ITEM.value == "scroll_to_item"

    def test_scroll_to_item_in_enum(self) -> None:
        from guidewire.backends.types import DesktopAction

        assert DesktopAction("scroll_to_item") == DesktopAction.SCROLL_TO_ITEM

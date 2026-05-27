"""Tests for CDP Page domain wrapper.

Validates :class:`PageDomain` — navigate, reload, enable/disable,
frame tree, layout metrics, screenshot, etc.
"""

from typing import Any
from unittest.mock import MagicMock

from guidewire.cdp.domains.page import PageDomain
from guidewire.models import Bounds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(responses: list[dict[str, Any]] | None = None) -> MagicMock:
    session = MagicMock()
    responses = responses or [{}]
    session.send_command.side_effect = responses
    return session


# ---------------------------------------------------------------------------
# Enable/Disable
# ---------------------------------------------------------------------------


class TestPageEnableDisable:
    """Tests for enable/disable."""

    def test_enable(self) -> None:
        session = _make_session()
        domain = PageDomain(session)
        domain.enable()
        session.send_command.assert_called_with(
            "Page.enable", None, timeout=None
        )

    def test_disable(self) -> None:
        session = _make_session()
        domain = PageDomain(session)
        domain.disable()
        session.send_command.assert_called_with(
            "Page.disable", None, timeout=None
        )


# ---------------------------------------------------------------------------
# Navigate
# ---------------------------------------------------------------------------


class TestPageNavigate:
    """Tests for navigate."""

    def test_navigate_basic(self) -> None:
        response = {"frameId": "main", "loaderId": "loader-1"}
        session = _make_session([response])
        domain = PageDomain(session)

        result = domain.navigate("https://example.com")

        assert result["frameId"] == "main"
        assert result["loaderId"] == "loader-1"

        call_args = session.send_command.call_args
        assert call_args[0][0] == "Page.navigate"
        assert call_args[0][1] == {"url": "https://example.com"}

    def test_navigate_with_options(self) -> None:
        session = _make_session([{"frameId": "f", "loaderId": "l"}])
        domain = PageDomain(session)

        domain.navigate(
            "https://example.com",
            referrer="https://google.com",
            transition_type="link",
            frame_id="frame-1",
        )

        params = session.send_command.call_args[0][1]
        assert params["referrer"] == "https://google.com"
        assert params["transitionType"] == "link"
        assert params["frameId"] == "frame-1"


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------


class TestPageReload:
    """Tests for reload."""

    def test_reload_default(self) -> None:
        session = _make_session()
        domain = PageDomain(session)

        domain.reload()

        params = session.send_command.call_args[0][1]
        assert params["ignoreCache"] is False

    def test_reload_ignore_cache(self) -> None:
        session = _make_session()
        domain = PageDomain(session)

        domain.reload(ignore_cache=True)

        params = session.send_command.call_args[0][1]
        assert params["ignoreCache"] is True

    def test_reload_with_script(self) -> None:
        session = _make_session()
        domain = PageDomain(session)

        domain.reload(script_to_evaluate_on_load="console.log('hi')")

        params = session.send_command.call_args[0][1]
        assert params["scriptToEvaluateOnLoad"] == "console.log('hi')"


# ---------------------------------------------------------------------------
# Frame tree
# ---------------------------------------------------------------------------


class TestPageGetFrameTree:
    """Tests for get_frame_tree."""

    def test_returns_frame_tree_dataclass(self) -> None:
        response = {
            "frameTree": {
                "frame": {"id": "root", "url": "https://example.com"},
                "childFrames": [
                    {"frame": {"id": "child-1", "url": "https://iframe.com"}},
                ],
            }
        }
        session = _make_session([response])
        domain = PageDomain(session)

        from guidewire.cdp._types import FrameTree

        tree = domain.get_frame_tree()
        assert isinstance(tree, FrameTree)
        assert tree.frame["id"] == "root"
        assert len(tree.child_frames) == 1
        assert tree.child_frames[0].frame["id"] == "child-1"

    def test_empty_tree(self) -> None:
        session = _make_session([{"frameTree": {}}])
        domain = PageDomain(session)

        tree = domain.get_frame_tree()
        assert tree.frame == {}
        assert len(tree.child_frames) == 0


# ---------------------------------------------------------------------------
# Layout metrics
# ---------------------------------------------------------------------------


class TestPageGetLayoutMetrics:
    """Tests for get_layout_metrics and get_content_bounds."""

    def test_get_layout_metrics(self) -> None:
        response = {
            "contentSize": {"width": 1200, "height": 800},
            "cssContentSize": {"width": 1200, "height": 800},
        }
        session = _make_session([response])
        domain = PageDomain(session)

        result = domain.get_layout_metrics()
        assert result["contentSize"]["width"] == 1200

    def test_get_content_bounds(self) -> None:
        response = {"contentSize": {"width": 1200, "height": 800}}
        session = _make_session([response])
        domain = PageDomain(session)

        bounds = domain.get_content_bounds()
        assert isinstance(bounds, Bounds)
        assert bounds.width == 1200.0
        assert bounds.height == 800.0
        assert bounds.x == 0.0

    def test_get_content_bounds_no_data(self) -> None:
        session = _make_session([{}])
        domain = PageDomain(session)

        bounds = domain.get_content_bounds()
        assert bounds is None

    def test_get_content_bounds_uses_css_fallback(self) -> None:
        response = {"cssContentSize": {"width": 800, "height": 600}}
        session = _make_session([response])
        domain = PageDomain(session)

        bounds = domain.get_content_bounds()
        assert bounds is not None
        assert bounds.width == 800.0


# ---------------------------------------------------------------------------
# Other Page methods
# ---------------------------------------------------------------------------


class TestPageOtherMethods:
    """Tests for bring_to_front, close, lifecycle events."""

    def test_bring_to_front(self) -> None:
        session = _make_session()
        domain = PageDomain(session)
        domain.bring_to_front()
        session.send_command.assert_called_with(
            "Page.bringToFront", None, timeout=None
        )

    def test_close(self) -> None:
        session = _make_session()
        domain = PageDomain(session)
        domain.close()
        session.send_command.assert_called_with(
            "Page.close", None, timeout=None
        )

    def test_set_lifecycle_events_enabled(self) -> None:
        session = _make_session()
        domain = PageDomain(session)
        domain.set_lifecycle_events_enabled(True)
        session.send_command.assert_called_with(
            "Page.setLifecycleEventsEnabled", {"enabled": True}, timeout=None
        )

    def test_get_navigation_history(self) -> None:
        response = {"currentIndex": 0, "entries": [{"id": 1, "url": "https://example.com"}]}
        session = _make_session([response])
        domain = PageDomain(session)

        history = domain.get_navigation_history()
        assert history["currentIndex"] == 0
        assert len(history["entries"]) == 1


class TestPageScreenshot:
    """Tests for capture_screenshot."""

    def test_screenshot_returns_base64(self) -> None:
        session = _make_session([{"data": "base64data=="}])
        domain = PageDomain(session)

        data = domain.capture_screenshot()
        assert data == "base64data=="

    def test_screenshot_with_format_and_quality(self) -> None:
        session = _make_session([{"data": "jpgdata=="}])
        domain = PageDomain(session)

        domain.capture_screenshot(format="jpeg", quality=80)

        params = session.send_command.call_args[0][1]
        assert params["format"] == "jpeg"
        assert params["quality"] == 80

    def test_screenshot_with_clip(self) -> None:
        session = _make_session([{"data": "data=="}])
        domain = PageDomain(session)

        clip = {"x": 0, "y": 0, "width": 100, "height": 100, "scale": 1}
        domain.capture_screenshot(clip=clip)

        params = session.send_command.call_args[0][1]
        assert params["clip"] == clip

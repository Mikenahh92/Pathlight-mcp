"""CDP Page domain wrapper.

Provides :class:`PageDomain` — typed methods for the CDP ``Page`` domain
that control page navigation, lifecycle, and frame management.

Key methods:
    - :meth:`enable` / :meth:`disable` — enable/disable domain events
    - :meth:`navigate` — navigate to a URL
    - :meth:`reload` — reload the current page
    - :meth:`get_frame_tree` — get the frame hierarchy
    - :meth:`get_layout_metrics` — get page layout dimensions
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from guidewire.cdp.domains._base import CDPDomain
from guidewire.models import Bounds

if TYPE_CHECKING:
    from guidewire.cdp.session import CDPSession

__all__ = ["PageDomain"]

logger = logging.getLogger(__name__)


class PageDomain(CDPDomain):
    """Typed wrapper for the CDP ``Page`` domain.

    Controls page navigation, lifecycle, and frame management.

    Args:
        session: The active CDP session to send commands through.
    """

    domain = "Page"

    def __init__(self, session: CDPSession) -> None:
        super().__init__(session)

    def enable(self) -> None:
        """Enable Page domain events.

        Sends ``Page.enable`` to start receiving frame and lifecycle events.
        """
        self._send(self._method("enable"))

    def disable(self) -> None:
        """Disable Page domain events.

        Sends ``Page.disable``.
        """
        self._send(self._method("disable"))

    def navigate(
        self,
        url: str,
        *,
        referrer: str | None = None,
        transition_type: str | None = None,
        frame_id: str | None = None,
    ) -> dict[str, str]:
        """Navigate the page to a URL.

        Sends ``Page.navigate``.

        Args:
            url: URL to navigate to.
            referrer: Optional referrer URL.
            transition_type: Transition type hint.
            frame_id: Target frame ID (main frame if ``None``).

        Returns:
            Dict with ``frameId`` and ``loaderId``.
        """
        params: dict[str, Any] = {"url": url}
        if referrer is not None:
            params["referrer"] = referrer
        if transition_type is not None:
            params["transitionType"] = transition_type
        if frame_id is not None:
            params["frameId"] = frame_id

        result = self._send(self._method("navigate"), params)
        return {
            "frameId": result.get("frameId", ""),
            "loaderId": result.get("loaderId", ""),
        }

    def reload(
        self,
        *,
        ignore_cache: bool = False,
        script_to_evaluate_on_load: str | None = None,
    ) -> None:
        """Reload the current page.

        Sends ``Page.reload``.

        Args:
            ignore_cache: If ``True``, bypass the browser cache.
            script_to_evaluate_on_load: Optional script to run after load.
        """
        params: dict[str, Any] = {"ignoreCache": ignore_cache}
        if script_to_evaluate_on_load is not None:
            params["scriptToEvaluateOnLoad"] = script_to_evaluate_on_load

        self._send(self._method("reload"), params)

    def get_frame_tree(self) -> list[dict[str, Any]]:
        """Get the current frame tree.

        Sends ``Page.getFrameTree``.

        Returns:
            List of frame descriptors from the ``frameTree``.
        """
        result = self._send(self._method("getFrameTree"))
        tree = result.get("frameTree", {})
        frames = [tree.get("frame", {})]
        for child in tree.get("childFrames", []):
            frames.append(child.get("frame", {}))
        return frames

    def get_layout_metrics(self) -> dict[str, Any]:
        """Get page layout metrics.

        Sends ``Page.getLayoutMetrics``.

        Returns:
            Dict with ``contentSize``, ``cssContentSize``,
            ``cssVisualViewport``, and ``visualViewport`` keys.
        """
        result = self._send(self._method("getLayoutMetrics"))
        return result

    def get_content_bounds(self) -> Bounds | None:
        """Get the page content bounding rectangle.

        Sends ``Page.getLayoutMetrics`` and extracts the content size.

        Returns:
            The content :class:`~guidewire.models.Bounds`, or ``None``.
        """
        result = self.get_layout_metrics()
        content_size = result.get("contentSize") or result.get("cssContentSize")
        if not content_size:
            return None

        return Bounds(
            x=0.0,
            y=0.0,
            width=float(content_size.get("width", 0)),
            height=float(content_size.get("height", 0)),
        )

    def bring_to_front(self) -> None:
        """Bring the page to the foreground.

        Sends ``Page.bringToFront``.
        """
        self._send(self._method("bringToFront"))

    def close(self) -> None:
        """Close the page.

        Sends ``Page.close``.
        """
        self._send(self._method("close"))

    def set_lifecycle_events_enabled(self, enabled: bool) -> None:
        """Enable or disable lifecycle events.

        Sends ``Page.setLifecycleEventsEnabled``.

        Args:
            enabled: Whether to enable lifecycle events.
        """
        self._send(
            self._method("setLifecycleEventsEnabled"),
            {"enabled": enabled},
        )

    def get_navigation_history(self) -> dict[str, Any]:
        """Get the page's navigation history.

        Sends ``Page.getNavigationHistory``.

        Returns:
            Dict with ``currentIndex`` and ``entries`` keys.
        """
        return self._send(self._method("getNavigationHistory"))

    def capture_screenshot(
        self,
        *,
        format: str = "png",
        quality: int | None = None,
        clip: dict[str, Any] | None = None,
    ) -> str:
        """Capture a screenshot of the page.

        Sends ``Page.captureScreenshot``.

        Args:
            format: Image format (``"png"`` or ``"jpeg"``).
            quality: JPEG quality (0-100, ignored for PNG).
            clip: Optional clip region dict.

        Returns:
            Base64-encoded screenshot data.
        """
        params: dict[str, Any] = {"format": format}
        if quality is not None:
            params["quality"] = quality
        if clip is not None:
            params["clip"] = clip

        result = self._send(self._method("captureScreenshot"), params)
        return result.get("data", "")

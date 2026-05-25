"""Tests for MCP resources endpoint (GW-113).

Validates that:
- All resources are registered and discoverable via list_resources.
- Each resource has the correct URI, name, and MIME type.
- Reading each resource returns non-empty markdown content.
- Resource content contains expected key terms/topics.
- The resources registry module has the expected structure.
- Resources are accessible from GuidewireServer after register_resources().
- The error-recovery resource dynamically includes all registered error codes.
"""

import importlib

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.resources import _RESOURCE_MODULES, register_all
from guidewire.server import GuidewireServer

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def mcp_server():
    """Return a FastMCP instance with all resources registered."""
    mcp = FastMCP(name="test")
    register_all(mcp)
    return mcp


@pytest.fixture()
def guidewire_server():
    """Return a GuidewireServer with tools and resources registered."""
    srv = GuidewireServer()
    srv.register_tools()
    srv.register_resources()
    return srv


# -- Expected resources -------------------------------------------------------

EXPECTED_RESOURCES = [
    {
        "uri": "guidewire://browser-limitations",
        "name": "browser-limitations",
        "title": "Browser Limitations & Web Tool Caveats",
        "mime_type": "text/markdown",
        "content_keywords": [
            "web_connect",
            "web_navigate",
            "web_evaluate",
            "CDP",
            "remote-debugging-port",
            "Rate-limited",
        ],
    },
    {
        "uri": "guidewire://tool-usage",
        "name": "tool-usage",
        "title": "Guidewire Tool Usage Guide",
        "mime_type": "text/markdown",
        "content_keywords": [
            "desktop.list_windows",
            "desktop.snapshot",
            "desktop.find",
            "desktop.click",
            "desktop.type_text",
            "desktop.multi_action",
            "desktop.wait_for",
        ],
    },
    {
        "uri": "guidewire://error-recovery",
        "name": "error-recovery",
        "title": "Error Recovery Guide",
        "mime_type": "text/markdown",
        "content_keywords": [
            "stale_element_reference",
            "element_not_found",
            "backend_unavailable",
            "action_not_supported",
            "permission_required",
        ],
    },
]


# -- Resource registration tests ----------------------------------------------


class TestResourceRegistration:
    """Tests for resource registration and discovery via list_resources."""

    async def test_all_resources_registered(self, mcp_server):
        """All expected resources should be discoverable via list_resources."""
        resources = await mcp_server.list_resources()
        uris = {str(r.uri) for r in resources}
        expected_uris = {spec["uri"] for spec in EXPECTED_RESOURCES}
        assert uris == expected_uris

    async def test_resource_count(self, mcp_server):
        """Exactly the expected number of resources should be registered."""
        resources = await mcp_server.list_resources()
        assert len(resources) == len(EXPECTED_RESOURCES)

    @pytest.mark.parametrize("spec", EXPECTED_RESOURCES, ids=lambda s: s["uri"])
    async def test_resource_has_name(self, mcp_server, spec):
        """Each resource should have the correct name."""
        resources = await mcp_server.list_resources()
        resource_map = {str(r.uri): r for r in resources}
        resource = resource_map[spec["uri"]]
        assert resource.name == spec["name"]

    @pytest.mark.parametrize("spec", EXPECTED_RESOURCES, ids=lambda s: s["uri"])
    async def test_resource_has_mime_type(self, mcp_server, spec):
        """Each resource should have the correct MIME type."""
        resources = await mcp_server.list_resources()
        resource_map = {str(r.uri): r for r in resources}
        resource = resource_map[spec["uri"]]
        assert resource.mimeType == spec["mime_type"]


# -- Resource content tests ---------------------------------------------------


class TestResourceContent:
    """Tests for reading resource content."""

    @pytest.mark.parametrize("spec", EXPECTED_RESOURCES, ids=lambda s: s["uri"])
    async def test_resource_content_not_empty(self, mcp_server, spec):
        """Each resource should return non-empty content."""
        content = await mcp_server.read_resource(spec["uri"])
        assert content is not None
        text = str(content)
        assert len(text) > 0

    @pytest.mark.parametrize("spec", EXPECTED_RESOURCES, ids=lambda s: s["uri"])
    async def test_resource_content_contains_keywords(self, mcp_server, spec):
        """Each resource content should contain expected keywords."""
        content = await mcp_server.read_resource(spec["uri"])
        text = str(content)
        for keyword in spec["content_keywords"]:
            assert keyword in text, f"Resource {spec['uri']} missing keyword '{keyword}'"


# -- Resource module registry tests ------------------------------------------


class TestResourceModuleRegistry:
    """Tests for the per-module register(mcp) pattern."""

    @pytest.mark.parametrize("module_name", _RESOURCE_MODULES)
    def test_module_has_register_function(self, module_name):
        """Each resource module should export a callable ``register``."""
        mod = importlib.import_module(module_name, package="guidewire.resources")
        assert callable(getattr(mod, "register", None)), (
            f"{module_name} is missing a 'register' function"
        )

    def test_resource_module_count(self):
        """There should be exactly the expected number of resource modules."""
        assert len(_RESOURCE_MODULES) == len(EXPECTED_RESOURCES)


# -- Server integration tests -------------------------------------------------


class TestServerResources:
    """Tests for resources registered through GuidewireServer."""

    async def test_server_register_resources(self, guidewire_server):
        """GuidewireServer should register all resources."""
        resources = await guidewire_server.mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        expected_uris = {spec["uri"] for spec in EXPECTED_RESOURCES}
        assert uris == expected_uris

    async def test_server_tools_still_registered(self, guidewire_server):
        """Registering resources should not affect tool registration."""
        tools = await guidewire_server.mcp.list_tools()
        tool_names = {t.name for t in tools}
        # Verify at least the core tools are still there
        assert "desktop.click" in tool_names
        assert "desktop.snapshot" in tool_names
        assert "desktop.find" in tool_names

    async def test_server_resource_readable(self, guidewire_server):
        """Resources registered through GuidewireServer should be readable."""
        content = await guidewire_server.mcp.read_resource("guidewire://tool-usage")
        text = str(content)
        assert "desktop.snapshot" in text
        assert "desktop.click" in text


# -- Error recovery dynamic content tests -------------------------------------


class TestErrorRecoveryDynamicContent:
    """Tests for the dynamically-generated error recovery resource."""

    async def test_all_error_codes_present(self, mcp_server):
        """All registered error codes should appear in the recovery guide."""
        from guidewire.hints import _HINT_REGISTRY

        content = await mcp_server.read_resource("guidewire://error-recovery")
        text = str(content)
        for code in _HINT_REGISTRY:
            assert code in text, f"Error code '{code}' missing from recovery guide"

    async def test_hints_appear_in_content(self, mcp_server):
        """Registered hint text should appear in the recovery guide."""
        from guidewire.hints import _HINT_REGISTRY

        content = await mcp_server.read_resource("guidewire://error-recovery")
        text = str(content)
        # Check at least one hint from each code appears
        for code, hints in _HINT_REGISTRY.items():
            if hints:
                # At least one hint should be present (check first hint's first word)
                first_hint_start = hints[0].split()[0]
                assert first_hint_start in text, f"Hint for '{code}' missing from recovery guide"


# -- Browser limitations content tests ----------------------------------------


class TestBrowserLimitationsContent:
    """Tests for the browser limitations resource content quality."""

    async def test_includes_connection_requirements(self, mcp_server):
        """Browser limitations should cover connection requirements."""
        content = await mcp_server.read_resource("guidewire://browser-limitations")
        text = str(content)
        assert "remote-debugging-port" in text
        assert "CDP" in text

    async def test_includes_rate_limiting(self, mcp_server):
        """Browser limitations should document rate limiting."""
        content = await mcp_server.read_resource("guidewire://browser-limitations")
        text = str(content)
        assert "rate" in text.lower()

    async def test_includes_recommended_workflow(self, mcp_server):
        """Browser limitations should include a recommended workflow."""
        content = await mcp_server.read_resource("guidewire://browser-limitations")
        text = str(content)
        assert "Recommended Workflow" in text or "workflow" in text.lower()


# -- Tool usage content tests -------------------------------------------------


class TestToolUsageContent:
    """Tests for the tool usage resource content quality."""

    async def test_covers_all_tool_categories(self, mcp_server):
        """Tool usage should cover all major tool categories."""
        content = await mcp_server.read_resource("guidewire://tool-usage")
        text = str(content)
        categories = [
            "Window Management",
            "Tree Inspection",
            "Element Interaction",
            "Clipboard",
            "Batch Operations",
            "Application Launch",
            "Web Tools",
        ]
        for category in categories:
            assert category in text, f"Missing tool category: {category}"

    async def test_includes_element_reference_explanation(self, mcp_server):
        """Tool usage should explain element references."""
        content = await mcp_server.read_resource("guidewire://tool-usage")
        text = str(content)
        assert "element_ref" in text or "element reference" in text.lower()
        assert "stale" in text.lower()


# -- Error handling tests (TC-14 / AC-3) --------------------------------------


class TestResourceErrorHandling:
    """Tests for resource error handling."""

    async def test_read_nonexistent_resource_raises_error(self, mcp_server):
        """Reading a non-existent URI should raise an error (TC-14, AC-3)."""
        with pytest.raises((ValueError, RuntimeError)):
            await mcp_server.read_resource("guidewire://nonexistent")

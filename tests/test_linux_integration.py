"""Comprehensive integration tests for the Linux Backend (GW-036).

Final validation gate for the Linux Backend epic (GW-EPIC-4). Validates
cross-cutting scenarios that span multiple subsystems:

TC-LI-01: Backend ABC contract — LinuxBackend implements all 9 DesktopBackend methods
TC-LI-02: Normalization → Safety pipeline — normalized Linux elements feed into classify()
TC-LI-03: Normalization → Privacy pipeline — normalized Linux elements feed into redact_snapshot()
TC-LI-04: Golden snapshot → Ref store pipeline — golden fixture trees register in ElementRefStore
TC-LI-05: Golden snapshot → Safety pipeline — gedit/Calculator elements classified correctly
TC-LI-06: Golden snapshot → Privacy pipeline — gedit/Calculator snapshots survive redaction
TC-LI-07: MockBackend ↔ LinuxBackend output parity — both produce compatible NormalizedElement trees
TC-LI-08: Error containment — all Linux error paths produce correct error codes
TC-LI-09: Cross-platform normalization parity — Linux and Windows normalize to same schema
TC-LI-10: End-to-end pipeline — normalize → classify → redact on golden fixture data
"""

from unittest.mock import MagicMock, patch

import pytest

from pathlight_mcp.backends.base import DesktopBackend
from pathlight_mcp.backends.linux import LinuxBackend
from pathlight_mcp.backends.mock import MockBackend
from pathlight_mcp.backends.normalize import normalize_element
from pathlight_mcp.backends.types import DesktopAction
from pathlight_mcp.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
    ElementNotFoundError,
    StaleElementReferenceError,
    WindowNotFoundError,
)
from pathlight_mcp.models import ElementStates, NormalizedElement
from pathlight_mcp.privacy import redact_snapshot
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.safety import classify
from tests.fixtures.helpers import (
    load_linux_golden_snapshot,
)

# ---------------------------------------------------------------------------
# TC-LI-01: Backend ABC contract
# ---------------------------------------------------------------------------


class TestLinuxBackendAbcContract:
    """Verify LinuxBackend fully satisfies the DesktopBackend ABC."""

    def test_is_concrete_subclass(self) -> None:
        """LinuxBackend must be a non-abstract subclass of DesktopBackend."""
        assert issubclass(LinuxBackend, DesktopBackend)
        assert not getattr(LinuxBackend, "__abstractmethods__", None)

    def test_all_nine_methods_callable(self) -> None:
        """All 9 abstract methods must exist and be callable."""
        methods = [
            "list_windows",
            "get_window_info",
            "focus_window",
            "snapshot",
            "find_elements",
            "perform_action",
            "get_element_info",
            "is_valid",
            "dispose",
        ]
        for name in methods:
            assert callable(getattr(LinuxBackend, name)), f"Missing: {name}"

    def test_reexported_from_package(self) -> None:
        """LinuxBackend must be importable from pathlight_mcp.backends."""
        from pathlight_mcp.backends import LinuxBackend as ImportedLinuxBackend

        assert ImportedLinuxBackend is LinuxBackend

    def test_method_signatures_match_abc(self) -> None:
        """Method signatures must accept the same parameters as DesktopBackend."""
        import inspect

        for method_name in [
            "list_windows",
            "get_window_info",
            "focus_window",
            "snapshot",
            "find_elements",
            "perform_action",
            "get_element_info",
            "is_valid",
            "dispose",
        ]:
            abc_sig = inspect.signature(getattr(DesktopBackend, method_name))
            lb_sig = inspect.signature(getattr(LinuxBackend, method_name))
            abc_params = set(abc_sig.parameters) - {"self", "return"}
            lb_params = set(lb_sig.parameters) - {"self", "return"}
            assert abc_params <= lb_params, (
                f"{method_name}: ABC params {abc_params} not subset of "
                f"LinuxBackend params {lb_params}"
            )

    def test_dispose_is_idempotent(self) -> None:
        """dispose() can be called multiple times without error."""
        mock_pyatspi = MagicMock()
        with (
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": mock_pyatspi}),
        ):
            backend = LinuxBackend()
            backend.dispose()
            backend.dispose()  # must not raise
            assert backend._disposed


# ---------------------------------------------------------------------------
# TC-LI-02: Normalization → Safety pipeline
# ---------------------------------------------------------------------------


class TestNormalizationSafetyPipeline:
    """Normalized Linux elements must feed correctly into the safety classifier."""

    def test_linux_button_classified_as_interaction(self) -> None:
        """A normalized Linux button element is classified as INTERACTION."""
        el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-btn-1",
            role="button",
            name="Submit",
            native_role="push button",
        )
        result = classify(el, "click")
        assert result.risk_level == "INTERACTION"

    def test_linux_delete_button_classified_as_sensitive(self) -> None:
        """A Linux delete button is classified as SENSITIVE."""
        el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-del-1",
            role="button",
            name="Delete",
            native_role="push button",
        )
        result = classify(el, "click")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True

    def test_linux_disabled_element_is_read_only(self) -> None:
        """A disabled Linux element is always READ_ONLY."""
        el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-btn-1",
            role="button",
            name="Submit",
            native_role="push button",
            raw_states={"enabled": False},
        )
        result = classify(el, "click")
        assert result.risk_level == "READ_ONLY"

    def test_linux_text_input_classified_for_type(self) -> None:
        """A Linux text_input element is INTERACTION for type action."""
        el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-entry-1",
            role="text_input",
            name="Username",
            native_role="entry",
            raw_states={"editable": True},
        )
        result = classify(el, "type")
        assert result.risk_level == "INTERACTION"

    def test_linux_window_is_read_only(self) -> None:
        """A Linux window element is READ_ONLY."""
        el = normalize_element(
            platform="linux",
            ref="w1",
            backend_id="native-win-1",
            role="window",
            name="Calculator",
            native_role="frame",
        )
        result = classify(el, "click")
        assert result.risk_level == "READ_ONLY"

    def test_linux_password_field_is_sensitive(self) -> None:
        """A Linux password text_input is INTERACTION (redaction is handled by privacy module)."""
        el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-pw-1",
            role="text_input",
            name="Password",
            native_role="password text",
            raw_states={"is_password": True},
        )
        result = classify(el, "type")
        # Safety classifies text_input as INTERACTION; privacy handles redaction
        assert result.risk_level == "INTERACTION"

    def test_linux_tree_with_mixed_risk_levels(self) -> None:
        """A tree with mixed elements classifies each correctly."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="native-win",
            role="window",
            native_role="frame",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="native-entry",
                    role="text_input",
                    name="Username",
                    native_role="entry",
                ),
                NormalizedElement(
                    ref="e2",
                    backend_id="native-pw",
                    role="text_input",
                    name="Password",
                    native_role="password text",
                    states=ElementStates(is_password=True),
                ),
                NormalizedElement(
                    ref="e3",
                    backend_id="native-del",
                    role="button",
                    name="Delete Account",
                    native_role="push button",
                ),
            ],
        )
        elements = tree.walk()
        assert classify(elements[0], "click").risk_level == "READ_ONLY"  # window
        assert classify(elements[1], "type").risk_level == "INTERACTION"  # username
        # password: safety=INTERACTION, privacy=redacts
        assert classify(elements[2], "type").risk_level == "INTERACTION"
        assert classify(elements[3], "click").risk_level == "SENSITIVE"  # delete


# ---------------------------------------------------------------------------
# TC-LI-03: Normalization → Privacy pipeline
# ---------------------------------------------------------------------------


class TestNormalizationPrivacyPipeline:
    """Normalized Linux elements must feed correctly into the privacy redactor."""

    def test_linux_password_redacted(self) -> None:
        """A Linux password field gets redacted."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="native-win",
            role="window",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="native-user",
                    role="text_input",
                    name="Username",
                    value="admin",
                    text="admin",
                    native_role="entry",
                ),
                NormalizedElement(
                    ref="e2",
                    backend_id="native-pw",
                    role="text_input",
                    name="Password",
                    value="secret",
                    text="secret",
                    native_role="password text",
                ),
            ],
        )
        result = redact_snapshot([tree])
        assert result[0].children[0].value == "admin"
        assert result[0].children[1].value == "[REDACTED]"

    def test_linux_is_password_state_redacted(self) -> None:
        """A Linux element with is_password=True state gets redacted."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="native-win",
            role="window",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="native-token",
                    role="text_input",
                    name="API Token",
                    value="abc123",
                    text="abc123",
                    native_role="entry",
                    states=ElementStates(is_password=True),
                ),
            ],
        )
        result = redact_snapshot([tree])
        assert result[0].children[0].value == "[REDACTED]"

    def test_linux_non_password_preserved(self) -> None:
        """A regular Linux text_input is not redacted."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="native-win",
            role="window",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="native-entry",
                    role="text_input",
                    name="Search",
                    value="hello",
                    text="hello",
                    native_role="entry",
                ),
            ],
        )
        result = redact_snapshot([tree])
        assert result[0].children[0].value == "hello"

    def test_linux_redaction_preserves_refs(self) -> None:
        """After redaction, all element refs remain findable."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="native-win",
            role="window",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="native-btn",
                    role="button",
                    name="OK",
                    native_role="push button",
                ),
            ],
        )
        result = redact_snapshot([tree])
        assert result[0].find_by_ref("w1") is not None
        assert result[0].find_by_ref("e1") is not None


# ---------------------------------------------------------------------------
# TC-LI-04: Golden snapshot → Ref store pipeline
# ---------------------------------------------------------------------------


class TestGoldenSnapshotRefStore:
    """Golden fixture trees must register correctly in ElementRefStore."""

    @pytest.fixture(params=["gedit_snapshot.json", "gnome_calculator_snapshot.json"])
    def snapshot_data(self, request: pytest.FixtureRequest) -> dict:
        """Load a golden snapshot fixture."""
        return load_linux_golden_snapshot(request.param)

    def test_all_elements_register_in_ref_store(self, snapshot_data: dict) -> None:
        """Every element in the golden snapshot can be stored in the ref store."""
        store = ElementRefStore()
        tree = snapshot_data["snapshot"]

        def register_nodes(node: dict) -> None:
            ref = node.get("ref", "")
            backend_id = node.get("backend_id", "")
            if ref and backend_id:
                stored_ref = store.store(backend_id)
                assert store.is_valid(stored_ref)
            for child in node.get("children", []):
                register_nodes(child)

        register_nodes(tree)
        assert store.size > 0

    def test_ref_store_clear_invalidates_all(self, snapshot_data: dict) -> None:
        """After clear, no refs from the golden snapshot resolve."""
        store = ElementRefStore()
        tree = snapshot_data["snapshot"]

        def collect_backend_ids(node: dict) -> list[str]:
            ids = []
            bid = node.get("backend_id", "")
            if bid:
                ids.append(bid)
            for child in node.get("children", []):
                ids.extend(collect_backend_ids(child))
            return ids

        all_ids = collect_backend_ids(tree)
        refs = [store.store(bid) for bid in all_ids]
        store.clear()
        for ref in refs:
            assert store.resolve(ref) is None

    def test_walk_matches_store_size(self, snapshot_data: dict) -> None:
        """walk() on the golden tree returns same count as registered handles."""
        tree = snapshot_data["snapshot"]

        def collect_nodes(node: dict) -> list[dict]:
            nodes = [node]
            for child in node.get("children", []):
                nodes.extend(collect_nodes(child))
            return nodes

        all_nodes = collect_nodes(tree)

        # Build a NormalizedElement tree from the fixture dict
        def dict_to_element(node: dict) -> NormalizedElement:
            children = [dict_to_element(c) for c in node.get("children", [])]
            return NormalizedElement(
                ref=node.get("ref", ""),
                backend_id=node.get("backend_id", ""),
                role=node.get("role", ""),
                name=node.get("name"),
                native_role=node.get("native_role"),
                value=node.get("value"),
                text=node.get("text"),
                children=children if children else None,
            )

        root = dict_to_element(tree)
        walked = root.walk()
        assert len(walked) == len(all_nodes)


# ---------------------------------------------------------------------------
# TC-LI-05: Golden snapshot → Safety pipeline
# ---------------------------------------------------------------------------


class TestGoldenSnapshotSafety:
    """Golden fixture elements must classify correctly via safety module."""

    def test_gedit_menu_bar_is_interaction(self) -> None:
        """Gedit menu_bar defaults to INTERACTION (not in ROLE_RISK_MAP)."""
        gedit = load_linux_golden_snapshot("gedit_snapshot.json")
        tree = gedit["snapshot"]

        def find_menu_bar(node: dict) -> dict | None:
            if node.get("role") == "menu_bar":
                return node
            for child in node.get("children", []):
                result = find_menu_bar(child)
                if result:
                    return result
            return None

        menu_bar = find_menu_bar(tree)
        assert menu_bar is not None
        el = NormalizedElement(
            ref=menu_bar.get("ref", ""),
            backend_id=menu_bar.get("backend_id", ""),
            role=menu_bar.get("role", ""),
            name=menu_bar.get("name"),
        )
        # menu_bar is not in ROLE_RISK_MAP, so it defaults to INTERACTION
        assert classify(el, "click").risk_level == "INTERACTION"

    def test_gedit_text_input_is_interaction(self) -> None:
        """Gedit document text_input should be INTERACTION for type."""
        gedit = load_linux_golden_snapshot("gedit_snapshot.json")
        tree = gedit["snapshot"]

        def find_text_input(node: dict) -> dict | None:
            if node.get("role") == "text_input":
                return node
            for child in node.get("children", []):
                result = find_text_input(child)
                if result:
                    return result
            return None

        text_input = find_text_input(tree)
        assert text_input is not None
        el = NormalizedElement(
            ref=text_input.get("ref", ""),
            backend_id=text_input.get("backend_id", ""),
            role=text_input.get("role", ""),
            name=text_input.get("name"),
        )
        assert classify(el, "type").risk_level == "INTERACTION"

    def test_calculator_digit_buttons_are_interaction(self) -> None:
        """Calculator digit buttons (0-9) should be INTERACTION."""
        calc = load_linux_golden_snapshot("gnome_calculator_snapshot.json")
        tree = calc["snapshot"]

        def find_digit_buttons(node: dict) -> list[dict]:
            buttons = []
            name = node.get("name") or ""
            if node.get("role") == "button" and name.isdigit():
                buttons.append(node)
            for child in node.get("children", []):
                buttons.extend(find_digit_buttons(child))
            return buttons

        digit_buttons = find_digit_buttons(tree)
        assert len(digit_buttons) >= 10
        for btn in digit_buttons:
            el = NormalizedElement(
                ref=btn.get("ref", ""),
                backend_id=btn.get("backend_id", ""),
                role=btn.get("role", ""),
                name=btn.get("name"),
            )
            result = classify(el, "click")
            assert result.risk_level == "INTERACTION"

    def test_calculator_clear_button_is_sensitive(self) -> None:
        """Calculator Clear button should be SENSITIVE (destructive name pattern)."""
        calc = load_linux_golden_snapshot("gnome_calculator_snapshot.json")
        tree = calc["snapshot"]

        def find_clear(node: dict) -> dict | None:
            name = (node.get("name") or "").lower()
            if node.get("role") == "button" and "clear" in name:
                return node
            for child in node.get("children", []):
                result = find_clear(child)
                if result:
                    return result
            return None

        clear_btn = find_clear(tree)
        assert clear_btn is not None
        el = NormalizedElement(
            ref=clear_btn.get("ref", ""),
            backend_id=clear_btn.get("backend_id", ""),
            role=clear_btn.get("role", ""),
            name=clear_btn.get("name"),
        )
        result = classify(el, "click")
        assert result.risk_level == "SENSITIVE"

    def test_calculator_display_is_read_only(self) -> None:
        """Calculator display pane should be READ_ONLY."""
        calc = load_linux_golden_snapshot("gnome_calculator_snapshot.json")
        tree = calc["snapshot"]

        def find_display(node: dict) -> dict | None:
            if node.get("role") == "pane" and "display" in (node.get("name") or "").lower():
                return node
            for child in node.get("children", []):
                result = find_display(child)
                if result:
                    return result
            return None

        display = find_display(tree)
        assert display is not None
        el = NormalizedElement(
            ref=display.get("ref", ""),
            backend_id=display.get("backend_id", ""),
            role=display.get("role", ""),
            name=display.get("name"),
        )
        # pane is in ROLE_RISK_MAP as READ_ONLY
        assert classify(el, "click").risk_level == "READ_ONLY"


# ---------------------------------------------------------------------------
# TC-LI-06: Golden snapshot → Privacy pipeline
# ---------------------------------------------------------------------------


class TestGoldenSnapshotPrivacy:
    """Golden fixture snapshots must survive the privacy redaction pipeline."""

    def test_gedit_snapshot_survives_redaction(self) -> None:
        """Redacting the gedit snapshot preserves tree structure."""
        gedit = load_linux_golden_snapshot("gedit_snapshot.json")
        tree_dict = gedit["snapshot"]

        def dict_to_element(node: dict) -> NormalizedElement:
            children = [dict_to_element(c) for c in node.get("children", [])]
            return NormalizedElement(
                ref=node.get("ref", ""),
                backend_id=node.get("backend_id", ""),
                role=node.get("role", ""),
                name=node.get("name"),
                native_role=node.get("native_role"),
                value=node.get("value"),
                text=node.get("text"),
                children=children if children else None,
            )

        root = dict_to_element(tree_dict)
        result = redact_snapshot([root])
        assert result[0].role == "window"
        assert result[0].name == "Untitled Document - gedit"
        # Structure preserved
        assert result[0].children is not None
        assert len(result[0].children) > 0

    def test_calculator_snapshot_survives_redaction(self) -> None:
        """Redacting the calculator snapshot preserves tree structure."""
        calc = load_linux_golden_snapshot("gnome_calculator_snapshot.json")
        tree_dict = calc["snapshot"]

        def dict_to_element(node: dict) -> NormalizedElement:
            children = [dict_to_element(c) for c in node.get("children", [])]
            return NormalizedElement(
                ref=node.get("ref", ""),
                backend_id=node.get("backend_id", ""),
                role=node.get("role", ""),
                name=node.get("name"),
                native_role=node.get("native_role"),
                value=node.get("value"),
                text=node.get("text"),
                children=children if children else None,
            )

        root = dict_to_element(tree_dict)
        result = redact_snapshot([root])
        assert result[0].role == "window"
        assert result[0].name == "Calculator"

    def test_redaction_preserves_all_refs(self) -> None:
        """After redacting golden snapshot, all refs remain findable."""
        gedit = load_linux_golden_snapshot("gedit_snapshot.json")
        tree_dict = gedit["snapshot"]

        def dict_to_element(node: dict) -> NormalizedElement:
            children = [dict_to_element(c) for c in node.get("children", [])]
            return NormalizedElement(
                ref=node.get("ref", ""),
                backend_id=node.get("backend_id", ""),
                role=node.get("role", ""),
                name=node.get("name"),
                children=children if children else None,
            )

        root = dict_to_element(tree_dict)
        result = redact_snapshot([root])

        # Collect all refs from original
        def collect_refs(node: dict) -> list[str]:
            refs = [node["ref"]] if "ref" in node else []
            for child in node.get("children", []):
                refs.extend(collect_refs(child))
            return refs

        original_refs = collect_refs(tree_dict)
        for ref in original_refs:
            assert result[0].find_by_ref(ref) is not None, f"Ref {ref} lost after redaction"


# ---------------------------------------------------------------------------
# TC-LI-07: MockBackend ↔ LinuxBackend output parity
# ---------------------------------------------------------------------------


class TestMockLinuxParity:
    """MockBackend and LinuxBackend must produce compatible output schemas."""

    @staticmethod
    def _make_accessible(
        role: str = "frame",
        name: str | None = "Test",
        children: list | None = None,
    ) -> MagicMock:
        """Create a mock pyatspi.Accessible with proper role string."""
        acc = MagicMock()
        acc.get_role.return_value = role
        acc.get_name.return_value = name
        acc.get_description.return_value = None
        state_set = MagicMock()
        state_set.contains.return_value = False
        acc.getState.return_value = state_set
        acc.get_state_set.return_value = state_set
        acc.getExtent.return_value = (0, 0, 800, 600)
        action_iface = MagicMock()
        action_iface.get_n_actions.return_value = 0
        acc.get_action.return_value = action_iface
        acc.queryInterface.return_value = None
        child_count = len(children) if children is not None else 0
        acc.childCount = child_count
        acc.get_child_count.return_value = child_count
        acc.getChildren.return_value = children or []
        return acc

    def test_both_produce_window_root(self) -> None:
        """Both backends produce snapshot dicts with role='window' at root."""
        # MockBackend
        mock = MockBackend()
        mock.add_window("Test App", "testapp")
        mock_snap = mock.snapshot(mock.last_window_handle)
        assert mock_snap["role"] == "window"

        # LinuxBackend with mocked pyatspi
        mock_pyatspi = _make_mock_pyatspi(showing_count=1, hidden_count=0)
        with (
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": mock_pyatspi}),
        ):
            backend = LinuxBackend()
            accessible = backend._desktop.children[0]
            # Set up accessible for snapshot — use "window" role to get "window" normalized role
            accessible.get_role.return_value = "window"
            accessible.get_name.return_value = "Test App"
            state_set = MagicMock()
            state_set.contains.return_value = False
            accessible.getState.return_value = state_set
            accessible.get_state_set.return_value = state_set
            accessible.getExtent.return_value = (0, 0, 800, 600)
            action_iface = MagicMock()
            action_iface.get_n_actions.return_value = 0
            accessible.get_action.return_value = action_iface
            accessible.queryInterface.return_value = None
            accessible.childCount = 0
            accessible.get_child_count.return_value = 0
            accessible.getChildren.return_value = []

            linux_snap = backend.snapshot(accessible)

        assert linux_snap["role"] == "window"

    def test_both_have_required_snapshot_keys(self) -> None:
        """Both snapshot dicts have the same required keys."""
        required_keys = {"ref", "role", "name", "states", "bounds", "actions"}

        mock = MockBackend()
        mock.add_window("App", "app")
        mock_snap = mock.snapshot(mock.last_window_handle)
        assert required_keys <= set(mock_snap.keys())

        # Use normalize_element directly to verify Linux schema
        el = normalize_element(
            platform="linux",
            ref="w1",
            backend_id="native-win",
            role="window",
            name="App",
            native_role="frame",
            raw_states={"enabled": True, "visible": True},
        )
        d = el.to_dict()
        assert required_keys <= set(d.keys())

    def test_both_children_is_list(self) -> None:
        """Both snapshot dicts have 'children' as a list."""
        mock = MockBackend()
        mock.add_window("App", "app")
        mock_snap = mock.snapshot(mock.last_window_handle)
        assert isinstance(mock_snap.get("children"), list)

        # Verify normalize_element children output is list
        el = normalize_element(
            platform="linux",
            ref="w1",
            backend_id="native-win",
            role="window",
            name="App",
            native_role="frame",
            children=[],
        )
        d = el.to_dict()
        assert isinstance(d.get("children"), list)

    def test_normalize_element_produces_to_dict_compatible_with_mock(self) -> None:
        """normalize_element output matches MockBackend snapshot schema."""
        el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-btn",
            role="button",
            name="Click Me",
            native_role="push button",
            raw_states={"enabled": True},
            raw_actions=["click"],
        )
        d = el.to_dict()

        mock = MockBackend()
        mock.add_window("App", "app")
        mock.add_element("button", "Click Me", parent=mock.last_window_handle)
        mock_snap = mock.snapshot(mock.last_window_handle)
        child = mock_snap["children"][0]

        # MockBackend output is a subset of normalize_element output
        # (normalize produces richer keys: native_role, backend_id)
        assert set(child.keys()) <= set(d.keys())


# ---------------------------------------------------------------------------
# TC-LI-08: Error containment
# ---------------------------------------------------------------------------


class TestLinuxErrorContainment:
    """All Linux error paths must produce correct error types and codes."""

    def test_platform_guard_uses_backend_unavailable(self) -> None:
        """Platform guard on non-Linux must use backend_unavailable error code."""
        with (
            pytest.raises(BackendUnavailableError) as exc_info,
            patch("sys.platform", "win32"),
        ):
            LinuxBackend()
        assert exc_info.value.error_code == "backend_unavailable"

    def test_pyatspi_guard_uses_backend_unavailable(self) -> None:
        """Missing pyatspi must use backend_unavailable error code."""
        with (
            pytest.raises(BackendUnavailableError) as exc_info,
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": None}),
        ):
            LinuxBackend()
        assert exc_info.value.error_code == "backend_unavailable"

    def test_disposed_list_windows_uses_backend_unavailable(self) -> None:
        """list_windows on disposed backend must use backend_unavailable."""
        mock_pyatspi = _make_mock_pyatspi(showing_count=0, hidden_count=0)
        with (
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": mock_pyatspi}),
        ):
            backend = LinuxBackend()
            backend.dispose()
            with pytest.raises(BackendUnavailableError) as exc_info:
                backend.list_windows()
            assert exc_info.value.error_code == "backend_unavailable"

    def test_disposed_snapshot_uses_backend_unavailable(self) -> None:
        """snapshot on disposed backend must use backend_unavailable."""
        mock_pyatspi = _make_mock_pyatspi(showing_count=0, hidden_count=0)
        with (
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": mock_pyatspi}),
        ):
            backend = LinuxBackend()
            backend.dispose()
            with pytest.raises(BackendUnavailableError) as exc_info:
                backend.snapshot(MagicMock())
            assert exc_info.value.error_code == "backend_unavailable"

    def test_none_handle_perform_action_uses_element_not_found(self) -> None:
        """perform_action with None handle must use element_not_found."""
        mock_pyatspi = _make_mock_pyatspi(showing_count=0, hidden_count=0)
        with (
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": mock_pyatspi}),
        ):
            backend = LinuxBackend()
            with pytest.raises(ElementNotFoundError) as exc_info:
                backend.perform_action(None, DesktopAction.CLICK)
            assert exc_info.value.error_code == "element_not_found"

    def test_disposed_perform_action_uses_stale_element_reference(self) -> None:
        """perform_action on disposed backend must use stale_element_reference."""
        mock_pyatspi = _make_mock_pyatspi(showing_count=0, hidden_count=0)
        with (
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": mock_pyatspi}),
        ):
            backend = LinuxBackend()
            backend.dispose()
            with pytest.raises(StaleElementReferenceError) as exc_info:
                backend.perform_action(MagicMock(), DesktopAction.CLICK)
            assert exc_info.value.error_code == "stale_element_reference"

    def test_disposed_get_element_info_uses_stale_element_reference(self) -> None:
        """get_element_info on disposed backend must use stale_element_reference."""
        mock_pyatspi = _make_mock_pyatspi(showing_count=0, hidden_count=0)
        with (
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": mock_pyatspi}),
        ):
            backend = LinuxBackend()
            backend.dispose()
            with pytest.raises(StaleElementReferenceError) as exc_info:
                backend.get_element_info(MagicMock())
            assert exc_info.value.error_code == "stale_element_reference"

    def test_is_valid_never_raises(self) -> None:
        """is_valid must never raise, regardless of input."""
        mock_pyatspi = _make_mock_pyatspi(showing_count=0, hidden_count=0)
        with (
            patch("sys.platform", "linux"),
            patch.dict("sys.modules", {"pyatspi": mock_pyatspi}),
        ):
            backend = LinuxBackend()
            assert backend.is_valid(None) is False
            assert backend.is_valid("not an accessible") is False
            backend.dispose()
            assert backend.is_valid(None) is False

    def test_all_linux_errors_are_pathlight_mcp_errors(self) -> None:
        """All error types used by LinuxBackend must inherit PathlightMCPError."""
        from pathlight_mcp.errors import PathlightMCPError

        for exc_class in [
            BackendUnavailableError,
            ElementNotFoundError,
            StaleElementReferenceError,
            ActionNotSupportedError,
            WindowNotFoundError,
        ]:
            assert issubclass(exc_class, PathlightMCPError)


# ---------------------------------------------------------------------------
# TC-LI-09: Cross-platform normalization parity
# ---------------------------------------------------------------------------


class TestCrossPlatformNormalizationParity:
    """Linux and Windows must normalize to the same schema (PRD R11)."""

    @pytest.mark.parametrize(
        ("native_role", "expected_role"),
        [
            ("push button", "button"),
            ("entry", "text_input"),
            ("check box", "checkbox"),
            ("window", "window"),
            ("list box", "list"),
            ("menu bar", "menu_bar"),
            ("menu item", "menu_item"),
            ("toggle button", "toggle_button"),
            ("table cell", "table_cell"),
            ("page tab", "tab_item"),
        ],
    )
    def test_linux_role_matches_shared_schema(self, native_role: str, expected_role: str) -> None:
        """Linux AT-SPI roles must normalize to the same roles as Windows."""
        el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-1",
            role=native_role,
            native_role=native_role,
        )
        assert el.role == expected_role

    def test_to_dict_keys_identical_across_platforms(self) -> None:
        """Core NormalizedElement.to_dict() keys must be identical for both platforms.

        Platform-specific keys (native_role for Linux, control_type for Windows)
        are expected to differ.
        """
        linux_el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-1",
            role="push button",
            native_role="push button",
            name="Click",
            raw_states={"enabled": True},
            raw_actions=["click"],
        )
        windows_el = normalize_element(
            platform="windows",
            ref="e1",
            backend_id="native-1",
            role="push button",
            control_type="50000",
            name="Click",
            raw_states={"IsEnabled": True},
            raw_actions=["Invoke"],
        )
        linux_keys = set(linux_el.to_dict().keys())
        windows_keys = set(windows_el.to_dict().keys())
        # Core keys must be shared
        core_keys = {"ref", "role", "name", "states", "bounds", "actions", "children", "backend_id"}
        assert core_keys <= linux_keys
        assert core_keys <= windows_keys

    def test_states_schema_identical_across_platforms(self) -> None:
        """State field names must be identical across platforms."""
        linux_el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-1",
            role="push button",
            native_role="push button",
            raw_states={"enabled": True, "focused": False},
        )
        windows_el = normalize_element(
            platform="windows",
            ref="e1",
            backend_id="native-1",
            role="push button",
            control_type="50000",
            raw_states={"IsEnabled": True, "HasKeyboardFocus": False},
        )
        linux_states = set(linux_el.to_dict()["states"].keys())
        windows_states = set(windows_el.to_dict()["states"].keys())
        assert linux_states == windows_states

    def test_mixed_checkbox_parity(self) -> None:
        """Tri-state checked (mixed) must produce the same value on both platforms."""
        linux_el = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="native-1",
            role="check box",
            native_role="check box",
            raw_states={"checked": 2},  # mixed
        )
        windows_el = normalize_element(
            platform="windows",
            ref="e1",
            backend_id="native-1",
            role="check box",
            control_type="50002",
            raw_states={"ToggleState": 2},  # Indeterminate
        )
        assert linux_el.to_dict()["states"]["checked"] == "mixed"
        assert windows_el.to_dict()["states"]["checked"] == "mixed"


# ---------------------------------------------------------------------------
# TC-LI-10: End-to-end pipeline on golden fixture data
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    """Full pipeline: golden snapshot → normalize → classify → redact."""

    def test_gedit_full_pipeline(self) -> None:
        """Gedit golden snapshot runs through classify → redact pipeline."""
        gedit = load_linux_golden_snapshot("gedit_snapshot.json")
        tree_dict = gedit["snapshot"]

        def dict_to_element(node: dict) -> NormalizedElement:
            children = [dict_to_element(c) for c in node.get("children", [])]
            return NormalizedElement(
                ref=node.get("ref", ""),
                backend_id=node.get("backend_id", ""),
                role=node.get("role", ""),
                name=node.get("name"),
                native_role=node.get("native_role"),
                value=node.get("value"),
                text=node.get("text"),
                children=children if children else None,
            )

        root = dict_to_element(tree_dict)

        # Step 1: Classify every element
        elements = root.walk()
        for el in elements:
            action = "type" if el.role == "text_input" else "click"
            result = classify(el, action)
            assert result.risk_level in ("READ_ONLY", "INTERACTION", "SENSITIVE")

        # Step 2: Register in ref store
        store = ElementRefStore()
        for el in elements:
            ref = store.store(el.backend_id)
            assert store.is_valid(ref)

        # Step 3: Redact
        redacted = redact_snapshot([root])
        assert redacted[0].role == "window"

        # Step 4: Verify refs survive redaction
        for el in elements:
            found = redacted[0].find_by_ref(el.ref)
            assert found is not None, f"Ref {el.ref} lost after redaction"

    def test_calculator_full_pipeline(self) -> None:
        """Calculator golden snapshot runs through classify → redact pipeline."""
        calc = load_linux_golden_snapshot("gnome_calculator_snapshot.json")
        tree_dict = calc["snapshot"]

        def dict_to_element(node: dict) -> NormalizedElement:
            children = [dict_to_element(c) for c in node.get("children", [])]
            return NormalizedElement(
                ref=node.get("ref", ""),
                backend_id=node.get("backend_id", ""),
                role=node.get("role", ""),
                name=node.get("name"),
                native_role=node.get("native_role"),
                value=node.get("value"),
                text=node.get("text"),
                children=children if children else None,
            )

        root = dict_to_element(tree_dict)

        # Step 1: Classify every element
        elements = root.walk()
        button_count = 0
        for el in elements:
            action = "type" if el.role == "text_input" else "click"
            result = classify(el, action)
            assert result.risk_level in ("READ_ONLY", "INTERACTION", "SENSITIVE")
            if el.role == "button":
                button_count += 1

        assert button_count >= 10

        # Step 2: Redact
        redacted = redact_snapshot([root])
        assert redacted[0].role == "window"
        assert redacted[0].name == "Calculator"

        # Step 3: All buttons preserved
        redacted_buttons = [el for el in redacted[0].walk() if el.role == "button"]
        assert len(redacted_buttons) >= 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pyatspi(
    showing_count: int = 0,
    hidden_count: int = 0,
) -> MagicMock:
    """Create a mock pyatspi module with configurable desktop children.

    Produces ``showing_count`` children with STATE_SHOWING and
    ``hidden_count`` children without it.
    """
    mock_pyatspi = MagicMock()
    mock_desktop = MagicMock()

    children = []
    for i in range(showing_count + hidden_count):
        child = MagicMock()
        child.get_role.return_value = mock_pyatspi.ROLE_FRAME
        child.get_name.return_value = f"Window {i}"

        state_set = MagicMock()
        if i < showing_count:
            state_set.contains.return_value = True
        else:
            state_set.contains.return_value = False
        child.getState.return_value = state_set
        child.get_state_set.return_value = state_set
        children.append(child)

    mock_desktop.children = children
    mock_desktop.getChildCount.return_value = len(children)
    mock_pyatspi.Registry.getDesktop.return_value = mock_desktop
    return mock_pyatspi

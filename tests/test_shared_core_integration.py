"""P0 integration tests — shared core module cross-cutting scenarios.

TC-060: Refs + Models integration
TC-061: Safety + Models integration
TC-062: Privacy + Models integration
TC-063: Errors + Backend integration
TC-064: End-to-end snapshot → classify → redact pipeline
"""

import pytest

from guidewire.backends import MockBackend
from guidewire.errors import ElementNotFoundError, StaleElementReferenceError
from guidewire.models import Bounds, ElementStates, NormalizedElement
from guidewire.privacy import PrivacyConfig, redact_snapshot
from guidewire.refs import ElementRefStore
from guidewire.safety import classify

# ---------------------------------------------------------------------------
# TC-060: Refs + Models integration
# ---------------------------------------------------------------------------


class TestRefsModelsIntegration:
    """TC-060: ElementRefStore works with NormalizedElement instances."""

    def test_store_element_ref_and_resolve(self) -> None:
        """Store a NativeHandle via ref store, find by ref on element tree."""
        store = ElementRefStore()
        handle = "native-btn-1"
        ref = store.store(handle)
        assert ref == "e1"

        tree = NormalizedElement(
            ref=ref,
            backend_id=handle,
            role="button",
            name="Submit",
        )
        found = tree.find_by_ref(ref)
        assert found is not None
        assert found.backend_id == handle

    def test_store_multiple_elements_find_each(self) -> None:
        """Multiple elements registered in ref store can all be found."""
        store = ElementRefStore()
        children = []
        for i in range(5):
            handle = f"native-el-{i}"
            ref = store.store(handle)
            children.append(
                NormalizedElement(
                    ref=ref,
                    backend_id=handle,
                    role="text_input",
                    name=f"Field {i}",
                )
            )

        root = NormalizedElement(
            ref="w1",
            backend_id="native-win",
            role="window",
            children=children,
        )

        for i in range(5):
            ref = f"e{i + 1}"
            found = root.find_by_ref(ref)
            assert found is not None
            assert found.name == f"Field {i}"

    def test_clear_store_invalidates_all_refs(self) -> None:
        """After clear, no refs resolve to any handle."""
        store = ElementRefStore()
        refs = [store.store(f"handle-{i}") for i in range(10)]
        store.clear()
        for ref in refs:
            assert store.resolve(ref) is None

    def test_walk_element_tree_matches_store_size(self) -> None:
        """walk() on a tree returns same count as store.size."""
        store = ElementRefStore()
        children = []
        for i in range(3):
            ref = store.store(f"h-{i}")
            children.append(NormalizedElement(ref=ref, backend_id=f"h-{i}", role="label"))
        root = NormalizedElement(
            ref="w1",
            backend_id="hw",
            role="window",
            children=children,
        )
        walked = root.walk()
        # store has 3 elements + 1 window = 4 refs stored
        assert len(walked) == len(children) + 1


# ---------------------------------------------------------------------------
# TC-061: Safety + Models integration
# ---------------------------------------------------------------------------


class TestSafetyModelsIntegration:
    """TC-061: classify() works correctly with real NormalizedElement data."""

    def test_classify_element_with_all_fields(self) -> None:
        """Full NormalizedElement with all fields classified correctly."""
        el = NormalizedElement(
            ref="e1",
            backend_id="native-1",
            role="button",
            name="Delete Account",
            native_role="AXButton",
            control_type="ControlType.Button",
            description="Delete user account permanently",
            value=None,
            text="Delete Account",
            states=ElementStates(enabled=True, focused=False),
            bounds=Bounds(x=100.0, y=200.0, width=120.0, height=40.0),
            actions=["click", "invoke"],
        )
        result = classify(el, "click")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True

    def test_classify_nested_tree_elements(self) -> None:
        """Classify each element in a nested tree independently."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="win-0",
            role="window",
            name="Settings",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="n-1",
                    role="label",
                    name="Username",
                ),
                NormalizedElement(
                    ref="e2",
                    backend_id="n-2",
                    role="text_input",
                    name="Username",
                ),
                NormalizedElement(
                    ref="e3",
                    backend_id="n-3",
                    role="delete_button",
                    name="Remove User",
                ),
            ],
        )
        elements = tree.walk()
        # window
        assert classify(elements[0], "click").risk_level == "READ_ONLY"
        # label
        assert classify(elements[1], "click").risk_level == "READ_ONLY"
        # text_input
        assert classify(elements[2], "type").risk_level == "INTERACTION"
        # delete_button
        assert classify(elements[3], "click").risk_level == "SENSITIVE"

    def test_classify_disabled_element_in_tree(self) -> None:
        """Disabled child in tree is READ_ONLY regardless of role."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="win-0",
            role="window",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="n-1",
                    role="delete_button",
                    name="Delete",
                    states=ElementStates(enabled=False),
                ),
            ],
        )
        btn = tree.find_by_ref("e1")
        assert btn is not None
        result = classify(btn, "click")
        assert result.risk_level == "READ_ONLY"


# ---------------------------------------------------------------------------
# TC-062: Privacy + Models integration
# ---------------------------------------------------------------------------


class TestPrivacyModelsIntegration:
    """TC-062: redact_snapshot works correctly with real NormalizedElement trees."""

    def test_redact_preserves_tree_structure(self) -> None:
        """Redaction preserves element hierarchy and non-sensitive values."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="win-0",
            role="window",
            name="App",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="n-1",
                    role="text_input",
                    name="Username",
                    value="admin",
                    text="admin",
                ),
                NormalizedElement(
                    ref="e2",
                    backend_id="n-2",
                    role="text_input",
                    name="Password",
                    value="secret",
                    text="secret",
                ),
                NormalizedElement(
                    ref="e3",
                    backend_id="n-3",
                    role="button",
                    name="Login",
                ),
            ],
        )
        config = PrivacyConfig()
        result = redact_snapshot([tree], config=config)

        username = result[0].children[0]
        assert username.value == "admin"  # not redacted

        password = result[0].children[1]
        assert password.value == "[REDACTED]"  # redacted

        button = result[0].children[2]
        assert button.name == "Login"  # preserved

    def test_redact_deeply_nested_password(self) -> None:
        """Password field nested deep in tree is still redacted."""
        deep = NormalizedElement(
            ref="e3",
            backend_id="n-3",
            role="text_input",
            name="PIN",
            value="1234",
            text="1234",
        )
        mid = NormalizedElement(
            ref="e2",
            backend_id="n-2",
            role="group",
            children=[deep],
        )
        root = NormalizedElement(
            ref="w1",
            backend_id="win-0",
            role="window",
            children=[mid],
        )
        result = redact_snapshot([root])
        redacted = result[0].children[0].children[0]
        assert redacted.value == "[REDACTED]"

    def test_redact_with_is_password_state(self) -> None:
        """Element with is_password=True state gets redacted even without
        a password-like name."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="win-0",
            role="window",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="n-1",
                    role="text_input",
                    name="Token",
                    value="abc123",
                    text="abc123",
                    states=ElementStates(is_password=True),
                ),
            ],
        )
        result = redact_snapshot([tree])
        assert result[0].children[0].value == "[REDACTED]"


# ---------------------------------------------------------------------------
# TC-063: Errors + Backend integration
# ---------------------------------------------------------------------------


class TestErrorsBackendIntegration:
    """TC-063: Error types integrate properly with backend patterns."""

    def test_element_not_found_with_mock_backend(self) -> None:
        """ElementNotFoundError can be raised when backend find fails."""
        MockBackend()
        with pytest.raises(ElementNotFoundError):
            raise ElementNotFoundError("Element e99 not found in backend")

    def test_stale_element_ref_with_store(self) -> None:
        """StaleElementReferenceError can reference a store ref."""
        store = ElementRefStore()
        ref = store.store("native-1")
        store.clear()
        # After clear, the ref is stale
        assert store.resolve(ref) is None
        with pytest.raises(StaleElementReferenceError):
            raise StaleElementReferenceError(
                f"Reference {ref} is no longer valid",
            )

    def test_mock_backend_add_window_returns_self(self) -> None:
        """MockBackend fluent builder returns self for chaining."""
        backend = MockBackend()
        result = backend.add_window("win-1", "Test App")
        assert result is backend

    def test_mock_backend_add_element_returns_self(self) -> None:
        """MockBackend fluent builder returns self for chaining."""
        backend = MockBackend()
        result = backend.add_element("win-1", "e1", "button", "Click")
        assert result is backend


# ---------------------------------------------------------------------------
# TC-064: End-to-end snapshot → classify → redact pipeline
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    """TC-064: Full pipeline from snapshot through classify to redaction."""

    def test_snapshot_classify_redact_pipeline(self) -> None:
        """Build a snapshot, classify each element, then redact the whole
        tree. Password fields should be redacted; risk metadata should
        be correct for each element."""
        snapshot = NormalizedElement(
            ref="w1",
            backend_id="win-0",
            role="window",
            name="Login Form",
            bounds=Bounds(x=0.0, y=0.0, width=800.0, height=600.0),
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="n-1",
                    role="text_input",
                    name="Username",
                    value="admin",
                    text="admin",
                    actions=["type"],
                ),
                NormalizedElement(
                    ref="e2",
                    backend_id="n-2",
                    role="text_input",
                    name="Password",
                    value="s3cret",
                    text="s3cret",
                    actions=["type"],
                ),
                NormalizedElement(
                    ref="e3",
                    backend_id="n-3",
                    role="button",
                    name="Delete Account",
                    actions=["click"],
                ),
                NormalizedElement(
                    ref="e4",
                    backend_id="n-4",
                    role="button",
                    name="Login",
                    actions=["click"],
                ),
            ],
        )

        # Step 1: Classify each element
        elements = snapshot.walk()
        classifications = {}
        for el in elements:
            action = "type" if el.role == "text_input" else "click"
            classifications[el.ref] = classify(el, action)

        # window is READ_ONLY
        assert classifications["w1"].risk_level == "READ_ONLY"
        # username is INTERACTION
        assert classifications["e1"].risk_level == "INTERACTION"
        # password field is INTERACTION (not SENSITIVE by role)
        assert classifications["e2"].risk_level == "INTERACTION"
        # delete button is SENSITIVE
        assert classifications["e3"].risk_level == "SENSITIVE"
        assert classifications["e3"].confirmation_required is True
        # login button is INTERACTION
        assert classifications["e4"].risk_level == "INTERACTION"

        # Step 2: Redact the snapshot
        config = PrivacyConfig()
        redacted = redact_snapshot([snapshot], config=config)

        # Username should not be redacted
        username = redacted[0].children[0]
        assert username.value == "admin"

        # Password should be redacted
        password = redacted[0].children[1]
        assert password.value == "[REDACTED]"

        # Buttons should not be redacted
        assert redacted[0].children[2].name == "Delete Account"
        assert redacted[0].children[3].name == "Login"

    def test_ref_store_with_pipeline(self) -> None:
        """Register all snapshot elements in ref store, then classify and
        redact."""
        store = ElementRefStore()
        snapshot = NormalizedElement(
            ref="w1",
            backend_id="win-0",
            role="window",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="n-1",
                    role="text_input",
                    name="Password",
                    value="secret",
                    text="secret",
                ),
            ],
        )

        # Register all handles in ref store
        for el in snapshot.walk():
            ref = store.store(el.backend_id)
            assert store.is_valid(ref)

        # Classify and redact
        pw = snapshot.find_by_ref("e1")
        assert pw is not None
        assert classify(pw, "type").risk_level == "INTERACTION"

        redacted = redact_snapshot([snapshot])
        assert redacted[0].children[0].value == "[REDACTED]"

    def test_redact_preserves_element_refs(self) -> None:
        """After redaction, element refs remain findable in tree."""
        tree = NormalizedElement(
            ref="w1",
            backend_id="win-0",
            role="window",
            children=[
                NormalizedElement(
                    ref="e1",
                    backend_id="n-1",
                    role="text_input",
                    name="Password",
                    value="x",
                    text="x",
                ),
                NormalizedElement(
                    ref="e2",
                    backend_id="n-2",
                    role="button",
                    name="Submit",
                ),
            ],
        )
        result = redact_snapshot([tree])
        # Refs should still be findable
        assert result[0].find_by_ref("e1") is not None
        assert result[0].find_by_ref("e2") is not None
        assert result[0].find_by_ref("w1") is not None

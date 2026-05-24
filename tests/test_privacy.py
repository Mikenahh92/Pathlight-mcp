"""Tests for privacy controls (GW-007, GW-044).

Verifies that the privacy module:
- Detects password and sensitive fields via name and state heuristics (role=text_input only)
- Redacts value/text/name/description on NormalizedElement instances
- Redacts entire snapshot tree lists recursively
- Replaces denylisted application windows with stub elements
- PrivacyConfig is a frozen dataclass with spec-matching fields
- __all__ exports exactly 5 names
- Deep copy returned when redact_passwords=False
- Redacts sensitive content in clipboard text via line-by-line keyword scanning
"""

import pytest

from guidewire.models import Bounds, ElementStates, NormalizedElement
from guidewire.privacy import (
    PrivacyConfig,
    is_password_field,
    redact_clipboard_text,
    redact_element,
    redact_snapshot,
    redact_web_content,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _el(
    role: str = "text_input",
    name: str | None = None,
    value: str | None = None,
    text: str | None = None,
    description: str | None = None,
    states: ElementStates | None = None,
    children: list[NormalizedElement] | None = None,
    ref: str = "e1",
) -> NormalizedElement:
    """Create a NormalizedElement for testing."""
    return NormalizedElement(
        ref=ref,
        backend_id="native-0",
        role=role,
        name=name,
        value=value,
        text=text,
        description=description,
        states=states or ElementStates(),
        children=children,
    )


def _window(
    name: str = "App Window",
    children: list[NormalizedElement] | None = None,
    ref: str = "w1",
) -> NormalizedElement:
    """Create a window element for testing."""
    return NormalizedElement(
        ref=ref,
        backend_id="win-0",
        role="window",
        name=name,
        bounds=Bounds(x=0.0, y=0.0, width=800.0, height=600.0),
        children=children,
    )


def _root_with_windows(
    windows: list[NormalizedElement],
) -> NormalizedElement:
    """Create a desktop root containing multiple windows."""
    return NormalizedElement(
        ref="desktop",
        backend_id="desktop",
        role="desktop",
        children=windows,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> PrivacyConfig:
    """Return a default PrivacyConfig."""
    return PrivacyConfig()


@pytest.fixture
def silent_config() -> PrivacyConfig:
    """Return a PrivacyConfig with redaction disabled."""
    return PrivacyConfig(redact_passwords=False)


@pytest.fixture
def denylist_config() -> PrivacyConfig:
    """Return a PrivacyConfig with keepass.exe on the denylist."""
    return PrivacyConfig(denylist_apps=frozenset({"keepass.exe"}))


@pytest.fixture
def sample_snapshot() -> NormalizedElement:
    """Return a sample snapshot tree with mixed elements."""
    return _window(
        name="Login Form",
        children=[
            _el(role="text_input", name="Username", value="admin", text="admin", ref="e1"),
            _el(
                role="text_input",
                name="Password",
                value="super_secret_123",
                text="super_secret_123",
                ref="e2",
            ),
            _el(role="button", name="Login", ref="e3"),
        ],
    )


# ---------------------------------------------------------------------------
# __all__ exports
# ---------------------------------------------------------------------------


class TestExports:
    """Verify module exports exactly 6 public names (F6)."""

    def test_all_exports(self) -> None:
        import guidewire.privacy as mod

        assert sorted(mod.__all__) == [
            "PrivacyConfig",
            "is_password_field",
            "redact_clipboard_text",
            "redact_element",
            "redact_snapshot",
            "redact_web_content",
        ]

    def test_privacy_config_importable(self) -> None:
        from guidewire.privacy import PrivacyConfig

        assert PrivacyConfig is PrivacyConfig

    def test_functions_importable(self) -> None:
        from guidewire.privacy import (
            is_password_field,
            redact_clipboard_text,
            redact_element,
            redact_snapshot,
            redact_web_content,
        )

        assert callable(is_password_field)
        assert callable(redact_clipboard_text)
        assert callable(redact_element)
        assert callable(redact_snapshot)
        assert callable(redact_web_content)


# ---------------------------------------------------------------------------
# PrivacyConfig
# ---------------------------------------------------------------------------


class TestPrivacyConfig:
    """Verify PrivacyConfig frozen dataclass with spec-matching fields (F4)."""

    def test_default_config(self) -> None:
        c = PrivacyConfig()
        assert len(c.denylist_apps) == 0
        assert c.redaction_placeholder == "[REDACTED]"
        assert c.redact_passwords is True

    def test_frozen(self) -> None:
        c = PrivacyConfig()
        with pytest.raises(AttributeError):
            c.denylist_apps = frozenset()  # type: ignore[misc]

    def test_custom_denylist_apps(self) -> None:
        c = PrivacyConfig(denylist_apps=frozenset({"evil.exe"}))
        assert "evil.exe" in c.denylist_apps

    def test_custom_redaction_placeholder(self) -> None:
        c = PrivacyConfig(redaction_placeholder="***")
        assert c.redaction_placeholder == "***"

    def test_redact_passwords_toggle(self) -> None:
        c = PrivacyConfig(redact_passwords=False)
        assert c.redact_passwords is False

    def test_no_extra_fields(self) -> None:
        """PrivacyConfig must not have password_roles or password_name_patterns (F4)."""
        c = PrivacyConfig()
        assert not hasattr(c, "password_roles")
        assert not hasattr(c, "password_name_patterns")
        assert not hasattr(c, "redacted_value")
        assert not hasattr(c, "denylist")


# ---------------------------------------------------------------------------
# is_password_field
# ---------------------------------------------------------------------------


class TestIsPasswordField:
    """Verify password/sensitive field detection restricted to role=text_input (F7, F8)."""

    # -- Role restriction (F7: only text_input) --

    def test_non_text_input_never_sensitive(self) -> None:
        """Elements with role != text_input are never password fields (§3.3)."""
        assert is_password_field(_el(role="button")) is False
        assert is_password_field(_el(role="window")) is False
        assert is_password_field(_el(role="password")) is False
        assert is_password_field(_el(role="edit")) is False

    def test_text_input_default_not_sensitive(self) -> None:
        assert is_password_field(_el(role="text_input")) is False

    # -- Name-based detection (text_input only) --

    def test_name_contains_password(self) -> None:
        el = _el(role="text_input", name="Enter Password")
        assert is_password_field(el) is True

    def test_name_contains_passwd(self) -> None:
        el = _el(role="text_input", name="Enter passwd")
        assert is_password_field(el) is True

    def test_name_contains_pwd(self) -> None:
        el = _el(role="text_input", name="Type your pwd here")
        assert is_password_field(el) is True

    def test_name_contains_secret(self) -> None:
        el = _el(role="text_input", name="API Secret")
        assert is_password_field(el) is True

    def test_name_contains_credential(self) -> None:
        el = _el(role="text_input", name="Enter credential")
        assert is_password_field(el) is True

    def test_name_contains_pin(self) -> None:
        el = _el(role="text_input", name="PIN Code")
        assert is_password_field(el) is True

    def test_name_case_insensitive(self) -> None:
        assert is_password_field(_el(role="text_input", name="PASSWORD FIELD")) is True
        assert is_password_field(_el(role="text_input", name="My Secret Key")) is True

    def test_non_sensitive_name(self) -> None:
        assert is_password_field(_el(role="text_input", name="Username")) is False
        assert is_password_field(_el(role="text_input", name="Email")) is False
        assert is_password_field(_el(role="text_input", name="Search")) is False

    def test_name_not_sensitive_on_non_text_input(self) -> None:
        """Name patterns only apply to text_input role (F7)."""
        assert is_password_field(_el(role="edit", name="Enter Password")) is False

    # -- None/empty name handling --

    def test_none_name(self) -> None:
        el = _el(role="text_input", name=None)
        assert is_password_field(el) is False

    # -- State-based detection --

    def test_is_password_state_true(self) -> None:
        el = _el(role="text_input", states=ElementStates(is_password=True))
        assert is_password_field(el) is True

    def test_is_password_state_false(self) -> None:
        el = _el(role="text_input", states=ElementStates(is_password=False))
        assert is_password_field(el) is False

    def test_is_password_state_none(self) -> None:
        el = _el(role="text_input", states=ElementStates(is_password=None))
        assert is_password_field(el) is False

    def test_state_not_sensitive_on_non_text_input(self) -> None:
        """State-based detection only applies to text_input role (F7)."""
        el = _el(role="password", states=ElementStates(is_password=True))
        assert is_password_field(el) is False

    # -- No config parameter (F8) --

    def test_no_config_param(self) -> None:
        """is_password_field takes only element, no config (F8)."""
        import inspect

        sig = inspect.signature(is_password_field)
        assert list(sig.parameters.keys()) == ["element"]


# ---------------------------------------------------------------------------
# redact_element
# ---------------------------------------------------------------------------


class TestRedactElement:
    """Verify single-element redaction with per-field keyword params (F2)."""

    def test_redacts_password_field_default(self) -> None:
        el = _el(role="text_input", name="Password", value="secret", text="secret")
        result = redact_element(el)
        assert result.value == "[REDACTED]"
        assert result.text == "[REDACTED]"

    def test_preserves_non_sensitive(self) -> None:
        el = _el(role="text_input", value="admin", text="admin")
        result = redact_element(el)
        assert result is el  # same object, no copy
        assert result.value == "admin"

    def test_redacts_name_based(self) -> None:
        el = _el(role="text_input", name="Enter your password", value="hunter2", text="hunter2")
        result = redact_element(el)
        assert result.value == "[REDACTED]"
        assert result.text == "[REDACTED]"

    def test_none_values_not_replaced(self) -> None:
        el = _el(role="text_input", name="Password", value=None, text=None)
        result = redact_element(el)
        assert result.value is None
        assert result.text is None

    def test_does_not_mutate_original(self) -> None:
        el = _el(role="text_input", name="Password", value="abc", text="abc")
        redact_element(el)
        assert el.value == "abc"
        assert el.text == "abc"

    # -- Per-field keyword params (F2) --

    def test_redact_value_false(self) -> None:
        el = _el(role="text_input", name="Password", value="secret", text="secret")
        result = redact_element(el, redact_value=False)
        assert result.value == "secret"
        assert result.text == "[REDACTED]"

    def test_redact_text_false(self) -> None:
        el = _el(role="text_input", name="Password", value="secret", text="secret")
        result = redact_element(el, redact_text=False)
        assert result.value == "[REDACTED]"
        assert result.text == "secret"

    def test_redact_name_true(self) -> None:
        el = _el(role="text_input", name="Password", value="secret", text="secret")
        result = redact_element(el, redact_name=True)
        assert result.name == "[REDACTED]"
        assert result.value == "[REDACTED]"

    def test_redact_description_true(self) -> None:
        el = _el(
            role="text_input",
            name="Password",
            value="secret",
            description="Enter your password",
        )
        result = redact_element(el, redact_description=True)
        assert result.description == "[REDACTED]"

    def test_redact_description_false_default(self) -> None:
        el = _el(
            role="text_input",
            name="Password",
            value="secret",
            description="Enter your password",
        )
        result = redact_element(el)
        assert result.description == "Enter your password"

    def test_redaction_placeholder_override(self) -> None:
        el = _el(role="text_input", name="Password", value="x", text="x")
        result = redact_element(el, redaction_placeholder="***HIDDEN***")
        assert result.value == "***HIDDEN***"
        assert result.text == "***HIDDEN***"

    def test_redaction_placeholder_none_uses_default(self) -> None:
        el = _el(role="text_input", name="Password", value="x", text="x")
        result = redact_element(el, redaction_placeholder=None)
        assert result.value == "[REDACTED]"

    def test_is_password_state_redaction(self) -> None:
        el = _el(
            role="text_input",
            value="cred",
            text="cred",
            states=ElementStates(is_password=True),
        )
        result = redact_element(el)
        assert result.value == "[REDACTED]"

    def test_non_text_input_role_not_redacted(self) -> None:
        """Elements with role != text_input are never redacted."""
        el = _el(role="password", value="secret", text="secret")
        result = redact_element(el)
        assert result is el
        assert result.value == "secret"


# ---------------------------------------------------------------------------
# redact_snapshot
# ---------------------------------------------------------------------------


class TestRedactSnapshot:
    """Verify snapshot tree list redaction (F1)."""

    def test_redacts_password_in_tree(
        self,
        config: PrivacyConfig,
        sample_snapshot: NormalizedElement,
    ) -> None:
        result = redact_snapshot([sample_snapshot], config=config)
        pw = next(c for c in result[0].children if c.name == "Password")
        assert pw.value == "[REDACTED]"
        assert pw.text == "[REDACTED]"

    def test_preserves_non_sensitive_in_tree(
        self,
        config: PrivacyConfig,
        sample_snapshot: NormalizedElement,
    ) -> None:
        result = redact_snapshot([sample_snapshot], config=config)
        username = next(c for c in result[0].children if c.name == "Username")
        assert username.value == "admin"
        assert username.text == "admin"

    def test_does_not_mutate_original(
        self,
        config: PrivacyConfig,
        sample_snapshot: NormalizedElement,
    ) -> None:
        original_value = sample_snapshot.children[1].value
        redact_snapshot([sample_snapshot], config=config)
        assert sample_snapshot.children[1].value == original_value

    def test_deeply_nested_redaction(self, config: PrivacyConfig) -> None:
        tree = _window(
            name="Deep",
            children=[
                _el(
                    role="group",
                    name="Container",
                    children=[
                        _el(
                            role="group",
                            name="Inner",
                            children=[
                                _el(
                                    role="text_input",
                                    name="PIN",
                                    value="1234",
                                    text="1234",
                                    ref="e3",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
        result = redact_snapshot([tree], config=config)
        deep = result[0].children[0].children[0].children[0]
        assert deep.value == "[REDACTED]"
        assert deep.text == "[REDACTED]"

    def test_multiple_sensitive_fields(self, config: PrivacyConfig) -> None:
        tree = _window(
            name="Payment",
            children=[
                _el(
                    role="text_input",
                    name="Enter credential",
                    value="4111111111111111",
                    text="4111111111111111",
                    ref="e1",
                ),
                _el(
                    role="text_input",
                    name="CVV password",
                    value="123",
                    text="123",
                    ref="e2",
                ),
                _el(
                    role="text_input",
                    name="Cardholder Name",
                    value="John Doe",
                    text="John Doe",
                    ref="e3",
                ),
            ],
        )
        result = redact_snapshot([tree], config=config)
        assert result[0].children[0].value == "[REDACTED]"  # credential (name match)
        assert result[0].children[1].value == "[REDACTED]"  # password in name
        assert result[0].children[2].value == "John Doe"  # safe

    def test_empty_tree(self, config: PrivacyConfig) -> None:
        tree = _window(name="Empty")
        result = redact_snapshot([tree], config=config)
        assert len(result) == 1
        assert result[0] is tree  # no changes needed, same object

    def test_multiple_roots(self, config: PrivacyConfig) -> None:
        """redact_snapshot accepts list[NormalizedElement] (F1)."""
        tree1 = _window(
            name="Window1",
            children=[
                _el(role="text_input", name="Password", value="secret", ref="e1"),
            ],
        )
        tree2 = _window(
            name="Window2",
            children=[
                _el(role="text_input", name="PIN", value="1234", ref="e2"),
            ],
        )
        result = redact_snapshot([tree1, tree2], config=config)
        assert len(result) == 2
        assert result[0].children[0].value == "[REDACTED]"
        assert result[1].children[0].value == "[REDACTED]"

    # -- Deep copy when redact_passwords=False (F3) --

    def test_silent_config_returns_deep_copy(
        self,
        silent_config: PrivacyConfig,
        sample_snapshot: NormalizedElement,
    ) -> None:
        input_list = [sample_snapshot]
        result = redact_snapshot(input_list, config=silent_config)
        assert result is not input_list
        assert len(result) == 1
        # Deep copy: values preserved but different object
        assert result[0] is not sample_snapshot
        pw = next(c for c in result[0].children if c.name == "Password")
        assert pw.value == "super_secret_123"

    def test_silent_config_deep_copy_is_independent(self, silent_config: PrivacyConfig) -> None:
        tree = _window(
            name="Test",
            children=[
                _el(role="text_input", name="Password", value="secret", ref="e1"),
            ],
        )
        result = redact_snapshot([tree], config=silent_config)
        # Modifying the result should not affect the original
        assert result[0].children[0].value == "secret"
        assert tree.children[0].value == "secret"
        # They are truly different objects (deep copy)
        assert result[0] is not tree
        assert result[0].children[0] is not tree.children[0]

    # -- Denylist at snapshot level --

    def test_denylisted_window_replaced_with_stub(
        self,
        denylist_config: PrivacyConfig,
    ) -> None:
        windows = [
            _window(name="Notepad", ref="w1"),
            _window(name="keepass.exe", ref="w2"),
            _window(name="Calculator", ref="w3"),
        ]
        root = _root_with_windows(windows)
        result = redact_snapshot([root], config=denylist_config)
        assert len(result) == 1
        assert len(result[0].children) == 3
        # keepass.exe window should be replaced with stub (F5)
        stub = result[0].children[1]
        assert stub.role == "pane"
        assert stub.name == "[APP DENYLISTED]"
        assert stub.ref == "w2"

    def test_denylist_stub_role_and_name(self, denylist_config: PrivacyConfig) -> None:
        """Stub element has role='pane' and name='[APP DENYLISTED]' (F5)."""
        windows = [_window(name="keepass.exe", ref="w1")]
        root = _root_with_windows(windows)
        result = redact_snapshot([root], config=denylist_config)
        stub = result[0].children[0]
        assert stub.role == "pane"
        assert stub.name == "[APP DENYLISTED]"

    def test_denylist_preserves_non_sensitive_children(
        self,
        denylist_config: PrivacyConfig,
    ) -> None:
        windows = [
            _window(
                name="keepass.exe",
                children=[
                    _el(
                        role="text_input",
                        name="Password",
                        value="secret",
                        text="secret",
                        ref="e1",
                    ),
                ],
                ref="w1",
            ),
        ]
        root = _root_with_windows(windows)
        result = redact_snapshot([root], config=denylist_config)
        stub = result[0].children[0]
        assert stub.name == "[APP DENYLISTED]"
        assert stub.children is None  # stub has no children

    def test_non_window_children_not_denylist_checked(
        self,
        denylist_config: PrivacyConfig,
    ) -> None:
        """Denylist only applies to window-role children, not nested elements."""
        tree = _window(
            name="Login",
            children=[
                _el(role="text_input", name="keepass.exe", value="some", text="some", ref="e1"),
            ],
        )
        result = redact_snapshot([tree], config=denylist_config)
        # text_input named "keepass.exe" should NOT be treated as denylisted
        assert result[0].children[0].name == "keepass.exe"
        assert result[0].children[0].value == "some"

    def test_none_config_tree(self, sample_snapshot: NormalizedElement) -> None:
        result = redact_snapshot([sample_snapshot], config=None)
        pw = next(c for c in result[0].children if c.name == "Password")
        assert pw.value == "[REDACTED]"

    def test_empty_children_tree(self, config: PrivacyConfig) -> None:
        """Tree with children=[] should work fine."""
        el = NormalizedElement(
            ref="w1",
            backend_id="x",
            role="window",
            name="Empty",
            children=[],
        )
        result = redact_snapshot([el], config=config)
        assert result[0].children == []

    # -- app_name parameter (F1) --

    def test_app_name_denylist(self) -> None:
        """app_name param triggers denylist check (F1)."""
        config = PrivacyConfig(denylist_apps=frozenset({"keepass.exe"}))
        windows = [
            _window(name="keepass.exe", ref="w1"),
            _window(name="Notepad", ref="w2"),
        ]
        root = _root_with_windows(windows)
        result = redact_snapshot([root], app_name="keepass.exe", config=config)
        stub = result[0].children[0]
        assert stub.role == "pane"
        assert stub.name == "[APP DENYLISTED]"

    def test_app_name_not_denylisted(self) -> None:
        config = PrivacyConfig(denylist_apps=frozenset({"keepass.exe"}))
        windows = [_window(name="Notepad", ref="w1")]
        root = _root_with_windows(windows)
        result = redact_snapshot([root], app_name="notepad.exe", config=config)
        assert result[0].children[0].name == "Notepad"

    def test_app_name_case_insensitive(self) -> None:
        config = PrivacyConfig(denylist_apps=frozenset({"keepass.exe"}))
        windows = [_window(name="keepass.exe", ref="w1")]
        root = _root_with_windows(windows)
        result = redact_snapshot([root], app_name="KeepAss.exe", config=config)
        assert result[0].children[0].name == "[APP DENYLISTED]"


# ---------------------------------------------------------------------------
# TC-040: PrivacyConfig field types
# ---------------------------------------------------------------------------


class TestPrivacyConfigFieldTypes:
    """TC-040: PrivacyConfig fields have correct types."""

    def test_denylist_apps_is_frozenset(self) -> None:
        c = PrivacyConfig()
        assert isinstance(c.denylist_apps, frozenset)

    def test_denylist_apps_custom_type(self) -> None:
        c = PrivacyConfig(denylist_apps=frozenset({"a.exe"}))
        assert isinstance(c.denylist_apps, frozenset)

    def test_redaction_placeholder_is_str(self) -> None:
        c = PrivacyConfig()
        assert isinstance(c.redaction_placeholder, str)

    def test_redact_passwords_is_bool(self) -> None:
        c = PrivacyConfig()
        assert isinstance(c.redact_passwords, bool)


# ---------------------------------------------------------------------------
# TC-041: is_password_field boundary cases
# ---------------------------------------------------------------------------


class TestIsPasswordFieldBoundary:
    """TC-041: is_password_field handles edge cases correctly."""

    def test_name_with_whitespace(self) -> None:
        """Whitespace-only name should not match any pattern."""
        el = _el(role="text_input", name="   ")
        assert is_password_field(el) is False

    def test_name_with_special_chars(self) -> None:
        """Special chars around password keyword still match."""
        el = _el(role="text_input", name="***password***")
        assert is_password_field(el) is True

    def test_name_substring_pin(self) -> None:
        """'PIN' in 'SPIN' should match (substring match)."""
        el = _el(role="text_input", name="SPIN Code")
        assert is_password_field(el) is True

    def test_name_exact_credential(self) -> None:
        el = _el(role="text_input", name="credential")
        assert is_password_field(el) is True

    def test_empty_string_name(self) -> None:
        """Empty string name should not match."""
        el = _el(role="text_input", name="")
        assert is_password_field(el) is False

    def test_name_passwd_substring(self) -> None:
        """'passwd' substring match."""
        el = _el(role="text_input", name="enter_passwd_here")
        assert is_password_field(el) is True

    def test_name_pwd_substring(self) -> None:
        """'pwd' substring match."""
        el = _el(role="text_input", name="your_pwd")
        assert is_password_field(el) is True

    def test_role_text_input_with_value_no_name(self) -> None:
        """text_input with value but no name is not a password field."""
        el = _el(role="text_input", name=None, value="secret")
        assert is_password_field(el) is False


# ---------------------------------------------------------------------------
# TC-042: redact_element with all fields
# ---------------------------------------------------------------------------


class TestRedactElementAllFields:
    """TC-042: redact_element can redact all four text fields at once."""

    def test_redact_all_fields(self) -> None:
        el = _el(
            role="text_input",
            name="Password",
            value="v",
            text="t",
            description="desc",
        )
        result = redact_element(
            el,
            redact_value=True,
            redact_text=True,
            redact_name=True,
            redact_description=True,
        )
        assert result.value == "[REDACTED]"
        assert result.text == "[REDACTED]"
        assert result.name == "[REDACTED]"
        assert result.description == "[REDACTED]"

    def test_redact_no_fields(self) -> None:
        """Redacting zero fields on password element returns copy with
        no changes to value/text/name/description."""
        el = _el(
            role="text_input",
            name="Password",
            value="v",
            text="t",
        )
        result = redact_element(
            el,
            redact_value=False,
            redact_text=False,
            redact_name=False,
            redact_description=False,
        )
        assert result.value == "v"
        assert result.text == "t"
        assert result.name == "Password"

    def test_redact_preserves_other_fields(self) -> None:
        """redact_element preserves ref, backend_id, role, states, bounds."""
        el = _el(
            role="text_input",
            name="Password",
            value="v",
            text="t",
            states=ElementStates(enabled=True),
        )
        result = redact_element(el)
        assert result.ref == el.ref
        assert result.backend_id == el.backend_id
        assert result.role == el.role
        assert result.states == el.states


# ---------------------------------------------------------------------------
# TC-043: redact_snapshot with multiple denylisted apps
# ---------------------------------------------------------------------------


class TestMultipleDenylistedApps:
    """TC-043: Multiple apps on denylist are all stubbed."""

    def test_two_denylisted_apps(self) -> None:
        config = PrivacyConfig(
            denylist_apps=frozenset({"keepass.exe", "bitwarden.exe"}),
        )
        windows = [
            _window(name="keepass.exe", ref="w1"),
            _window(name="Notepad", ref="w2"),
            _window(name="bitwarden.exe", ref="w3"),
        ]
        root = _root_with_windows(windows)
        result = redact_snapshot([root], config=config)
        assert result[0].children[0].name == "[APP DENYLISTED]"
        assert result[0].children[1].name == "Notepad"
        assert result[0].children[2].name == "[APP DENYLISTED]"


# ---------------------------------------------------------------------------
# TC-044: redact_snapshot idempotency
# ---------------------------------------------------------------------------


class TestRedactSnapshotIdempotency:
    """TC-044: Redacting an already-redacted snapshot is safe."""

    def test_double_redact_same_result(self, config: PrivacyConfig) -> None:
        tree = _window(
            name="Form",
            children=[
                _el(
                    role="text_input",
                    name="Password",
                    value="secret",
                    text="secret",
                    ref="e1",
                ),
            ],
        )
        result1 = redact_snapshot([tree], config=config)
        result2 = redact_snapshot(result1, config=config)
        pw = result2[0].children[0]
        assert pw.value == "[REDACTED]"
        assert pw.text == "[REDACTED]"


# ---------------------------------------------------------------------------
# TC-045: redact_snapshot with empty elements list
# ---------------------------------------------------------------------------


class TestRedactSnapshotEmpty:
    """TC-045: Redacting an empty list returns an empty list."""

    def test_empty_list(self, config: PrivacyConfig) -> None:
        result = redact_snapshot([], config=config)
        assert result == []

    def test_none_config_empty_list(self) -> None:
        result = redact_snapshot([], config=None)
        assert result == []


# ---------------------------------------------------------------------------
# TC-046: PrivacyConfig custom placeholder
# ---------------------------------------------------------------------------


class TestCustomPlaceholder:
    """TC-046: Custom placeholder flows through to redact_element and
    redact_snapshot."""

    def test_custom_placeholder_in_element(self) -> None:
        el = _el(role="text_input", name="Password", value="s", text="s")
        result = redact_element(el, redaction_placeholder="***")
        assert result.value == "***"
        assert result.text == "***"

    def test_custom_placeholder_in_snapshot(self) -> None:
        config = PrivacyConfig(redaction_placeholder="[HIDDEN]")
        tree = _window(
            name="Form",
            children=[
                _el(
                    role="text_input",
                    name="Password",
                    value="secret",
                    text="secret",
                    ref="e1",
                ),
            ],
        )
        result = redact_snapshot([tree], config=config)
        pw = result[0].children[0]
        assert pw.value == "[HIDDEN]"
        assert pw.text == "[HIDDEN]"


# ---------------------------------------------------------------------------
# redact_clipboard_text
# ---------------------------------------------------------------------------


class TestRedactClipboardText:
    """Verify clipboard text redaction (GW-044).

    Line-by-line keyword scanning: any line containing a sensitive keyword
    is fully replaced with the redaction placeholder.
    """

    # -- Single-line detection: entire line replaced --

    def test_password_equals(self) -> None:
        result = redact_clipboard_text("password=secret123")
        assert result == "[REDACTED]"

    def test_passwd_equals(self) -> None:
        result = redact_clipboard_text("passwd=abc")
        assert result == "[REDACTED]"

    def test_pwd_equals(self) -> None:
        result = redact_clipboard_text("pwd=xyz")
        assert result == "[REDACTED]"

    def test_secret_equals(self) -> None:
        result = redact_clipboard_text("secret=mykey")
        assert result == "[REDACTED]"

    def test_credential_equals(self) -> None:
        result = redact_clipboard_text("credential=tkn")
        assert result == "[REDACTED]"

    def test_pin_equals(self) -> None:
        result = redact_clipboard_text("pin=1234")
        assert result == "[REDACTED]"

    # -- Keyword without separator: still redacted (line-by-line scanning) --

    def test_password_colon(self) -> None:
        result = redact_clipboard_text("password: secret123")
        assert result == "[REDACTED]"

    def test_secret_colon(self) -> None:
        result = redact_clipboard_text("api_secret: abcdef123")
        assert result == "[REDACTED]"

    # -- Case insensitivity --

    def test_case_insensitive_password(self) -> None:
        assert redact_clipboard_text("PASSWORD=hunter2") == "[REDACTED]"
        assert redact_clipboard_text("Password=hunter2") == "[REDACTED]"

    def test_case_insensitive_secret(self) -> None:
        assert redact_clipboard_text("SECRET=abc") == "[REDACTED]"

    # -- Keyword without separator triggers full-line redaction --

    def test_keyword_without_separator(self) -> None:
        """Line-by-line: any line with keyword is fully replaced (AC1/AC6)."""
        text = "password is required"
        result = redact_clipboard_text(text)
        assert result == "[REDACTED]"

    # -- Multiple matches across lines --

    def test_multiple_sensitive_pairs(self) -> None:
        text = "user=admin\npassword=s3cret\npin=1234"
        result = redact_clipboard_text(text)
        assert result == "user=admin\n[REDACTED]\n[REDACTED]"
        assert "s3cret" not in result
        assert "1234" not in result

    # -- Single line with keyword and other content --

    def test_sensitive_line_with_safe_prefix(self) -> None:
        text = "user=admin password=s3cret pin=1234"
        result = redact_clipboard_text(text)
        assert result == "[REDACTED]"
        assert "s3cret" not in result

    # -- Non-sensitive text preserved --

    def test_non_sensitive_text_unchanged(self) -> None:
        text = "username=admin email=user@example.com"
        result = redact_clipboard_text(text)
        assert result == text

    def test_empty_string(self) -> None:
        assert redact_clipboard_text("") == ""

    def test_no_sensitive_content(self) -> None:
        text = "Hello World"
        assert redact_clipboard_text(text) == text

    # -- Multi-line with mixed sensitive and safe lines --

    def test_multiline_mixed(self) -> None:
        """TC-060: Multi-line text with some safe and some sensitive lines."""
        text = "username=admin\npassword=hunter2\nemail=user@example.com"
        result = redact_clipboard_text(text)
        lines = result.split("\n")
        assert lines[0] == "username=admin"
        assert lines[1] == "[REDACTED]"
        assert lines[2] == "email=user@example.com"

    def test_multiline_all_sensitive(self) -> None:
        text = "password=abc\nsecret=xyz"
        result = redact_clipboard_text(text)
        assert result == "[REDACTED]\n[REDACTED]"

    def test_multiline_all_safe(self) -> None:
        text = "name=John\nemail=j@x.com"
        result = redact_clipboard_text(text)
        assert result == text

    # -- Config integration --

    def test_custom_placeholder(self) -> None:
        config = PrivacyConfig(redaction_placeholder="***")
        result = redact_clipboard_text("password=abc", config=config)
        assert result == "***"

    def test_silent_config_returns_original(self) -> None:
        config = PrivacyConfig(redact_passwords=False)
        text = "password=secret123"
        result = redact_clipboard_text(text, config=config)
        assert result == text

    def test_none_config_uses_default(self) -> None:
        result = redact_clipboard_text("password=abc", config=None)
        assert result == "[REDACTED]"

    # -- Idempotency --

    def test_idempotent(self) -> None:
        text = "password=secret"
        result1 = redact_clipboard_text(text)
        result2 = redact_clipboard_text(result1)
        assert result2 == result1
        assert "[REDACTED]" in result2

    # -- Whitespace around separator (still triggers full-line redaction) --

    def test_whitespace_around_equals(self) -> None:
        result = redact_clipboard_text("password = secret123")
        assert result == "[REDACTED]"

    def test_whitespace_around_colon(self) -> None:
        result = redact_clipboard_text("password : secret123")
        assert result == "[REDACTED]"


# ===========================================================================
# GW-100: redact_web_content tests
# ===========================================================================


class TestRedactWebContent:
    """Verify web-specific content redaction (GW-100).

    Line-by-line scanning for:
    - Cookie headers (Cookie:, Set-Cookie:)
    - Sensitive web patterns (credit card, API keys, SSN, tokens)
    - Standard keyword detection (password, passwd, pwd, etc.)
    """

    # -- Cookie header detection --

    def test_cookie_header_redacted(self) -> None:
        result = redact_web_content("Cookie: session_id=abc123")
        assert result == "[REDACTED]"
        assert "session_id" not in result

    def test_set_cookie_header_redacted(self) -> None:
        result = redact_web_content("Set-Cookie: auth_token=xyz789; Path=/")
        assert result == "[REDACTED]"
        assert "auth_token" not in result

    def test_cookie_with_equals(self) -> None:
        result = redact_web_content("cookie=session=value")
        assert result == "[REDACTED]"

    def test_cookie_case_insensitive(self) -> None:
        assert redact_web_content("COOKIE: abc") == "[REDACTED]"
        assert redact_web_content("set-cookie: xyz") == "[REDACTED]"

    def test_cookie_in_middle_of_text(self) -> None:
        """Cookie must be at the start of the line."""
        result = redact_web_content("some text cookie: value")
        assert "cookie" in result.lower()

    # -- Credit card patterns --

    def test_credit_card_redacted(self) -> None:
        result = redact_web_content("credit.card: 4111111111111111")
        assert result == "[REDACTED]"

    def test_cc_number_redacted(self) -> None:
        result = redact_web_content("cc.number=4111111111111111")
        assert result == "[REDACTED]"

    def test_cvv_redacted(self) -> None:
        result = redact_web_content("cvv=123")
        assert result == "[REDACTED]"

    def test_csc_redacted(self) -> None:
        result = redact_web_content("csc=456")
        assert result == "[REDACTED]"

    # -- API key / token patterns --

    def test_api_key_redacted(self) -> None:
        result = redact_web_content("api.key=sk-abc123def456")
        assert result == "[REDACTED]"
        assert "sk-abc" not in result

    def test_api_secret_redacted(self) -> None:
        result = redact_web_content("api.secret=supersecret")
        assert result == "[REDACTED]"

    def test_auth_token_redacted(self) -> None:
        result = redact_web_content("auth.token=eyJhbGciOiJIUzI1NiJ9")
        assert result == "[REDACTED]"

    def test_access_token_redacted(self) -> None:
        result = redact_web_content("access.token=abc123")
        assert result == "[REDACTED]"

    def test_refresh_token_redacted(self) -> None:
        result = redact_web_content("refresh.token=xyz789")
        assert result == "[REDACTED]"

    # -- SSN patterns --

    def test_social_security_redacted(self) -> None:
        result = redact_web_content("social.security=123-45-6789")
        assert result == "[REDACTED]"

    def test_ssn_redacted(self) -> None:
        result = redact_web_content("ssn=123-45-6789")
        assert result == "[REDACTED]"

    # -- Standard keyword patterns (password, etc.) --

    def test_password_equals_redacted(self) -> None:
        result = redact_web_content("password=secret123")
        assert result == "[REDACTED]"

    def test_secret_equals_redacted(self) -> None:
        result = redact_web_content("secret=mykey")
        assert result == "[REDACTED]"

    # -- Non-sensitive content preserved --

    def test_non_sensitive_preserved(self) -> None:
        text = "username=admin\nemail=user@example.com"
        result = redact_web_content(text)
        assert result == text

    def test_safe_line_preserved(self) -> None:
        result = redact_web_content("Hello World")
        assert result == "Hello World"

    def test_empty_string(self) -> None:
        assert redact_web_content("") == ""

    # -- Multi-line mixed content --

    def test_multiline_mixed(self) -> None:
        text = "user=admin\nCookie: session=abc\napi.key=sk-123\nemail=user@test.com"
        result = redact_web_content(text)
        lines = result.split("\n")
        assert lines[0] == "user=admin"
        assert lines[1] == "[REDACTED]"
        assert lines[2] == "[REDACTED]"
        assert lines[3] == "email=user@test.com"

    # -- Config integration --

    def test_custom_placeholder(self) -> None:
        config = PrivacyConfig(redaction_placeholder="***")
        result = redact_web_content("Cookie: abc", config=config)
        assert result == "***"

    def test_silent_config_returns_original(self) -> None:
        config = PrivacyConfig(redact_passwords=False)
        text = "Cookie: session=abc\napi.key=sk-123"
        result = redact_web_content(text, config=config)
        assert result == text

    def test_none_config_uses_default(self) -> None:
        result = redact_web_content("Cookie: abc", config=None)
        assert result == "[REDACTED]"

    # -- Case insensitivity --

    def test_case_insensitive_api_key(self) -> None:
        assert redact_web_content("API.KEY=sk-123") == "[REDACTED]"
        assert redact_web_content("Api.Key=sk-123") == "[REDACTED]"

    def test_case_insensitive_credit_card(self) -> None:
        assert redact_web_content("CREDIT.CARD=4111") == "[REDACTED]"

    # -- Idempotency --

    def test_idempotent(self) -> None:
        text = "Cookie: abc\napi.key=sk-123"
        result1 = redact_web_content(text)
        result2 = redact_web_content(result1)
        assert result2 == result1

    # -- Whitespace tolerance --

    def test_whitespace_around_separator(self) -> None:
        result = redact_web_content("api.key = sk-123")
        assert result == "[REDACTED]"

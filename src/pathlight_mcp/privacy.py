"""Privacy controls for Pathlight MCP responses (PRD R13, GW-100).

Prevents sensitive data from leaking into MCP responses by:

- Detecting password and sensitive input fields via heuristic patterns
- Redacting element values on :class:`~pathlight_mcp.models.NormalizedElement` instances
- Filtering out denylisted applications from snapshot trees
- Redacting sensitive content in clipboard text via line-by-line keyword scanning
- Web-specific privacy redactions for cookies, password fields, and sensitive
  form data (GW-100)

Public API::

    from pathlight_mcp.privacy import (
        PrivacyConfig,
        is_password_field,
        redact_clipboard_text,
        redact_element,
        redact_snapshot,
        redact_web_content,
    )
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, replace

from pathlight_mcp.models import NormalizedElement

__all__ = [
    "PrivacyConfig",
    "is_password_field",
    "redact_clipboard_text",
    "redact_element",
    "redact_snapshot",
    "redact_web_content",
    "should_allow_screenshot",
]


# ---------------------------------------------------------------------------
# Constants (private — not part of public API per architecture §3.6)
# ---------------------------------------------------------------------------

# Roles that indicate a password/sensitive input field.
_DEFAULT_PASSWORD_ROLES: frozenset[str] = frozenset(
    {
        "password",
        "password_edit",
        "edit_password",
    }
)

# Case-insensitive substrings in element names that signal a password field.
# Approved 6 patterns per architecture §3.3.
_DEFAULT_PASSWORD_NAME_PATTERNS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "credential",
    "pin",
)

# Default denylist is empty — callers add apps explicitly (architecture §3.5).
_DEFAULT_DENYLIST: frozenset[str] = frozenset()

_DEFAULT_REDACTION_PLACEHOLDER = "[REDACTED]"


# ---------------------------------------------------------------------------
# PrivacyConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrivacyConfig:
    """Immutable privacy configuration.

    Attributes:
        denylist_apps: Application names (case-insensitive) whose windows
            are replaced with stub elements at the snapshot level.
        redaction_placeholder: Replacement string for redacted values.
        redact_passwords: Master toggle — when ``False``, a deep copy is
            returned but no redaction occurs.
    """

    denylist_apps: frozenset[str] = _DEFAULT_DENYLIST
    redaction_placeholder: str = _DEFAULT_REDACTION_PLACEHOLDER
    redact_passwords: bool = True


# ---------------------------------------------------------------------------
# is_password_field
# ---------------------------------------------------------------------------


def is_password_field(element: NormalizedElement) -> bool:
    """Check whether an element is a password or sensitive input field.

    Detection uses two strategies on elements with ``role="text_input"`` only
    (architecture §3.3):

    1. **Name match**: The element's name (case-insensitive) contains any
       substring from the built-in 6-pattern list.
    2. **State match**: The element's ``states.is_password`` is ``True``.

    Args:
        element: A :class:`~pathlight_mcp.models.NormalizedElement` instance.

    Returns:
        ``True`` if the element should be treated as a sensitive field.
    """
    # Only text_input elements are candidates for password detection (§3.3)
    if element.role != "text_input":
        return False

    # State-based check
    is_pw = getattr(element.states, "is_password", None)
    if is_pw is True:
        return True

    # Name-based check (only for text_input role)
    name = element.name
    if name:
        lower_name = name.lower()
        return any(p in lower_name for p in _DEFAULT_PASSWORD_NAME_PATTERNS)

    return False


# ---------------------------------------------------------------------------
# redact_element
# ---------------------------------------------------------------------------


def redact_element(
    element: NormalizedElement,
    *,
    redact_value: bool = True,
    redact_text: bool = True,
    redact_name: bool = False,
    redact_description: bool = False,
    redaction_placeholder: str | None = None,
) -> NormalizedElement:
    """Redact sensitive values on a single element, returning a new copy.

    Per-field keyword params control which fields are redacted. Only elements
    identified as password fields (via :func:`is_password_field`) are affected.

    Args:
        element: A :class:`~pathlight_mcp.models.NormalizedElement` instance.
        redact_value: Whether to redact the ``value`` field.
        redact_text: Whether to redact the ``text`` field.
        redact_name: Whether to redact the ``name`` field.
        redact_description: Whether to redact the ``description`` field.
        redaction_placeholder: Override replacement string. Defaults to
            ``"[REDACTED]"``.

    Returns:
        A new :class:`~pathlight_mcp.models.NormalizedElement` with redacted
        values, or the original element if not sensitive.
    """
    if not is_password_field(element):
        return element

    placeholder = redaction_placeholder or _DEFAULT_REDACTION_PLACEHOLDER

    changes: dict = {}
    if redact_value and element.value is not None:
        changes["value"] = placeholder
    if redact_text and element.text is not None:
        changes["text"] = placeholder
    if redact_name and element.name is not None:
        changes["name"] = placeholder
    if redact_description and element.description is not None:
        changes["description"] = placeholder
    return replace(element, **changes)


# ---------------------------------------------------------------------------
# redact_snapshot
# ---------------------------------------------------------------------------

# Stub element used to replace denylisted application windows at snapshot level.
_STUB_PANE = NormalizedElement(
    ref="",
    backend_id="",
    role="pane",
    name="[APP DENYLISTED]",
)


def redact_snapshot(
    elements: list[NormalizedElement],
    app_name: str | None = None,
    config: PrivacyConfig | None = None,
) -> list[NormalizedElement]:
    """Redact sensitive values throughout a list of element trees.

    Walks each tree recursively:

    - Password/sensitive fields have their ``value`` and ``text`` replaced.
    - Top-level children whose ``role`` is ``"window"`` and whose ``name``
      matches a denylisted application are replaced with a stub element.

    The original trees are **not** mutated; new copies are created only for
    elements that need redaction. When ``config.redact_passwords`` is ``False``,
    a deep copy is returned without any redaction applied.

    Args:
        elements: A list of root :class:`~pathlight_mcp.models.NormalizedElement`
            instances representing the snapshot trees.
        app_name: Application name to check against the denylist (case-
            insensitive). When provided, top-level window children whose
            name matches are replaced with stubs.
        config: Privacy configuration. Defaults to a fresh
            :class:`PrivacyConfig`.

    Returns:
        A new list with sensitive values redacted and denylisted windows
        replaced by stubs.
    """
    if config is None:
        config = PrivacyConfig()

    if not config.redact_passwords:
        # Return deep copies even when redaction is disabled (F3)
        return [copy.deepcopy(el) for el in elements]

    return [_redact_tree(el, config, app_name, is_root=True) for el in elements]


# ---------------------------------------------------------------------------
# redact_clipboard_text
# ---------------------------------------------------------------------------

# Pre-compiled regex for line-by-line keyword detection (case-insensitive).
# Any line containing one of the 6 sensitive keywords is fully replaced.
_CLIPBOARD_KEYWORD_RE = re.compile(
    r"(password|passwd|pwd|secret|credential|pin)",
    re.IGNORECASE,
)


def redact_clipboard_text(
    text: str,
    *,
    config: PrivacyConfig | None = None,
) -> str:
    """Redact sensitive content in clipboard text.

    Performs line-by-line scanning: any line that contains one of the 6
    sensitive keywords (``password``, ``passwd``, ``pwd``, ``secret``,
    ``credential``, ``pin``) is **fully replaced** with the configured
    redaction placeholder. Non-sensitive lines are left intact.

    Uses the same 6 keyword patterns as :func:`is_password_field`.

    Args:
        text: Clipboard text content to redact.
        config: Privacy configuration. When ``None``, defaults to a fresh
            :class:`PrivacyConfig`. When ``config.redact_passwords`` is
            ``False``, the original text is returned unchanged.

    Returns:
        The text with sensitive lines replaced by the redaction placeholder.
    """
    if config is None:
        config = PrivacyConfig()

    if not config.redact_passwords:
        return text

    placeholder = config.redaction_placeholder

    lines = text.split("\n")
    result_lines = [placeholder if _CLIPBOARD_KEYWORD_RE.search(line) else line for line in lines]
    return "\n".join(result_lines)


def _is_denylisted(
    name: str,
    config: PrivacyConfig,
    app_name: str | None,
) -> bool:
    """Check whether an application name is on the denylist."""
    denylist_lower = {a.lower() for a in config.denylist_apps}
    return bool(
        (app_name and app_name.lower() in denylist_lower)
        or (name and name.lower() in denylist_lower)
    )


def _redact_tree(
    element: NormalizedElement,
    config: PrivacyConfig,
    app_name: str | None,
    is_root: bool = True,
) -> NormalizedElement:
    """Recursively redact an element and its children."""
    # Check denylist for top-level window children
    if (
        not is_root
        and element.role == "window"
        and element.name
        and _is_denylisted(element.name, config, app_name)
    ):
        return replace(
            _STUB_PANE,
            ref=element.ref,
            backend_id=element.backend_id,
        )

    # Check if this element is a password field
    sensitive = is_password_field(element)

    changes: dict = {}
    if sensitive:
        if element.value is not None:
            changes["value"] = config.redaction_placeholder
        if element.text is not None:
            changes["text"] = config.redaction_placeholder

    # Recurse into children
    children = element.children
    if children:
        new_children = [_redact_tree(child, config, app_name, is_root=False) for child in children]
        changes["children"] = new_children

    return replace(element, **changes) if changes else element


# ---------------------------------------------------------------------------
# Web-specific privacy redactions (GW-100)
# ---------------------------------------------------------------------------

# HTML input types that always indicate sensitive data.
_WEB_SENSITIVE_INPUT_TYPES: frozenset[str] = frozenset(
    {
        "password",
        "hidden",
    }
)

# HTML autocomplete attribute values that indicate sensitive form data.
_WEB_SENSITIVE_AUTOCOMPLETE: frozenset[str] = frozenset(
    {
        "current-password",
        "new-password",
        "cc-number",
        "cc-exp",
        "cc-csc",
        "cc-type",
        "cc-name",
        "cc-given-name",
        "cc-additional-name",
        "cc-family-name",
        "cc-exp-month",
        "cc-exp-year",
        "transaction-amount",
        "transaction-currency",
    }
)

# Cookie header name patterns (case-insensitive).
_WEB_COOKIE_HEADER_PATTERNS: tuple[str, ...] = (
    "cookie",
    "set-cookie",
)

# Pre-compiled regex for cookie header detection.
_COOKIE_HEADER_RE = re.compile(
    r"^(cookie|set-cookie)\s*[:=]",
    re.IGNORECASE,
)

# Pre-compiled regex for sensitive web form patterns in text content.
_WEB_SENSITIVE_CONTENT_RE = re.compile(
    r"(password|passwd|pwd|secret|credential|pin|"
    r"credit.card|cc.number|cvv|csc|"
    r"social.security|ssn|"
    r"api.key|api.secret|auth.token|access.token|refresh.token)"
    r"\s*[:=]",
    re.IGNORECASE,
)

# Web-specific password role names from AX tree (extends the desktop set).
_WEB_PASSWORD_ROLES: frozenset[str] = frozenset(
    {
        "password",
        "password_field",
        "password_input",
        "credential_field",
    }
)


def _is_web_password_element(element: NormalizedElement) -> bool:
    """Detect web-specific password/sensitive fields.

    Extends :func:`is_password_field` with web-specific heuristics:
    - Known web password roles (password, password_field, password_input)
    - State-based ``is_password`` flag (any role)
    - Name-based detection for web form field names

    Args:
        element: A :class:`~pathlight_mcp.models.NormalizedElement` instance.

    Returns:
        ``True`` if the element should be treated as a web sensitive field.
    """
    # Role-based check — web-specific password roles
    if element.role in _WEB_PASSWORD_ROLES:
        return True

    # State-based check (any role)
    is_pw = getattr(element.states, "is_password", None)
    if is_pw is True:
        return True

    # Name-based check on text_input elements (reuse desktop patterns)
    if element.role in ("text_input", "textbox", "search_input"):
        name = element.name
        if name:
            lower_name = name.lower()
            return any(p in lower_name for p in _DEFAULT_PASSWORD_NAME_PATTERNS)

    return False


def redact_web_content(
    text: str,
    *,
    config: PrivacyConfig | None = None,
) -> str:
    """Redact sensitive web content from text.

    Performs web-specific redactions on top of the base clipboard redaction:

    - Cookie header lines (``Cookie: ...``, ``Set-Cookie: ...``)
    - Sensitive form data patterns (password, credit card, API key, SSN, tokens)
    - Falls back to :func:`redact_clipboard_text` for standard keyword matching

    Args:
        text: Text content potentially containing web-sensitive data.
        config: Privacy configuration. When ``None``, defaults to a fresh
            :class:`PrivacyConfig`. When ``config.redact_passwords`` is
            ``False``, the original text is returned unchanged.

    Returns:
        The text with sensitive web content replaced by the redaction placeholder.
    """
    if config is None:
        config = PrivacyConfig()

    if not config.redact_passwords:
        return text

    placeholder = config.redaction_placeholder
    lines = text.split("\n")
    result_lines: list[str] = []

    for line in lines:
        # Cookie header detection
        if _COOKIE_HEADER_RE.match(line):
            result_lines.append(placeholder)
            continue
        # Sensitive web content pattern detection
        if _WEB_SENSITIVE_CONTENT_RE.search(line):
            result_lines.append(placeholder)
            continue
        # Standard keyword detection (password, passwd, pwd, etc.)
        if _CLIPBOARD_KEYWORD_RE.search(line):
            result_lines.append(placeholder)
            continue
        result_lines.append(line)

    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# Screenshot privacy gate (GW-152)
# ---------------------------------------------------------------------------


def should_allow_screenshot(
    *,
    app_name: str | None = None,
    config: PrivacyConfig | None = None,
) -> bool:
    """Check whether a screenshot capture should be permitted.

    The privacy gate denies screenshots when the application name matches
    an entry on the denylist (case-insensitive).

    Args:
        app_name: Application name (e.g. ``"notepad.exe"``).
        config: Privacy configuration. When ``None``, defaults to a fresh
            :class:`PrivacyConfig`.

    Returns:
        ``True`` when the screenshot is permitted, ``False`` otherwise.
    """
    if config is None:
        config = PrivacyConfig()

    denylist_lower = {a.lower() for a in config.denylist_apps}

    return not (app_name and app_name.lower() in denylist_lower)
